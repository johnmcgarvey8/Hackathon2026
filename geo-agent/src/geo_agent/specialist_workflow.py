from typing import Literal, Protocol

from geo_agent.contracts import Contract, MeasurementResults, digest, utc_now
from geo_agent.jobs import ClaimedOperationRunner, JobType, WorkflowJob
from geo_agent.measurement_workflow import (
    MeasurementEvent,
    MeasurementRepository,
    MeasurementRun,
    MeasurementState,
    Mutation,
)
from geo_agent.projects import FoundryProjectBinding
from geo_agent.recommendations import (
    RecommendationProposal,
    RecommendationReport,
    RecommendationService,
    build_recommendation_context,
    validate_recommendations,
)
from geo_agent.specialist_agents import (
    AgentBindingSnapshot,
    AgentStageRecord,
    AgentStageStatus,
    SpecialistAgentCall,
    SpecialistAgentRole,
    SpecialistProviderMode,
    upsert_stage,
)
from geo_agent.specialist_foundry import HostedSpecialistAgent
from geo_agent.workflow import Conflict


class SpecialistStageRequest(Contract):
    role: SpecialistAgentRole
    provider_mode: SpecialistProviderMode
    binding: FoundryProjectBinding | None = None
    confirm_provider_call: Literal[True]
    source_job_id: str | None = None
    retry_of_job_id: str | None = None


class RecommendationProvider(Protocol):
    def recommend(self, measurement: MeasurementResults) -> RecommendationReport: ...


class SpecialistStageHandler:
    def __init__(
        self,
        repository: MeasurementRepository,
        baseline_recommendations: RecommendationService,
        hosted_agent: HostedSpecialistAgent,
    ):
        self.repository = repository
        self.baseline_recommendations = baseline_recommendations
        self.hosted_agent = hosted_agent

    def __call__(
        self,
        job: WorkflowJob,
        operations: ClaimedOperationRunner,
    ) -> Mutation:
        if job.job_type != JobType.AGENT_STAGE:
            raise Conflict("Specialist stage handler requires an agent-stage job")
        request = SpecialistStageRequest.model_validate(job.request)
        if request.role != SpecialistAgentRole.RECOMMENDATIONS:
            raise Conflict("This specialist stage role is not implemented")
        run = self.repository.get(job.run_id, job.owner)
        if (
            run.measurement is None
            or run.state not in {MeasurementState.READY, MeasurementState.PARTIAL}
        ):
            raise Conflict("Recommendations require saved measurement results")
        measurement = run.measurement
        report = operations.call(
            f"{job.job_id}:recommend",
            "recommendation-model",
            lambda: self._recommend(request, measurement),
        )

        def mutation(current: MeasurementRun) -> MeasurementRun:
            if (
                current.measurement is None
                or digest(current.measurement.model_dump(mode="json"))
                != digest(measurement.model_dump(mode="json"))
            ):
                raise Conflict("Recommendation stage no longer matches the saved measurement")
            output_hash = digest(report.model_dump(mode="json"))
            call = report.agent_call
            stage = AgentStageRecord(
                role=request.role,
                status=AgentStageStatus.COMPLETED,
                provider_mode=request.provider_mode,
                job_id=job.job_id,
                binding=self._binding_snapshot(request.binding),
                input_hash=digest(measurement.model_dump(mode="json")),
                output_hash=output_hash,
                provider_response_id=(
                    call.provider_response_id if call is not None
                    else report.model_call.response_id if report.model_call is not None
                    else None
                ),
                created_at=self._created_at(current, request.role),
                updated_at=utc_now(),
            )
            return current.model_copy(update={
                "recommendations": report,
                "recommendation_review": None,
                "agent_stages": upsert_stage(current.agent_stages, stage),
                "events": (*current.events, MeasurementEvent(
                    sequence=len(current.events) + 1,
                    event_type="recommendations-completed",
                )),
            })

        return mutation

    def _recommend(
        self,
        request: SpecialistStageRequest,
        measurement: MeasurementResults,
    ) -> tuple[RecommendationReport, dict]:
        if request.provider_mode == SpecialistProviderMode.BASELINE_PROVIDER:
            report = self.baseline_recommendations.recommend(measurement)
            call = report.model_call
            report = report.model_copy(update={
                "agent_call": SpecialistAgentCall(
                    role=SpecialistAgentRole.RECOMMENDATIONS,
                    provider_mode=request.provider_mode,
                    provider_response_id=call.response_id if call is not None else None,
                    model=call.model if call is not None else None,
                    input_tokens=call.input_tokens if call is not None else None,
                    output_tokens=call.output_tokens if call is not None else None,
                ),
            })
        else:
            if request.binding is None:
                raise Conflict("Hosted specialist provider requires a pinned agent binding")
            context = build_recommendation_context(measurement)
            if not context["comparison_sources"]:
                report = validate_recommendations(
                    measurement,
                    RecommendationProposal(
                        tasks=(),
                        reason="No eligible comparison sources in the saved retrieval packets.",
                    ),
                    context,
                ).model_copy(update={
                    "agent_call": SpecialistAgentCall(
                        role=SpecialistAgentRole.RECOMMENDATIONS,
                        provider_mode=request.provider_mode,
                        agent_name=request.binding.agent_name,
                        agent_version=request.binding.agent_version,
                    ),
                })
            else:
                proposal, call = self.hosted_agent.invoke(
                    request.binding,
                    SpecialistAgentRole.RECOMMENDATIONS,
                    request.provider_mode,
                    context,
                    RecommendationProposal,
                )
                report = validate_recommendations(
                    measurement,
                    RecommendationProposal.model_validate(proposal),
                    context,
                ).model_copy(update={"agent_call": call})
        report.validate_for(measurement)
        metadata = (
            report.agent_call.model_dump(mode="json")
            if report.agent_call is not None
            else {"provider_mode": request.provider_mode.value}
        )
        return report, metadata

    @staticmethod
    def _binding_snapshot(
        binding: FoundryProjectBinding | None,
    ) -> AgentBindingSnapshot | None:
        if binding is None:
            return None
        return AgentBindingSnapshot(
            project_endpoint=binding.project_endpoint,
            agent_name=binding.agent_name,
            agent_version=binding.agent_version,
        )

    @staticmethod
    def _created_at(
        run: MeasurementRun,
        role: SpecialistAgentRole,
    ):
        existing = next((item for item in run.agent_stages if item.role == role), None)
        return existing.created_at if existing is not None else utc_now()
