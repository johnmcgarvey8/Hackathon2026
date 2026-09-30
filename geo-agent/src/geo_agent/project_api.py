from collections.abc import Callable
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import Field, ValidationError

from geo_agent.artifact_storage import ArtifactService, ArtifactStorage, MeasurementArtifact
from geo_agent.artifacts import render_assessment_bundle, render_content_strategy_bundle
from geo_agent.contracts import Brief, Contract, MeasurementInputs, Provenance, QueryPlan, identifier
from geo_agent.evaluation_workflow import EvaluationRequest, EvaluatorRecoveryRequest
from geo_agent.evidence_assessment import BrandDefinition, build_evidence_assessment
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.goal_summary import (
    GoalSummaryInput,
    GoalSummaryRequest,
    ProjectGoalSummaryService,
)
from geo_agent.jobs import JobService, JobType
from geo_agent.measurement_api import (
    ApproveQueriesRequest,
    ExportRequest,
    OperatorPrincipal,
    PrepareRunRequest,
    ReviewRecommendationsRequest,
    RecoverEvaluatorsRequest,
    RevisionRequest,
    ReviseQueriesRequest,
    SaveBrandDefinitionRequest,
    StartEvaluationRequest,
)
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementRun,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.preparation import PreparationRequest
from geo_agent.projects import (
    Project,
    ProjectCreate,
    ProjectMeasurementRepository,
    ProjectUpdate,
    host_in_domains,
)
from geo_agent.project_measurements import ProjectMeasurementOrchestrator
from geo_agent.recommendations import build_content_strategy
from geo_agent.run_view import build_run_view, job_view
from geo_agent.workflow import Conflict, NotFound


class ProjectBriefRequest(Brief):
    pass


class ConfirmedProjectMeasurementRequest(ProjectBriefRequest):
    raw_goal: str | None = Field(default=None, min_length=1, max_length=1000)
    goal_summary_operation_id: str | None = None


class ArchiveProjectRequest(Contract):
    expected_revision: int = Field(ge=1)


def _artifact_view(artifact: MeasurementArtifact) -> dict:
    return artifact.model_dump(mode="json", exclude={"storage_key"})


def _project_view(
    project: Project,
    repository: ProjectMeasurementRepository,
    owner: OwnerIdentity,
    policy: MeasurementExecutionPolicy | None,
) -> dict:
    runs = repository.list_project_runs(project.project_id, owner, limit=None)
    return {
        **project.model_dump(mode="json", exclude={"owner"}),
        "initials": project.initials,
        "domains": project.domains,
        "run_count": len(runs),
        "active_run_count": sum(run.state in {
            MeasurementState.PREPARING,
            MeasurementState.QUEUED,
            MeasurementState.EVALUATING,
            MeasurementState.RECOMMENDING,
        } for run in runs),
        "latest_run": build_run_view(
            repository, runs[0], policy, project_id=project.project_id,
        ) if runs else None,
        "foundry_status": (
            "configured-unverified"
            if project.foundry is not None
            else "manual-setup-required"
        ),
    }


def create_project_router(
    repository: ProjectMeasurementRepository,
    policy: MeasurementExecutionPolicy | None,
    authenticate: Callable[..., OperatorPrincipal],
    artifact_storage: ArtifactStorage,
    run_pending_jobs: Callable[[], None] | None = None,
    goal_summaries: ProjectGoalSummaryService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["projects"])
    coordinator = MeasurementCoordinator(repository)
    jobs = JobService(repository)
    artifact_service = ArtifactService(repository, artifact_storage)
    automatic = ProjectMeasurementOrchestrator(repository, policy)

    def require_operator(
        principal: Annotated[OperatorPrincipal, Depends(authenticate)],
    ) -> OwnerIdentity:
        if "Geo.Operator" not in principal.roles:
            raise HTTPException(403, "Geo.Operator role required")
        return principal.owner

    owner_dependency = Depends(require_operator)

    def scoped_run(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity,
    ) -> MeasurementRun:
        return repository.get_project_run(project_id, run_id, owner)

    def run_view(run: MeasurementRun, project_id: str) -> dict:
        if run.inputs is not None and run.inputs.snapshot.provenance != Provenance.LIVE:
            raise Conflict("Project measurement contains non-live provenance")
        return build_run_view(repository, run, policy, project_id=project_id)

    def require_measurement_policy() -> MeasurementExecutionPolicy:
        if policy is None or policy.execution_mode != "live":
            raise HTTPException(503, "Live measurement policy is not configured; no mock fallback is enabled.")
        return policy

    def measurement_capacity_view(
        owner: OwnerIdentity,
        execution_policy: MeasurementExecutionPolicy,
    ) -> dict:
        historical = repository.measurement_capacity(owner, execution_policy).model_dump(mode="json")
        return {
            **historical,
            "authorized_runs": None,
            "remaining_runs": None,
            "exhausted": False,
            "unlimited": True,
        }

    @router.get("/projects")
    def list_projects(owner: OwnerIdentity = owner_dependency) -> list[dict]:
        return [
            _project_view(project, repository, owner, policy)
            for project in repository.list_projects(owner)
        ]

    @router.post("/projects", status_code=201)
    def create_project(
        body: ProjectCreate,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        project = repository.create_project(owner, body)
        return _project_view(project, repository, owner, policy)

    @router.get("/projects/{project_id}")
    def get_project(
        project_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _project_view(repository.get_project(project_id, owner), repository, owner, policy)

    @router.delete("/projects/{project_id}", status_code=204)
    def delete_project(
        project_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> Response:
        storage_keys = repository.delete_project(project_id, owner)
        for storage_key in storage_keys:
            artifact_storage.delete(storage_key)
        return Response(status_code=204)

    @router.patch("/projects/{project_id}")
    def update_project(
        project_id: str,
        body: ProjectUpdate,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        try:
            project = repository.update_project(project_id, owner, body)
        except ValidationError as error:
            raise HTTPException(422, detail=str(error)) from error
        return _project_view(project, repository, owner, policy)

    @router.post("/projects/{project_id}/archive")
    def archive_project(
        project_id: str,
        body: ArchiveProjectRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        project = repository.archive_project(project_id, owner, body.expected_revision)
        return _project_view(project, repository, owner, policy)

    @router.get("/projects/{project_id}/runs")
    def list_project_runs(
        project_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        owner: OwnerIdentity = owner_dependency,
    ) -> list[dict]:
        return [
            run_view(run, project_id)
            for run in repository.list_project_runs(project_id, owner, limit)
        ]

    @router.get("/projects/{project_id}/runs/{run_id}")
    def get_project_run(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return run_view(scoped_run(project_id, run_id, owner), project_id)

    @router.delete("/projects/{project_id}/runs/{run_id}", status_code=204)
    def delete_project_run(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> Response:
        storage_keys = repository.delete_project_run(project_id, run_id, owner)
        for storage_key in storage_keys:
            artifact_storage.delete(storage_key)
        return Response(status_code=204)

    @router.post("/projects/{project_id}/briefs", status_code=201)
    def create_project_brief(
        project_id: str,
        body: ProjectBriefRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        execution_policy = require_measurement_policy()
        project = repository.get_project(project_id, owner)
        if project.archived:
            raise Conflict("Archived projects cannot create measurement runs")
        brief = Brief.model_validate(body.model_dump())
        if brief.locale != project.default_locale:
            raise HTTPException(422, "Brief locale must match the project locale")
        if not host_in_domains(brief.url.host or "", project.domains):
            raise HTTPException(422, "Brief URL must belong to the project")
        execution_policy.validate_brief(brief, project_bound=True)
        run = repository.create(
            owner,
            brief=brief,
            brand_definition=BrandDefinition(name=project.name, domains=project.domains),
            project_id=project_id,
            competitor_domains=project.competitor_domains,
        )
        return run_view(run, project_id)

    @router.post("/projects/{project_id}/measurements", status_code=202)
    def create_automatic_measurement(
        project_id: str,
        body: ConfirmedProjectMeasurementRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        execution_policy = require_measurement_policy()
        project = repository.get_project(project_id, owner)
        if body.goal_summary_operation_id is not None:
            if goal_summaries is None or body.raw_goal is None:
                raise HTTPException(503, "Goal summarisation is not configured")
            saved_summary = goal_summaries.get(
                project_id,
                owner,
                body.goal_summary_operation_id,
            )
            expected_input = GoalSummaryInput(
                raw_goal=body.raw_goal,
                url=str(body.url),
                audience=body.audience,
                target_kind="page",
            )
            if saved_summary.input_hash != expected_input.input_hash:
                raise Conflict("Goal summary does not match the measurement inputs")
        brief = Brief.model_validate(body.model_dump(
            exclude={"raw_goal", "goal_summary_operation_id"},
        ))
        try:
            job, run = automatic.start(
                project,
                brief,
                idempotency_key=f"automatic-{identifier()}-prepare",
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {
            "run": run_view(run, project_id),
            "job": job_view(job),
            "capacity": measurement_capacity_view(owner, execution_policy),
        }

    @router.post("/projects/{project_id}/measurement-goal-summaries")
    async def summarize_measurement_goal(
        project_id: str,
        body: GoalSummaryRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        if goal_summaries is None:
            raise HTTPException(503, "Goal summarisation is not configured")
        project = repository.get_project(project_id, owner)
        if project.archived:
            raise Conflict("Archived projects cannot create measurement runs")
        preview_brief = Brief(
            url=body.url,
            audience=body.audience or body.raw_goal,
            goal=body.raw_goal,
            locale=project.default_locale,
        )
        if not host_in_domains(preview_brief.url.host or "", project.domains):
            raise HTTPException(422, "Goal summary URL must belong to the project")
        result = await goal_summaries.summarize(project, owner, body)
        return result.model_dump(mode="json")

    @router.get("/projects/{project_id}/measurement-capacity")
    def get_measurement_capacity(
        project_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        execution_policy = require_measurement_policy()
        repository.get_project(project_id, owner)
        return measurement_capacity_view(owner, execution_policy)

    @router.post("/projects/{project_id}/runs/{run_id}/brand-definition")
    def save_brand_definition(
        project_id: str,
        run_id: str,
        body: SaveBrandDefinitionRequest,
        response: Response,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        scoped_run(project_id, run_id, owner)
        record = repository.save_brand_definition(
            run_id, owner, body.definition, body.expected_definition_version,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return {**record.model_dump(mode="json"), "definition_hash": record.definition_hash}

    @router.get("/projects/{project_id}/runs/{run_id}/evidence-assessment")
    def get_evidence_assessment(
        project_id: str,
        run_id: str,
        response: Response,
        definition_version: int | None = Query(default=None, ge=1),
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = scoped_run(project_id, run_id, owner)
        record = repository.get_brand_definition(run_id, owner, definition_version)
        report = (
            build_evidence_assessment(
                run.measurement, record, run_id=run_id, run_revision=run.revision,
                competitor_domains=run.competitor_domains,
            )
            if run.measurement else None
        )
        status = (
            "unconfigured" if record is None else
            "ready" if report else
            "unavailable-no-saved-measurement"
            if run.state in {"failed", "cancelled", "needs-review"} else
            "pending-saved-measurement"
        )
        response.headers["Cache-Control"] = "private, no-store"
        return {
            "run_id": run_id,
            "run_revision": run.revision,
            "status": status,
            "brand_definition": (
                {**record.model_dump(mode="json"), "definition_hash": record.definition_hash}
                if record else None
            ),
            "assessment": report.model_dump(mode="json") if report else None,
            "assessment_hash": report.assessment_hash if report else None,
        }

    @router.get("/projects/{project_id}/runs/{run_id}/evidence-assessment/download")
    def download_assessment(
        project_id: str,
        run_id: str,
        definition_version: int = Query(ge=1),
        measurement_hash: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
        owner: OwnerIdentity = owner_dependency,
    ) -> Response:
        run = scoped_run(project_id, run_id, owner)
        record = repository.get_brand_definition(run_id, owner, definition_version)
        if run.measurement is None:
            raise Conflict("No saved measurement is available for assessment")
        report = build_evidence_assessment(
            run.measurement,
            record,
            run_id=run_id,
            run_revision=run.revision,
            competitor_domains=run.competitor_domains,
        )
        if measurement_hash is not None and report.measurement_hash != measurement_hash:
            raise Conflict("Saved measurement changed; reload the assessment")
        content = render_assessment_bundle(run.measurement, report)
        return Response(content, media_type="application/zip", headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f'attachment; filename="geo-assessment-{run.run_id}-v{definition_version}.zip"'
            ),
            "ETag": f'"{sha256(content).hexdigest()}"',
        })

    @router.get("/projects/{project_id}/runs/{run_id}/content-strategy")
    def get_content_strategy(
        project_id: str,
        run_id: str,
        response: Response,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = scoped_run(project_id, run_id, owner)
        report = build_content_strategy(run.measurement) if run.measurement else None
        response.headers["Cache-Control"] = "private, no-store"
        return {
            "run_id": run_id,
            "run_revision": run.revision,
            "status": "ready" if report else "pending-saved-measurement",
            "report": report.model_dump(mode="json") if report else None,
        }

    @router.get("/projects/{project_id}/runs/{run_id}/content-strategy/download")
    def download_content_strategy(
        project_id: str,
        run_id: str,
        measurement_hash: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$"),
        owner: OwnerIdentity = owner_dependency,
    ) -> Response:
        run = scoped_run(project_id, run_id, owner)
        if run.measurement is None:
            raise Conflict("No saved measurement is available for content recommendations")
        report = build_content_strategy(run.measurement)
        if measurement_hash is not None and report.measurement_hash != measurement_hash:
            raise Conflict("Saved measurement changed; reload the content recommendations")
        content = render_content_strategy_bundle(run.measurement)
        return Response(content, media_type="application/zip", headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="geo-content-strategy-{run.run_id}.zip"',
            "ETag": f'"{sha256(content).hexdigest()}"',
        })

    @router.post("/projects/{project_id}/runs/{run_id}/prepare", status_code=202)
    def prepare_run(
        project_id: str,
        run_id: str,
        body: PrepareRunRequest,
        background_tasks: BackgroundTasks,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        require_measurement_policy()
        if body.confirm_preparation_calls is not True:
            raise Conflict("Preparation provider calls require explicit confirmation")
        run = scoped_run(project_id, run_id, owner)
        if run.brief is None:
            raise Conflict("Preparation requires a submitted brief")
        job, updated = jobs.enqueue(
            run_id,
            owner,
            body.expected_revision,
            JobType.PREPARE,
            body.idempotency_key,
            PreparationRequest(
                brief=run.brief,
                confirm_preparation_calls=True,
                project_bound=True,
            ),
        )
        if run_pending_jobs is not None:
            background_tasks.add_task(run_pending_jobs)
        return {"job": job_view(job), "run": run_view(updated, project_id)}

    @router.put("/projects/{project_id}/runs/{run_id}/queries")
    def revise_queries(
        project_id: str,
        run_id: str,
        body: ReviseQueriesRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = scoped_run(project_id, run_id, owner)
        if run.inputs is None:
            raise Conflict("Query revision requires prepared measurement inputs")
        try:
            query_plan = QueryPlan(queries=body.queries)
            inputs = MeasurementInputs.model_validate({
                **run.inputs.model_dump(mode="json"),
                "query_plan": query_plan.model_dump(mode="json"),
            })
        except ValueError:
            raise HTTPException(
                422, "Queries must be unique and retain supported page evidence",
            ) from None
        return run_view(coordinator.revise_inputs(
            run_id, owner, body.expected_revision, inputs,
        ), project_id)

    @router.post("/projects/{project_id}/runs/{run_id}/query-approval")
    def approve_queries(
        project_id: str,
        run_id: str,
        body: ApproveQueriesRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        scoped_run(project_id, run_id, owner)
        return run_view(coordinator.approve(
            run_id, owner, body.expected_revision, body.input_hash,
        ), project_id)

    @router.post("/projects/{project_id}/runs/{run_id}/start", status_code=202)
    def start_evaluation(
        project_id: str,
        run_id: str,
        body: StartEvaluationRequest,
        background_tasks: BackgroundTasks,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        require_measurement_policy()
        if body.confirm_evaluation_calls is not True:
            raise Conflict("Evaluation provider calls require explicit confirmation")
        scoped_run(project_id, run_id, owner)
        job, updated = jobs.enqueue(
            run_id,
            owner,
            body.expected_revision,
            JobType.EVALUATE,
            body.idempotency_key,
            EvaluationRequest(
                confirm_evaluation_calls=True,
                include_recommendations=body.include_recommendations,
                project_bound=True,
            ),
        )
        if run_pending_jobs is not None:
            background_tasks.add_task(run_pending_jobs)
        return {"job": job_view(job), "run": run_view(updated, project_id)}

    @router.post("/projects/{project_id}/runs/{run_id}/recover-evaluators", status_code=202)
    def recover_evaluators(
        project_id: str,
        run_id: str,
        body: RecoverEvaluatorsRequest,
        background_tasks: BackgroundTasks,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        require_measurement_policy()
        if body.confirm_evaluation_calls is not True:
            raise Conflict("Evaluator recovery calls require explicit confirmation")
        scoped_run(project_id, run_id, owner)
        job, updated = jobs.enqueue(
            run_id,
            owner,
            body.expected_revision,
            JobType.RECOVER_EVALUATORS,
            body.idempotency_key,
            EvaluatorRecoveryRequest(confirm_evaluation_calls=True),
        )
        if run_pending_jobs is not None:
            background_tasks.add_task(run_pending_jobs)
        return {"job": job_view(job), "run": run_view(updated, project_id)}

    @router.post("/projects/{project_id}/runs/{run_id}/recommendation-review")
    def review_recommendations(
        project_id: str,
        run_id: str,
        body: ReviewRecommendationsRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        scoped_run(project_id, run_id, owner)
        return run_view(coordinator.review_recommendations(
            run_id, owner, body.expected_revision, body.decisions,
        ), project_id)

    @router.get("/projects/{project_id}/runs/{run_id}/events")
    def get_events(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = scoped_run(project_id, run_id, owner)
        return {
            "run_id": run.run_id,
            "revision": run.revision,
            "events": [event.model_dump(mode="json") for event in run.events],
        }

    @router.get("/projects/{project_id}/runs/{run_id}/evidence/{evidence_id}")
    def get_evidence(
        project_id: str,
        run_id: str,
        evidence_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = scoped_run(project_id, run_id, owner)
        if run.measurement is not None:
            for retrieval in run.measurement.retrievals:
                for source in retrieval.sources:
                    if source.evidence_id == evidence_id:
                        return {
                            **source.model_dump(mode="json"),
                            "evidence_type": "grounding-citation",
                            "query_id": retrieval.query_id,
                            "grounding_query": retrieval.grounding_query,
                        }
        raise NotFound("Evidence not found")

    @router.get("/projects/{project_id}/runs/{run_id}/jobs")
    def list_run_jobs(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> list[dict]:
        scoped_run(project_id, run_id, owner)
        return [job_view(job) for job in repository.list_run_jobs(run_id, owner)]

    @router.get("/projects/{project_id}/runs/{run_id}/jobs/{job_id}")
    def get_job(
        project_id: str,
        run_id: str,
        job_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        scoped_run(project_id, run_id, owner)
        job = repository.get_job(job_id, owner)
        if job.run_id != run_id:
            raise NotFound("Workflow job not found")
        return job_view(job)

    @router.get("/projects/{project_id}/runs/{run_id}/progress")
    def get_run_progress(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        scoped_run(project_id, run_id, owner)
        return repository.get_run_progress(run_id, owner).model_dump(mode="json")

    @router.post("/projects/{project_id}/runs/{run_id}/jobs/{job_id}/cancel")
    def cancel_job(
        project_id: str,
        run_id: str,
        job_id: str,
        body: RevisionRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        run = scoped_run(project_id, run_id, owner)
        job = repository.get_job(job_id, owner)
        if job.run_id != run_id:
            raise NotFound("Workflow job not found")
        if run.revision != body.expected_revision:
            raise Conflict("Stale measurement revision; reload the run")
        cancelled, updated = jobs.cancel(job_id, owner)
        return {"job": job_view(cancelled), "run": run_view(updated, project_id)}

    @router.post("/projects/{project_id}/runs/{run_id}/exports", status_code=201)
    def export_run(
        project_id: str,
        run_id: str,
        body: ExportRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        scoped_run(project_id, run_id, owner)
        artifact, run = artifact_service.export(
            run_id,
            owner,
            body.expected_revision,
            body.definition_version,
            body.definition_hash,
        )
        return {
            "artifact": _artifact_view(artifact),
            "run": run_view(run, project_id),
            "download_url": (
                f"/api/v2/projects/{project_id}/runs/{run_id}/artifacts/{artifact.artifact_id}"
            ),
        }

    @router.get("/projects/{project_id}/runs/{run_id}/artifacts")
    def list_artifacts(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> list[dict]:
        run = scoped_run(project_id, run_id, owner)
        if run.state != MeasurementState.EXPORTED:
            return []
        return [_artifact_view(repository.get_run_artifact(run_id, owner))]

    @router.get("/projects/{project_id}/runs/{run_id}/artifacts/{artifact_id}")
    def download_artifact(
        project_id: str,
        run_id: str,
        artifact_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> Response:
        scoped_run(project_id, run_id, owner)
        artifact, content = artifact_service.download(run_id, artifact_id, owner)
        return Response(content, media_type=artifact.media_type, headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="geo-{run_id}.zip"',
            "ETag": f'"{artifact.content_hash}"',
            "X-Content-Type-Options": "nosniff",
        })

    return router
