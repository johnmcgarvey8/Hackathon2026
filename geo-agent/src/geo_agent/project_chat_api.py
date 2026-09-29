from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response

from geo_agent.evidence_assessment import build_evidence_assessment
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_api import OperatorPrincipal
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.project_chat import ProjectChatRequest, ProjectChatService
from geo_agent.workflow import NotFound


def _legacy_evidence_type(source_id: str, title: str) -> str | None:
    if "-query-" in source_id or title.startswith(("Approved query ", "Grounding query ")):
        return "grounding-query"
    if "-evidence-" in source_id:
        return "grounding-citation"
    if "-answer-" in source_id:
        return "test-answer"
    return "measurement-run"


def _legacy_query_id(source_id: str) -> str | None:
    if "-query-" in source_id:
        return source_id.rsplit("-query-", 1)[-1]
    if "-evidence-" in source_id:
        evidence_id = source_id.rsplit("-evidence-", 1)[-1]
        parts = evidence_id.split("-")
        if len(parts) >= 2 and parts[0] == "q" and parts[1].isdigit():
            return "-".join(parts[:2])
    return None


def _conversation_view(conversation, service: ProjectChatService) -> dict:
    view = conversation.model_dump(mode="json", exclude={"owner"})
    run_annotations: dict[str, dict] = {}
    run_ids = tuple(dict.fromkeys((
        *conversation.linked_run_ids,
        *((conversation.run_id,) if conversation.run_id is not None else ()),
    )))
    for run_id in run_ids:
        try:
            run = service.repository.get_project_run(
                conversation.project_id,
                run_id,
                conversation.owner,
            )
        except NotFound:
            continue
        if run is not None and run.measurement is not None:
            record = service.repository.get_brand_definition(
                run.run_id,
                conversation.owner,
            )
            if record is not None:
                assessment = build_evidence_assessment(
                    run.measurement,
                    record,
                    run_id=run.run_id,
                    run_revision=run.revision,
                )
                run_annotations[run_id] = {
                    "brand_name": record.definition.name,
                    "query_status": {
                        item["query_id"]: item["brand_status"]
                        for item in assessment.queries
                    },
                    "source_status": {
                        source["evidence_id"]: source["brand"]["status"]
                        for item in assessment.queries
                        for source in item["sources"]
                    },
                }
    for turn in view["turns"]:
        for citation in turn["citations"]:
            if citation["source_class"] != "geo-evidence":
                continue
            evidence_type = citation.get("geo_evidence_type") or _legacy_evidence_type(
                citation["source_id"],
                citation["title"],
            )
            citation["geo_evidence_type"] = evidence_type
            query_id = citation.get("query_id") or _legacy_query_id(citation["source_id"])
            citation["query_id"] = query_id
            citation_run_id = next((
                run_id for run_id in run_ids
                if (
                    citation["source_id"] == run_id
                    or citation["source_id"].startswith(f"{run_id}-")
                )
            ), conversation.run_id)
            annotation = run_annotations.get(citation_run_id or "", {})
            if evidence_type == "grounding-query":
                citation["title"] = citation["title"].replace(
                    "Approved query ",
                    "Grounding query ",
                    1,
                )
                citation["brand_status"] = annotation.get(
                    "query_status",
                    {},
                ).get(query_id, citation.get("brand_status"))
            elif evidence_type == "grounding-citation":
                evidence_id = citation["source_id"].rsplit("-evidence-", 1)[-1]
                citation["brand_status"] = annotation.get(
                    "source_status",
                    {},
                ).get(evidence_id, citation.get("brand_status"))
            if citation.get("brand_status") is not None:
                citation["brand_name"] = annotation.get(
                    "brand_name",
                    citation.get("brand_name"),
                )
    return view


def create_project_chat_router(
    service: ProjectChatService,
    policy: MeasurementExecutionPolicy | None,
    authenticate: Callable[..., OperatorPrincipal],
    run_pending_jobs: Callable[[], None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["project-chat"])

    def require_operator(
        principal: Annotated[OperatorPrincipal, Depends(authenticate)],
    ) -> OwnerIdentity:
        if "Geo.Operator" not in principal.roles:
            raise HTTPException(403, "Geo.Operator role required")
        return principal.owner

    owner_dependency = Depends(require_operator)

    @router.get("/projects/{project_id}/chat-status")
    def chat_status(
        project_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        project = service.repository.get_project(project_id, owner)
        return service.agent.status(project)

    @router.get("/projects/{project_id}/conversations")
    def list_conversations(
        project_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> list[dict]:
        return [
            _conversation_view(conversation, service)
            for conversation in service.store.list(project_id, owner)
        ]

    @router.post("/projects/{project_id}/conversations", status_code=201)
    def create_conversation(
        project_id: str,
        run_id: str | None = None,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _conversation_view(service.store.create(project_id, owner, run_id), service)

    @router.get("/projects/{project_id}/conversations/{conversation_id}")
    def get_conversation(
        project_id: str,
        conversation_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _conversation_view(service.store.get(project_id, conversation_id, owner), service)

    @router.delete(
        "/projects/{project_id}/conversations/{conversation_id}",
        status_code=204,
    )
    def delete_conversation(
        project_id: str,
        conversation_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> Response:
        service.store.delete(project_id, conversation_id, owner)
        return Response(status_code=204)

    @router.post("/projects/{project_id}/conversations/{conversation_id}/messages")
    async def send_message(
        project_id: str,
        conversation_id: str,
        body: ProjectChatRequest,
        background_tasks: BackgroundTasks,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        conversation = await service.respond(
            project_id,
            conversation_id,
            owner,
            body,
        )
        if run_pending_jobs is not None:
            background_tasks.add_task(run_pending_jobs)
        return _conversation_view(conversation, service)

    return router
