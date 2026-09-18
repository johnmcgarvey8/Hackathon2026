from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from geo_agent.contracts import Brief, Contract
from geo_agent.evaluation import measurement_scores
from geo_agent.evidence_assessment import BrandDefinition
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_api import OperatorPrincipal
from geo_agent.measurement_workflow import MeasurementRun, MeasurementState, OwnerIdentity
from geo_agent.projects import Project, ProjectCreate, ProjectMeasurementRepository, ProjectUpdate


class ProjectBriefRequest(Brief):
    brand_definition: BrandDefinition | None = None


class ArchiveProjectRequest(Contract):
    expected_revision: int = Field(ge=1)


def _run_view(run: MeasurementRun) -> dict:
    payload = run.model_dump(mode="json", exclude={"owner"})
    if payload["approval"] is not None:
        payload["approval"].pop("actor", None)
    if payload["recommendation_review"] is not None:
        payload["recommendation_review"].pop("actor", None)
    payload["approval_hash"] = run.inputs.approval_hash if run.inputs is not None else None
    payload["scores"] = measurement_scores(run.measurement) if run.measurement is not None else None
    return payload


def _project_view(
    project: Project,
    repository: ProjectMeasurementRepository,
    owner: OwnerIdentity,
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
        "latest_run": _run_view(runs[0]) if runs else None,
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
) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["projects"])

    def require_operator(
        principal: Annotated[OperatorPrincipal, Depends(authenticate)],
    ) -> OwnerIdentity:
        if "Geo.Operator" not in principal.roles:
            raise HTTPException(403, "Geo.Operator role required")
        return principal.owner

    owner_dependency = Depends(require_operator)

    @router.get("/projects")
    def list_projects(owner: OwnerIdentity = owner_dependency) -> list[dict]:
        return [
            _project_view(project, repository, owner)
            for project in repository.list_projects(owner)
        ]

    @router.post("/projects", status_code=201)
    def create_project(
        body: ProjectCreate,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        project = repository.create_project(owner, body)
        return _project_view(project, repository, owner)

    @router.get("/projects/{project_id}")
    def get_project(
        project_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _project_view(repository.get_project(project_id, owner), repository, owner)

    @router.patch("/projects/{project_id}")
    def update_project(
        project_id: str,
        body: ProjectUpdate,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        project = repository.update_project(project_id, owner, body)
        return _project_view(project, repository, owner)

    @router.post("/projects/{project_id}/archive")
    def archive_project(
        project_id: str,
        body: ArchiveProjectRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        project = repository.archive_project(project_id, owner, body.expected_revision)
        return _project_view(project, repository, owner)

    @router.get("/projects/{project_id}/runs")
    def list_project_runs(
        project_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        owner: OwnerIdentity = owner_dependency,
    ) -> list[dict]:
        return [
            _run_view(run)
            for run in repository.list_project_runs(project_id, owner, limit)
        ]

    @router.get("/projects/{project_id}/runs/{run_id}")
    def get_project_run(
        project_id: str,
        run_id: str,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        return _run_view(repository.get_project_run(project_id, run_id, owner))

    @router.post("/projects/{project_id}/briefs", status_code=201)
    def create_project_brief(
        project_id: str,
        body: ProjectBriefRequest,
        owner: OwnerIdentity = owner_dependency,
    ) -> dict:
        if policy is None:
            raise HTTPException(503, "Live measurement policy is not configured; no mock fallback is enabled.")
        project = repository.get_project(project_id, owner)
        brief = Brief.model_validate(body.model_dump(exclude={"brand_definition"}))
        if brief.locale != project.default_locale:
            raise HTTPException(422, "Brief locale must match the project locale")
        if brief.url.host not in project.domains and not any(
            brief.url.host.endswith(f".{domain}") for domain in project.domains
        ):
            raise HTTPException(422, "Brief URL must belong to the project")
        policy.validate_brief(brief)
        run = repository.create(
            owner,
            brief=brief,
            brand_definition=body.brand_definition,
            project_id=project_id,
        )
        return _run_view(run)

    return router
