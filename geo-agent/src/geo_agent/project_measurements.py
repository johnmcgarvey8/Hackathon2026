from geo_agent.contracts import Brief
from geo_agent.evaluation_workflow import EvaluationRequest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.evidence_assessment import BrandDefinition
from geo_agent.jobs import JobService, JobState, JobType, WorkflowJob
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementRun,
    MeasurementState,
)
from geo_agent.preparation import PreparationRequest
from geo_agent.projects import Project, ProjectMeasurementRepository
from geo_agent.workflow import Conflict


class ProjectMeasurementOrchestrator:
    def __init__(
        self,
        repository: ProjectMeasurementRepository,
        policy: MeasurementExecutionPolicy | None,
    ):
        self.repository = repository
        self.policy = policy
        self.coordinator = MeasurementCoordinator(repository)
        self.jobs = JobService(repository)

    def require_live_policy(self) -> MeasurementExecutionPolicy:
        if self.policy is None or self.policy.execution_mode != "live":
            raise Conflict("Live project measurement is not configured; no mock fallback is enabled")
        return self.policy

    def validate_brief(self, project: Project, brief: Brief) -> None:
        policy = self.require_live_policy()
        if project.archived:
            raise Conflict("Archived projects cannot create measurement runs")
        if brief.locale != project.default_locale:
            raise ValueError("Brief locale must match the project locale")
        host = (brief.url.host or "").casefold()
        if host not in project.domains and not any(
            host.endswith(f".{domain}") for domain in project.domains
        ):
            raise ValueError("Brief URL must belong to the project")
        policy.validate_brief(brief, project_bound=True)

    def start(
        self,
        project: Project,
        brief: Brief,
        *,
        idempotency_key: str,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        self.validate_brief(project, brief)
        run = self.repository.create(
            project.owner,
            brief=brief,
            brand_definition=BrandDefinition(name=project.name, domains=project.domains),
            project_id=project.project_id,
        )
        return self.jobs.enqueue(
            run.run_id,
            project.owner,
            run.revision,
            JobType.PREPARE,
            idempotency_key,
            PreparationRequest(
                brief=brief,
                confirm_preparation_calls=True,
                project_bound=True,
            ),
        )

    def reconcile_job(
        self,
        job: WorkflowJob,
        run: MeasurementRun,
    ) -> tuple[WorkflowJob, MeasurementRun] | None:
        if (
            job.state != JobState.COMPLETED
            or job.job_type != JobType.PREPARE
        ):
            return None
        current = self.repository.get(run.run_id, run.owner)
        if current.inputs is None:
            raise Conflict("Automatic evaluation requires prepared measurement inputs")
        try:
            current.inputs.validate_live()
        except ValueError as error:
            raise Conflict(str(error)) from None
        if current.state == MeasurementState.AWAITING_APPROVAL:
            if current.approval is None:
                current = self.coordinator.approve(
                    current.run_id,
                    current.owner,
                    current.revision,
                    current.inputs.approval_hash,
                )
            elif current.approval.input_hash != current.inputs.approval_hash:
                raise Conflict("Automatic approval does not match the current measurement inputs")
        elif current.state not in {
            MeasurementState.QUEUED,
            MeasurementState.EVALUATING,
            MeasurementState.READY,
            MeasurementState.PARTIAL,
            MeasurementState.FAILED,
        }:
            raise Conflict("Automatic measurement cannot advance from the current state")
        if current.state != MeasurementState.AWAITING_APPROVAL:
            existing = tuple(
                item for item in self.repository.list_run_jobs(current.run_id, current.owner)
                if item.job_type == JobType.EVALUATE
            )
            if existing:
                return existing[0], current
        return self.jobs.enqueue(
            current.run_id,
            current.owner,
            current.revision,
            JobType.EVALUATE,
            f"automatic-{current.run_id}-evaluate",
            EvaluationRequest(
                confirm_evaluation_calls=True,
                include_recommendations=True,
                project_bound=True,
            ),
        )
