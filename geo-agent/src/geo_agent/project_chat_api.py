from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_api import OperatorPrincipal
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.project_chat import ProjectChatRequest, ProjectChatService


def _conversation_view(conversation) -> dict:
    return conversation.model_dump(mode="json", exclude={"owner"})


def create_project_chat_router(
    service: ProjectChatService,
    policy: MeasurementExecutionPolicy | None,
    authenticate: Callable[..., OperatorPrincipal],
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
            _conversation_view(conversation)
            for conversation in service.store.list(project_id, owner)
        ]

    @router.post("/projects/{project_id}/conversations", status_code=201)
    def create_conversation(
        project_id: str,
        run_id: str | None = None,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _conversation_view(service.store.create(project_id, owner, run_id))

    @router.get("/projects/{project_id}/conversations/{conversation_id}")
    def get_conversation(
        project_id: str,
        conversation_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _conversation_view(service.store.get(project_id, conversation_id, owner))

    @router.post("/projects/{project_id}/conversations/{conversation_id}/messages")
    async def send_message(
        project_id: str,
        conversation_id: str,
        body: ProjectChatRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        conversation = await service.respond(
            project_id,
            conversation_id,
            owner,
            body,
        )
        return _conversation_view(conversation)

    return router
