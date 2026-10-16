from typing import Literal, Protocol

from geo_agent.contracts import Contract, MeasurementResults, digest, utc_now
from geo_agent.evidence_assessment import build_evidence_assessment
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import ClaimedOperationRunner, JobType, WorkflowJob
from geo_agent.measurement_workflow import (
    MeasurementEvent,
    MeasurementRepository,
    MeasurementRun,
    MeasurementState,
    Mutation,
)
from geo_agent.projects import FoundryProjectBinding
from geo_agent.recommendation_plan import (
    PlanProposal,
    RECOMMENDATION_PLANNER_PROMPT,
    RecommendationPlanReport,
    build_recommendation_context_v3,
    validate_plan,
)
from geo_agent.recommendations import (
    RecommendationReport,
    RecommendationService,
    build_content_strategy,
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
from geo_agent.technical_checks import TechnicalSignals
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict


class SpecialistStageRequest(Contract):
    role: SpecialistAgentRole
    provider_mode: SpecialistProviderMode
    binding: FoundryProjectBinding | None = None
    confirm_provider_call: Literal[True]
    source_job_id: str | None = None
    retry_of_job_id: str | None = None


class RecommendationProvider(Protocol):
    def recommend(self, measurement: MeasurementResults) -> RecommendationPlanReport: ...


class TechnicalBrowseProvider(Protocol):
    def browse_markup(self, brief) -> TechnicalSignals: ...


class SpecialistStageHandler:
    def __init__(
        self,
        repository: MeasurementRepository,
        baseline_recommendations: RecommendationService,
        hosted_agent: HostedSpecialistAgent,
        policy: MeasurementExecutionPolicy | None = None,
        technical_browser: TechnicalBrowseProvider | None = None,
    ):
        self.repository = repository
        self.policy = policy
        self.baseline_recommendations = baseline_recommendations
        self.hosted_agent = hosted_agent
        self.technical_browser = technical_browser

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
        technical_signals = TechnicalSignals(reason="feature-disabled")
        if self.policy is not None and self.policy.technical_browse:
            technical_signals = operations.call(
                f"{job.job_id}:technical-browse",
                "recommendation-technical-browse",
                lambda: self._browse_technical(measurement),
            )
        brand_record = self.repository.get_brand_definition(run.run_id, run.owner)
        assessment = (
            build_evidence_assessment(
                measurement,
                brand_record,
                run_id=run.run_id,
                run_revision=run.revision,
            )
            if brand_record is not None else None
        )
        context = build_recommendation_context_v3(
            measurement,
            assessment=assessment,
            content_strategy=build_content_strategy(measurement),
            page_analysis=run.page_analysis,
            brand_record=brand_record,
            competitor_domains=run.competitor_domains,
            technical_signals=technical_signals,
        )
        if not any(packet.status == "completed" for packet in measurement.retrievals):
            report = validate_plan(
                measurement,
                PlanProposal(
                    diagnosis="No retrieval packet completed.",
                    key_findings=(),
                    page_optimisations=(),
                    new_content_recommendations=(),
                    not_recommended=(),
                    reason="No retrieval completed, so recommendations cannot be evidence-bound.",
                ),
                context,
                include_rule_items=False,
            )
        else:
            recommendation_operation_key = (
                f"{job.job_id}:recommendation-plan"
                if (
                    request.provider_mode != SpecialistProviderMode.BASELINE_PROVIDER
                    or hasattr(self.baseline_recommendations, "foundry")
                )
                else f"{job.job_id}:recommend"
            )
            report = operations.call(
                recommendation_operation_key,
                "recommendation-model",
                lambda: self._recommend(request, measurement, context),
            )

        def mutation(current: MeasurementRun) -> MeasurementRun:
            if (
                current.measurement is None
                or digest(current.measurement.model_dump(mode="json"))
                != digest(measurement.model_dump(mode="json"))
            ):
                raise Conflict("Recommendation stage no longer matches the saved measurement")
            output_hash = digest(report.model_dump(mode="json"))
            call = report.plan_call if isinstance(report, RecommendationPlanReport) else None
            stage = AgentStageRecord(
                role=request.role,
                status=AgentStageStatus.COMPLETED,
                provider_mode=request.provider_mode,
                job_id=job.job_id,
                binding=self._binding_snapshot(request.binding),
                input_hash=digest(measurement.model_dump(mode="json")),
                output_hash=output_hash,
                provider_response_id=(
                    call.provider_response_id if call is not None else None
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
        context: dict,
    ) -> tuple[RecommendationPlanReport | RecommendationReport, dict]:
        if request.provider_mode == SpecialistProviderMode.BASELINE_PROVIDER:
            if not hasattr(self.baseline_recommendations, "foundry"):
                report = self.baseline_recommendations.recommend(measurement)
                report.validate_for(measurement)
                return report, {"provider_mode": request.provider_mode.value}
            max_output_tokens = (
                self.policy.recommendation_planner_max_output_tokens
                if self.policy is not None else 4000
            )
            response = self.baseline_recommendations.foundry._parse(
                RECOMMENDATION_PLANNER_PROMPT,
                context,
                PlanProposal,
                max_output_tokens=max_output_tokens,
            )
            metadata = self.baseline_recommendations.foundry.metadata(response)
            call = SpecialistAgentCall(
                role=SpecialistAgentRole.RECOMMENDATIONS,
                provider_mode=request.provider_mode,
                provider_response_id=metadata.response_id,
                model=metadata.model,
                input_tokens=metadata.input_tokens,
                output_tokens=metadata.output_tokens,
            )
            report = validate_plan(
                measurement,
                PlanProposal.model_validate(response.output_parsed),
                context,
                plan_call=call,
            )
        else:
            if request.binding is None:
                raise Conflict("Hosted specialist provider requires a pinned agent binding")
            max_output_tokens = (
                self.policy.recommendation_planner_max_output_tokens
                if self.policy is not None else 4000
            )
            proposal, call = self.hosted_agent.invoke(
                request.binding,
                SpecialistAgentRole.RECOMMENDATIONS,
                request.provider_mode,
                context,
                PlanProposal,
                max_output_tokens=max_output_tokens,
                strict_json_schema=False,
            )
            report = validate_plan(
                measurement,
                PlanProposal.model_validate(proposal),
                context,
                plan_call=call,
            )
        report.validate_for(measurement, context)
        metadata = (
            report.plan_call.model_dump(mode="json")
            if report.plan_call is not None
            else {"provider_mode": request.provider_mode.value}
        )
        return report, metadata

    def _browse_technical(
        self,
        measurement: MeasurementResults,
    ) -> tuple[TechnicalSignals, dict[str, str]]:
        if self.technical_browser is None:
            signals = TechnicalSignals(reason="provider-unavailable")
            return signals, {"status": signals.status, "reason": signals.reason or ""}
        try:
            signals = self.technical_browser.browse_markup(measurement.inputs.brief)
        except ProviderError:
            signals = TechnicalSignals(reason="browse-failed")
        return signals, {"status": signals.status, "reason": signals.reason or ""}

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
