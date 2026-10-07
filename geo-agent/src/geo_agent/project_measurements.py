from geo_agent.contracts import Brief, digest
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
from geo_agent.projects import (
    Project,
    ProjectMeasurementRepository,
    host_in_domains,
)
from geo_agent.specialist_agents import (
    AgentStageStatus,
    SpecialistAgentRole,
    SpecialistProviderMode,
)
from geo_agent.specialist_foundry import SpecialistFoundrySettings
from geo_agent.specialist_workflow import SpecialistStageRequest
from geo_agent.workflow import Conflict


class ProjectMeasurementOrchestrator:
    def __init__(
        self,
        repository: ProjectMeasurementRepository,
        policy: MeasurementExecutionPolicy | None,
        specialist_settings: SpecialistFoundrySettings | None = None,
    ):
        self.repository = repository
        self.policy = policy
        self.coordinator = MeasurementCoordinator(repository)
        self.jobs = JobService(repository)
        self.specialist_settings = specialist_settings or SpecialistFoundrySettings()

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
        host = brief.url.host or ""
        if not host_in_domains(host, project.domains):
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
        policy = self.require_live_policy()
        run = self.repository.create(
            project.owner,
            brief=brief,
            brand_definition=BrandDefinition(name=project.name, domains=project.domains),
            project_id=project.project_id,
            competitor_domains=project.competitor_domains,
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
            policy_id=policy.policy_id,
            policy_hash=policy.policy_hash,
            operation_ceiling=(
                policy.max_browse_calls
                + policy.max_page_analysis_calls
                + policy.max_query_plan_calls
            ),
        )

    def reconcile_job(
        self,
        job: WorkflowJob,
        run: MeasurementRun,
    ) -> tuple[WorkflowJob, MeasurementRun] | None:
        if job.state != JobState.COMPLETED:
            return None
        if job.job_type in {JobType.EVALUATE, JobType.RECOVER_EVALUATORS}:
            return self._reconcile_recommendations(job, run)
        if job.job_type != JobType.PREPARE:
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
            policy_id=self.policy.policy_id,
            policy_hash=self.policy.policy_hash,
            operation_ceiling=(
                self.policy.max_search_calls
                + self.policy.max_evaluator_calls_per_profile
                * len(self.policy.profiles)
            ),
        )

    def _reconcile_recommendations(
        self,
        job: WorkflowJob,
        run: MeasurementRun,
    ) -> tuple[WorkflowJob, MeasurementRun] | None:
        current = self.repository.get(run.run_id, run.owner)
        recommendations_requested = (
            EvaluationRequest.model_validate(job.request).include_recommendations
            if job.job_type == JobType.EVALUATE
            else any(
                item.job_type == JobType.AGENT_STAGE
                and item.request.get("role")
                == SpecialistAgentRole.RECOMMENDATIONS.value
                for item in self.repository.list_run_jobs(
                    current.run_id,
                    current.owner,
                )
            )
        )
        if (
            not recommendations_requested
            or current.measurement is None
            or current.state not in {MeasurementState.READY, MeasurementState.PARTIAL}
            or not any(result.status == "completed" for result in current.measurement.results)
        ):
            return None
        existing = tuple(
            item for item in self.repository.list_run_jobs(current.run_id, current.owner)
            if (
                item.job_type == JobType.AGENT_STAGE
                and item.request.get("role") == SpecialistAgentRole.RECOMMENDATIONS.value
            )
        )
        current_input_hash = digest(current.measurement.model_dump(mode="json"))
        current_stage = next(
            (
                item for item in current.agent_stages
                if item.role == SpecialistAgentRole.RECOMMENDATIONS
            ),
            None,
        )
        if (
            existing
            and current_stage is not None
            and current_stage.input_hash == current_input_hash
        ):
            return existing[0], current
        binding, provider_mode = self._resolve_recommendations(current)
        return self.jobs.enqueue(
            current.run_id,
            current.owner,
            current.revision,
            JobType.AGENT_STAGE,
            f"specialist-{job.job_id}-recommendations",
            SpecialistStageRequest(
                role=SpecialistAgentRole.RECOMMENDATIONS,
                provider_mode=provider_mode,
                binding=binding,
                confirm_provider_call=True,
                source_job_id=job.job_id,
            ),
            policy_id=job.policy_id,
            policy_hash=job.policy_hash,
            operation_ceiling=1,
        )

    def retry_recommendations(
        self,
        run: MeasurementRun,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        existing = next(
            (
                item for item in self.repository.list_run_jobs(run.run_id, run.owner)
                if (
                    item.job_type == JobType.AGENT_STAGE
                    and item.idempotency_key == idempotency_key
                    and item.request.get("retry_of_job_id") is not None
                    and item.request.get("role")
                    == SpecialistAgentRole.RECOMMENDATIONS.value
                )
            ),
            None,
        )
        if existing is not None:
            return existing, self.repository.get(run.run_id, run.owner)
        if run.revision != expected_revision:
            raise Conflict("Stale measurement revision; reload the run")
        stage = next(
            (
                item for item in run.agent_stages
                if item.role == SpecialistAgentRole.RECOMMENDATIONS
            ),
            None,
        )
        if stage is None or stage.status != AgentStageStatus.FAILED or stage.job_id is None:
            raise Conflict("Recommendation retry requires a failed saved specialist stage")
        binding, provider_mode = self._resolve_recommendations(run)
        previous = self.repository.get_job(stage.job_id, run.owner)
        return self.jobs.enqueue(
            run.run_id,
            run.owner,
            run.revision,
            JobType.AGENT_STAGE,
            idempotency_key,
            SpecialistStageRequest(
                role=SpecialistAgentRole.RECOMMENDATIONS,
                provider_mode=provider_mode,
                binding=binding,
                confirm_provider_call=True,
                retry_of_job_id=stage.job_id,
            ),
            policy_id=previous.policy_id,
            policy_hash=previous.policy_hash,
            operation_ceiling=1,
        )

    def _resolve_recommendations(
        self,
        current: MeasurementRun,
    ):
        project_binding = None
        project_id = self.repository.get_run_project_id(current.run_id, current.owner)
        if project_id is not None:
            project = self.repository.get_project(project_id, current.owner)
            project_binding = project.specialist_binding(SpecialistAgentRole.RECOMMENDATIONS)
        environment_binding = self.specialist_settings.binding(
            SpecialistAgentRole.RECOMMENDATIONS
        )
        binding = project_binding or environment_binding
        provider_mode = (
            SpecialistProviderMode.PROJECT_AGENT
            if project_binding is not None
            else SpecialistProviderMode.ENVIRONMENT_AGENT
            if environment_binding is not None
            else SpecialistProviderMode.BASELINE_PROVIDER
        )
        return binding, provider_mode
