from typing import Literal, Protocol

from geo_agent.contracts import (
    Contract,
    EvaluationResult,
    MeasurementResults,
    RetrievalResult,
    SimulationProfile,
    Source,
)
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import ClaimedOperationRunner, JobType, WorkflowJob
from geo_agent.measurement_workflow import MeasurementEvent, MeasurementRepository, MeasurementRun, MeasurementState, Mutation
from geo_agent.recommendations import RecommendationReport
from geo_agent.workflow import Conflict


class EvaluationRequest(Contract):
    confirm_evaluation_calls: Literal[True]
    include_recommendations: bool = False


class SearchProvider(Protocol):
    def search(self, query, locale: str) -> tuple[Source, ...]: ...


class Evaluator(Protocol):
    @property
    def profile(self) -> SimulationProfile: ...

    def evaluate(self, query, locale: str, sources: tuple[Source, ...]) -> EvaluationResult: ...


class RecommendationProvider(Protocol):
    def recommend(self, measurement: MeasurementResults) -> RecommendationReport: ...


class EvaluationHandler:
    def __init__(
        self,
        repository: MeasurementRepository,
        policy: MeasurementExecutionPolicy,
        search: SearchProvider,
        evaluators: tuple[Evaluator, ...],
        recommendations: RecommendationProvider | None = None,
    ):
        self.repository = repository
        self.policy = policy
        self.search = search
        self.evaluators = {evaluator.profile.profile_id: evaluator for evaluator in evaluators}
        self.recommendations = recommendations

    def __call__(self, job: WorkflowJob, operations: ClaimedOperationRunner) -> Mutation:
        if job.job_type != JobType.EVALUATE:
            raise Conflict("Evaluation handler requires an evaluation job")
        request = EvaluationRequest.model_validate(job.request)
        run = self.repository.get(job.run_id, job.owner)
        if run.state != MeasurementState.EVALUATING or run.inputs is None or run.approval is None:
            raise Conflict("Evaluation requires leased, approved measurement inputs")
        if (
            run.inputs.policy_hash != self.policy.policy_hash
            or run.inputs.profiles != self.policy.profiles
            or run.approval.input_hash != run.inputs.approval_hash
        ):
            raise Conflict("Evaluation inputs do not match the execution policy and approval")
        if set(self.evaluators) != {profile.profile_id for profile in run.inputs.profiles}:
            raise Conflict("Evaluation requires exactly the approved profile roster")

        retrievals: list[RetrievalResult] = []
        results: list[EvaluationResult] = []
        for pair in sorted(run.inputs.query_plan.queries, key=lambda item: item.priority):
            grounding_query = pair.as_query(grounding=True)
            try:
                retrieval = operations.call(
                    f"{job.idempotency_key}:search:{pair.query_id}",
                    "webiq-search",
                    lambda pair=pair, query=grounding_query: self._search_call(
                        pair,
                        query,
                        run.inputs.brief.locale,
                        run.inputs.snapshot.provenance,
                    ),
                )
            except Exception:
                retrieval = RetrievalResult(
                    query_id=pair.query_id,
                    grounding_query=pair.grounding_query,
                    provenance=run.inputs.snapshot.provenance,
                    status="error",
                    error="Retrieval failed; no automatic retry.",
                )
            retrievals.append(retrieval)
            for profile in run.inputs.profiles:
                if retrieval.status == "error":
                    results.append(self._error_result(
                        pair.query_id,
                        profile,
                        (),
                        run.inputs.snapshot.provenance,
                        "Retrieval failed; evaluator not called.",
                    ))
                    continue
                evaluator = self.evaluators[profile.profile_id]
                try:
                    result = operations.call(
                        f"{job.idempotency_key}:evaluate:{pair.query_id}:{profile.profile_id}",
                        "profile-evaluator",
                        lambda pair=pair, evaluator=evaluator, sources=retrieval.sources: self._evaluate_call(
                            evaluator,
                            pair.as_query(),
                            run.inputs.brief.locale,
                            sources,
                            run.inputs.snapshot.provenance,
                        ),
                    )
                except Exception:
                    result = self._error_result(
                        pair.query_id,
                        profile,
                        retrieval.sources,
                        run.inputs.snapshot.provenance,
                        "Evaluator failed; no automatic retry.",
                    )
                results.append(result)

        measurement = MeasurementResults(
            inputs=run.inputs,
            retrievals=tuple(retrievals),
            results=tuple(results),
        )
        completed = sum(result.status == "completed" for result in measurement.results)
        state = (
            MeasurementState.READY
            if completed == len(measurement.results)
            else MeasurementState.PARTIAL
            if completed
            else MeasurementState.FAILED
        )
        report = None
        if request.include_recommendations and self.recommendations is not None and completed:
            try:
                report = operations.call(
                    f"{job.idempotency_key}:recommend",
                    "recommendation-model",
                    lambda: self._recommendation_call(measurement),
                )
            except Exception:
                state = MeasurementState.PARTIAL
                report = None

        def mutation(current: MeasurementRun) -> MeasurementRun:
            if current.state != MeasurementState.EVALUATING or current.inputs != measurement.inputs:
                raise Conflict("Evaluation result no longer matches the run")
            return current.model_copy(update={
                "measurement": measurement,
                "recommendations": report,
                "recommendation_review": None,
                "state": state,
                "events": (*current.events, MeasurementEvent(
                    sequence=len(current.events) + 1,
                    event_type=state.value,
                )),
            })

        return mutation

    def _search_call(self, pair, query, locale: str, provenance):
        sources = self.search.search(query, locale)
        retrieval = RetrievalResult(
            query_id=pair.query_id,
            grounding_query=pair.grounding_query,
            provenance=provenance,
            status="completed",
            sources=sources,
            provider_trace_id=next((source.provider_trace_id for source in sources if source.provider_trace_id), None),
        )
        return retrieval, {
            "provider": "fixture" if self.policy.execution_mode == "mock" else "webiq",
            "query_id": pair.query_id,
            "result_count": len(sources),
        }

    @staticmethod
    def _evaluate_call(evaluator: Evaluator, query, locale: str, sources: tuple[Source, ...], provenance):
        result = evaluator.evaluate(query, locale, sources)
        if (
            result.query_id != query.query_id
            or result.profile_id != evaluator.profile.profile_id
            or result.sources != sources
            or result.provenance != provenance
        ):
            raise ValueError("Evaluator result does not match the approved packet")
        return result, {
            "provider": evaluator.profile.provider,
            "profile_id": evaluator.profile.profile_id,
            "model": result.model,
            "response_id": result.response_id,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }

    @staticmethod
    def _error_result(
        query_id: str,
        profile: SimulationProfile,
        sources: tuple[Source, ...],
        provenance,
        error: str,
    ) -> EvaluationResult:
        return EvaluationResult(
            query_id=query_id,
            profile_id=profile.profile_id,
            provenance=provenance,
            status="error",
            sources=sources,
            error=error,
        )

    def _recommendation_call(self, measurement: MeasurementResults):
        if self.recommendations is None:
            raise Conflict("Recommendation provider is not configured")
        report = self.recommendations.recommend(measurement)
        report.validate_for(measurement)
        metadata = report.model_call.model_dump(mode="json") if report.model_call else {"provider": "deterministic"}
        return report, metadata