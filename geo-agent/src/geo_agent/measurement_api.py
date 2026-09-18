from collections.abc import Callable
from hashlib import sha256
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import Field

from geo_agent.artifact_storage import ArtifactRepository, ArtifactService, ArtifactStorage, MeasurementArtifact
from geo_agent.artifacts import render_assessment_bundle, render_content_strategy_bundle
from geo_agent.contracts import Brief, Contract, MeasurementInputs, QueryPair, QueryPlan
from geo_agent.evaluation_workflow import EvaluationRequest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.evaluation import measurement_scores
from geo_agent.evidence_assessment import BrandDefinition, build_evidence_assessment
from geo_agent.jobs import JobRepository, JobService, JobType, WorkflowJob
from geo_agent.agent_access import ExecutionStage
from geo_agent.measurement_service import MeasurementApplicationService
from geo_agent.measurement_views import (
    CursorCodec,
    artifact_view as _artifact_view,
    job_view as _job_view,
    run_view as _run_view,
)
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    RecommendationDecision,
    MeasurementRepository,
    MeasurementRun,
    OwnerIdentity,
)
from geo_agent.preparation import PreparationRequest
from geo_agent.recommendations import build_content_strategy
from geo_agent.workflow import Conflict, NotFound


class OperatorPrincipal(Contract):
    tenant_id: str = Field(min_length=1, max_length=200)
    object_id: str = Field(min_length=1, max_length=200)
    roles: tuple[str, ...] = ()

    @property
    def owner(self) -> OwnerIdentity:
        return OwnerIdentity(tenant_id=self.tenant_id, object_id=self.object_id)


class CreateMeasurementBriefRequest(Brief):
    brand_definition: BrandDefinition | None = None


class SaveBrandDefinitionRequest(Contract):
    definition: BrandDefinition
    expected_definition_version: int = Field(ge=0)


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=1)


class QueueRequest(RevisionRequest):
    idempotency_key: str = Field(min_length=1, max_length=200)


class ExportRequest(RevisionRequest):
    definition_version: int | None = Field(default=None, ge=1)
    definition_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class PrepareRunRequest(QueueRequest):
    confirm_preparation_calls: bool


class ApproveQueriesRequest(RevisionRequest):
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviseQueriesRequest(RevisionRequest):
    queries: tuple[QueryPair, ...] = Field(min_length=5, max_length=5)


class StartEvaluationRequest(QueueRequest):
    confirm_evaluation_calls: bool
    include_recommendations: bool = False


class ReviewRecommendationsRequest(RevisionRequest):
    decisions: tuple[RecommendationDecision, ...] = Field(min_length=1, max_length=3)


class AgentExecutionAuthorizationRequest(RevisionRequest):
    agent_principal_id: str = Field(min_length=1, max_length=200)
    stage: ExecutionStage
    input_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    lifetime_seconds: int = Field(default=900, ge=60, le=86400)


def create_measurement_router(
    repository: MeasurementRepository | JobRepository | ArtifactRepository,
    policy: MeasurementExecutionPolicy,
    authenticate: Callable[..., OperatorPrincipal],
    artifact_storage: ArtifactStorage,
    run_pending_jobs: Callable[[], None] | None = None,
    *,
    service: MeasurementApplicationService | None = None,
    default_agent_principal_id: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["measurement-v2"])
    coordinator = MeasurementCoordinator(repository)
    jobs = JobService(repository)
    artifact_service = ArtifactService(repository, artifact_storage)
    application = service or MeasurementApplicationService(
        repository,
        policy,
        artifact_service,
        CursorCodec(secrets.token_bytes(32)),
    )

    def require_operator(
        principal: Annotated[OperatorPrincipal, Depends(authenticate)],
    ) -> OwnerIdentity:
        if policy.owner_role not in principal.roles:
            raise HTTPException(403, "Geo.Operator role required")
        return principal.owner

    owner_dependency = Depends(require_operator)

    @router.get("/policy")
    def get_policy(owner: OwnerIdentity = owner_dependency) -> dict:
        return {
            "policy_id": policy.policy_id,
            "execution_mode": policy.execution_mode,
            "allowed_domains": policy.allowed_domains,
            "locale": policy.locale,
            "profiles": tuple({
                "profile_id": profile.profile_id,
                "provider": profile.provider,
                "prompt_version": profile.prompt_version,
            } for profile in policy.profiles),
            "limits": {
                "preparation_calls": policy.max_browse_calls + policy.max_page_analysis_calls + policy.max_query_plan_calls,
                "search_calls": policy.max_search_calls,
                "evaluator_calls": policy.max_evaluator_calls_per_profile * len(policy.profiles),
                "recommendation_calls": policy.max_recommendation_calls,
                "automatic_retries": policy.automatic_retries,
            },
            "agent_execution": {
                "default_principal_id": default_agent_principal_id,
                "human_authorization_required": True,
            },
        }

    @router.post("/briefs", status_code=201)
    def create_brief(body: CreateMeasurementBriefRequest, owner: OwnerIdentity = owner_dependency) -> dict:
        brief = Brief.model_validate(body.model_dump(exclude={"brand_definition"}))
        return _run_view(application.create_human_run(
            owner,
            brief=brief,
            brand_definition=body.brand_definition,
        ))

    @router.post("/runs/{run_id}/brand-definition")
    def save_brand_definition(run_id: str, body: SaveBrandDefinitionRequest,
                              response: Response, owner: OwnerIdentity = owner_dependency) -> dict:
        record = repository.save_brand_definition(run_id, owner, body.definition, body.expected_definition_version)
        response.headers["Cache-Control"] = "private, no-store"
        return {**record.model_dump(mode="json"), "definition_hash": record.definition_hash}

    @router.get("/runs/{run_id}/evidence-assessment")
    def get_evidence_assessment(run_id: str, response: Response,
                                definition_version: int | None = Query(default=None, ge=1),
                                owner: OwnerIdentity = owner_dependency) -> dict:
        run = repository.get(run_id, owner)
        record = repository.get_brand_definition(run_id, owner, definition_version)
        response.headers["Cache-Control"] = "private, no-store"
        report = build_evidence_assessment(run.measurement, record, run_id=run_id, run_revision=run.revision) \
            if run.measurement else None
        status = ("unconfigured" if record is None else "ready" if report else
                  "unavailable-no-saved-measurement" if run.state in {"failed", "cancelled", "needs-review"} else
                  "pending-saved-measurement")
        return {"run_id": run_id, "run_revision": run.revision, "status": status,
                "brand_definition": {**record.model_dump(mode="json"), "definition_hash": record.definition_hash} if record else None,
                "assessment": report.model_dump(mode="json") if report else None,
                "assessment_hash": report.assessment_hash if report else None}

    @router.get("/runs/{run_id}/evidence-assessment/download")
    def download_assessment(run_id: str, definition_version: int = Query(ge=1),
                            measurement_hash: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
                            owner: OwnerIdentity = owner_dependency) -> Response:
        run = repository.get(run_id, owner)
        record = repository.get_brand_definition(run_id, owner, definition_version)
        if run.measurement is None:
            raise Conflict("No saved measurement is available for assessment")
        report = build_evidence_assessment(run.measurement, record, run_id=run_id, run_revision=run.revision)
        if measurement_hash is not None and report.measurement_hash != measurement_hash:
            raise Conflict("Saved measurement changed; reload the assessment")
        content = render_assessment_bundle(run.measurement, report)
        return Response(content, media_type="application/zip", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="geo-assessment-{run.run_id}-v{definition_version}.zip"',
            "ETag": f'"{sha256(content).hexdigest()}"',
        })

    @router.get("/runs")
    def list_runs(owner: OwnerIdentity = owner_dependency) -> list[dict]:
        return [_run_view(run) for run in repository.list_runs(owner)]

    @router.get("/runs/{run_id}/content-strategy")
    def get_content_strategy(run_id: str, response: Response, owner: OwnerIdentity = owner_dependency) -> dict:
        run = repository.get(run_id, owner)
        report = build_content_strategy(run.measurement) if run.measurement else None
        response.headers["Cache-Control"] = "private, no-store"
        return {"run_id": run_id, "run_revision": run.revision,
                "status": "ready" if report else "pending-saved-measurement",
                "report": report.model_dump(mode="json") if report else None}

    @router.get("/runs/{run_id}/content-strategy/download")
    def download_content_strategy(run_id: str,
                                  measurement_hash: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
                                  owner: OwnerIdentity = owner_dependency) -> Response:
        run = repository.get(run_id, owner)
        if run.measurement is None:
            raise Conflict("No saved measurement is available for content recommendations")
        report = build_content_strategy(run.measurement)
        if measurement_hash is not None and report.measurement_hash != measurement_hash:
            raise Conflict("Saved measurement changed; reload the content recommendations")
        content = render_content_strategy_bundle(run.measurement)
        return Response(content, media_type="application/zip", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="geo-content-strategy-{run.run_id}.zip"',
            "ETag": f'"{sha256(content).hexdigest()}"',
        })

    @router.post("/runs/{run_id}/prepare", status_code=202)
    def prepare_run(
        run_id: str,
        body: PrepareRunRequest,
        background_tasks: BackgroundTasks,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        if body.confirm_preparation_calls is not True:
            raise Conflict("Preparation provider calls require explicit confirmation")
        job, updated = application.prepare_human_run(
            owner,
            run_id=run_id,
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
        )
        if run_pending_jobs is not None:
            background_tasks.add_task(run_pending_jobs)
        return {"job": _job_view(job), "run": _run_view(updated)}

    @router.post("/runs/{run_id}/query-approval")
    def approve_queries(
        run_id: str,
        body: ApproveQueriesRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _run_view(coordinator.approve(
            run_id,
            owner,
            body.expected_revision,
            body.input_hash,
        ))

    @router.put("/runs/{run_id}/queries")
    def revise_queries(
        run_id: str,
        body: ReviseQueriesRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = repository.get(run_id, owner)
        if run.inputs is None:
            raise Conflict("Query revision requires prepared measurement inputs")
        try:
            query_plan = QueryPlan(queries=body.queries)
            inputs = MeasurementInputs.model_validate({
                **run.inputs.model_dump(mode="json"),
                "query_plan": query_plan.model_dump(mode="json"),
            })
        except ValueError:
            raise HTTPException(422, "Queries must be unique and retain supported page evidence") from None
        return _run_view(coordinator.revise_inputs(
            run_id,
            owner,
            body.expected_revision,
            inputs,
        ))

    @router.post("/runs/{run_id}/start", status_code=202)
    def start_evaluation(
        run_id: str,
        body: StartEvaluationRequest,
        background_tasks: BackgroundTasks,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        if body.confirm_evaluation_calls is not True:
            raise Conflict("Evaluation provider calls require explicit confirmation")
        job, updated = application.start_human_measurement(
            owner,
            run_id=run_id,
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
            include_recommendations=body.include_recommendations,
        )
        if run_pending_jobs is not None:
            background_tasks.add_task(run_pending_jobs)
        return {"job": _job_view(job), "run": _run_view(updated)}

    @router.post("/runs/{run_id}/recommendation-review")
    def review_recommendations(
        run_id: str,
        body: ReviewRecommendationsRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _run_view(coordinator.review_recommendations(
            run_id,
            owner,
            body.expected_revision,
            body.decisions,
        ))

    @router.post("/runs/{run_id}/agent-execution-authorizations", status_code=201)
    def authorize_agent_execution(
        run_id: str,
        body: AgentExecutionAuthorizationRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        record = application.issue_execution_authorization(
            owner=owner,
            run_id=run_id,
            principal_id=body.agent_principal_id,
            stage=body.stage,
            expected_revision=body.expected_revision,
            input_hash=body.input_hash,
            lifetime_seconds=body.lifetime_seconds,
        )
        return record.model_dump(
            mode="json",
            exclude={"owner", "consumed_at", "consumed_by_job_id"},
        )

    @router.get("/runs/{run_id}")
    def get_run(run_id: str, owner: OwnerIdentity = owner_dependency) -> dict:
        return _run_view(repository.get(run_id, owner))

    @router.get("/runs/{run_id}/events")
    def get_events(run_id: str, owner: OwnerIdentity = owner_dependency) -> dict:
        run = repository.get(run_id, owner)
        return {
            "run_id": run.run_id,
            "revision": run.revision,
            "events": [event.model_dump(mode="json") for event in run.events],
        }

    @router.get("/runs/{run_id}/evidence/{evidence_id}")
    def get_evidence(run_id: str, evidence_id: str, owner: OwnerIdentity = owner_dependency) -> dict:
        run = repository.get(run_id, owner)
        if run.measurement is not None:
            for retrieval in run.measurement.retrievals:
                for source in retrieval.sources:
                    if source.evidence_id == evidence_id:
                        return source.model_dump(mode="json")
        raise NotFound("Evidence not found")

    @router.get("/runs/{run_id}/queries/{query_id}/evidence/{evidence_id}")
    def get_query_evidence(
        run_id: str,
        query_id: str,
        evidence_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = repository.get(run_id, owner)
        if run.measurement is not None:
            for retrieval in run.measurement.retrievals:
                if retrieval.query_id != query_id:
                    continue
                for source in retrieval.sources:
                    if source.evidence_id == evidence_id:
                        return source.model_dump(mode="json")
        raise NotFound("Evidence not found")

    @router.post("/runs/{run_id}/exports", status_code=201)
    def export_run(run_id: str, body: ExportRequest, owner: OwnerIdentity = owner_dependency) -> dict:
        artifact, run = artifact_service.export(run_id, owner, body.expected_revision,
                                                 body.definition_version, body.definition_hash)
        return {
            "artifact": _artifact_view(artifact),
            "run": _run_view(run),
            "download_url": f"/api/v2/runs/{run.run_id}/artifacts/{artifact.artifact_id}",
        }

    @router.get("/runs/{run_id}/artifacts")
    def list_artifacts(run_id: str, owner: OwnerIdentity = owner_dependency) -> list[dict]:
        repository.get(run_id, owner)
        return [_artifact_view(item) for item in repository.list_artifacts(run_id, owner)]

    @router.get("/runs/{run_id}/artifacts/{artifact_id}")
    def download_artifact(
        run_id: str,
        artifact_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> Response:
        artifact, content = artifact_service.download(run_id, artifact_id, owner)
        return Response(content, media_type=artifact.media_type, headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="geo-{run_id}.zip"',
            "ETag": f'"{artifact.content_hash}"',
            "X-Content-Type-Options": "nosniff",
        })

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, owner: OwnerIdentity = owner_dependency) -> dict:
        return _job_view(repository.get_job(job_id, owner))

    @router.get("/runs/{run_id}/jobs")
    def list_run_jobs(run_id: str, owner: OwnerIdentity = owner_dependency) -> list[dict]:
        return [_job_view(job) for job in repository.list_run_jobs(run_id, owner)]

    @router.get("/runs/{run_id}/progress")
    def get_run_progress(run_id: str, owner: OwnerIdentity = owner_dependency) -> dict:
        return repository.get_run_progress(run_id, owner).model_dump(mode="json")

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, owner: OwnerIdentity = owner_dependency) -> dict:
        job, run = jobs.cancel(job_id, owner)
        return {"job": _job_view(job), "run": _run_view(run)}

    return router