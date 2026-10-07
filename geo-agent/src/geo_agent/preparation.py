from typing import Literal, Protocol

from geo_agent.contracts import Brief, Contract, MeasurementInputs, ModelCall, PageSnapshot, Provenance, QueryPlan
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.foundry import PageAnalysis
from geo_agent.jobs import ClaimedOperationRunner, JobType, WorkflowJob
from geo_agent.measurement_workflow import MeasurementEvent, MeasurementRun, MeasurementState, Mutation
from geo_agent.webiq import ProviderError, ProviderFailure
from geo_agent.workflow import Conflict


class PreparationRequest(Contract):
    brief: Brief
    confirm_preparation_calls: Literal[True]
    project_bound: bool = False


class BrowseProvider(Protocol):
    def browse(self, brief: Brief) -> PageSnapshot: ...


class PageAnalysisProvider(Protocol):
    def analyse_preparation(self, snapshot: PageSnapshot, passages: list[dict]) -> tuple[PageAnalysis, dict]: ...


class QueryPlanProvider(Protocol):
    def propose_pairs(self, brief: Brief, snapshot: PageSnapshot) -> tuple[QueryPlan, dict]: ...


class PreparationHandler:
    def __init__(
        self,
        policy: MeasurementExecutionPolicy,
        browse: BrowseProvider,
        analysis: PageAnalysisProvider,
        query_planner: QueryPlanProvider,
        fallback_query_planner: QueryPlanProvider | None = None,
        query_planner_operation_type: str = "paired-query-plan",
    ):
        self.policy = policy
        self.browse = browse
        self.analysis = analysis
        self.query_planner = query_planner
        self.fallback_query_planner = fallback_query_planner
        self.query_planner_operation_type = query_planner_operation_type

    @staticmethod
    def _metadata(value: dict) -> dict[str, str | int | bool | None]:
        return {
            str(key): item
            for key, item in value.items()
            if isinstance(item, (str, int, bool)) or item is None
        }

    def __call__(self, job: WorkflowJob, operations: ClaimedOperationRunner) -> Mutation:
        if job.job_type != JobType.PREPARE:
            raise Conflict("Preparation handler requires a preparation job")
        if (
            job.policy_id is not None
            and (
                job.policy_id != self.policy.policy_id
                or job.policy_hash != self.policy.policy_hash
            )
        ):
            raise Conflict("Preparation job does not match the worker execution policy")
        request = PreparationRequest.model_validate(job.request)
        self.policy.validate_brief(request.brief, project_bound=request.project_bound)
        if request.project_bound and self.policy.execution_mode != "live":
            raise Conflict("Project measurements require a live execution policy")

        def browse_call() -> tuple[PageSnapshot, dict[str, str | int | bool | None]]:
            snapshot = self.browse.browse(request.brief)
            return snapshot, {
                "provider": "fixture" if self.policy.execution_mode == "mock" else "webiq",
                "trace_id": snapshot.provider_trace_id,
            }

        snapshot = operations.call(
            f"{job.job_id}:browse",
            "webiq-browse",
            browse_call,
        )
        expected_provenance = Provenance.LIVE if request.project_bound else (
            Provenance.SYNTHETIC if self.policy.execution_mode == "mock" else Provenance.LIVE
        )
        if snapshot.url != request.brief.url or snapshot.provenance != expected_provenance:
            raise ProviderError("Browse evidence does not match the preparation policy")
        passages = [
            {
                "passage_id": f"page-{offset // 1000 + 1}",
                "url": str(snapshot.url),
                "text": snapshot.content[offset:offset + 1000],
            }
            for offset in range(0, min(len(snapshot.content), 10000), 1000)
        ]

        page_analysis = None
        page_analysis_unavailable = False
        try:
            page_analysis = operations.call(
                f"{job.job_id}:page-analysis",
                "page-analysis-model",
                lambda: self._analyse(snapshot, passages),
            )
        except ProviderError:
            page_analysis_unavailable = True
        query_plan, query_metadata, fallback_used = self._plan_with_metadata(
            job,
            operations,
            request.brief,
            snapshot,
        )
        prepared = MeasurementInputs(
            brief=request.brief,
            snapshot=snapshot,
            query_plan=query_plan,
            profiles=self.policy.profiles,
            policy_hash=self.policy.policy_hash,
            query_generation=ModelCall.model_validate(query_metadata),
        )

        def mutation(run: MeasurementRun) -> MeasurementRun:
            if run.state != MeasurementState.PREPARING or run.brief != request.brief:
                raise Conflict("Preparation result no longer matches the run")
            events = run.events
            if page_analysis_unavailable:
                events = (*events, MeasurementEvent(
                    sequence=len(events) + 1,
                    event_type="page-analysis-unavailable",
                ))
            if fallback_used:
                events = (*events, MeasurementEvent(
                    sequence=len(events) + 1,
                    event_type="query-plan-fallback-used",
                ))
            return run.model_copy(update={
                "inputs": prepared,
                "page_analysis": page_analysis,
                "state": MeasurementState.AWAITING_APPROVAL,
                "events": (*events, MeasurementEvent(
                    sequence=len(events) + 1,
                    event_type="awaiting-query-approval",
                )),
            })

        return mutation

    def _analyse(self, snapshot: PageSnapshot, passages: list[dict]):
        analyse = getattr(self.analysis, "analyse_preparation", None)
        if analyse is None:
            analyse = self.analysis.analyse_page
        report, metadata = analyse(snapshot, passages)
        return report, self._metadata(metadata)

    def _plan_with_metadata(
        self,
        job: WorkflowJob,
        operations: ClaimedOperationRunner,
        brief: Brief,
        snapshot: PageSnapshot,
    ) -> tuple[QueryPlan, dict[str, str | int | bool | None], bool]:
        def claimed_plan(
            provider: QueryPlanProvider,
            operation_key: str,
            operation_type: str,
        ) -> tuple[QueryPlan, dict[str, str | int | bool | None]]:
            captured: dict[str, str | int | bool | None] = {}

            def plan_call():
                try:
                    plan, metadata = provider.propose_pairs(brief, snapshot)
                    plan = QueryPlan.model_validate(plan)
                    if [query.priority for query in plan.queries] != list(range(1, 6)):
                        raise ProviderError(
                            "Query planner returned an invalid query plan order",
                            code=ProviderFailure.PLAN_ORDER,
                        )
                    plan.validate_evidence(snapshot)
                    model_call = ModelCall.model_validate(self._metadata(metadata))
                except ProviderError:
                    raise
                except ValueError:
                    raise ProviderError(
                        "Query planner returned an invalid query plan",
                        code=ProviderFailure.SCHEMA,
                    ) from None
                captured.update(model_call.model_dump(mode="json"))
                return plan, captured

            plan = operations.call(
                operation_key,
                operation_type,
                plan_call,
            )
            return plan, captured

        primary_key = (
            f"{job.job_id}:query-plan"
            if self.query_planner_operation_type == "paired-query-plan"
            else f"{job.job_id}:query-plan:missions"
        )
        try:
            plan, metadata = claimed_plan(
                self.query_planner,
                primary_key,
                self.query_planner_operation_type,
            )
            return plan, metadata, False
        except ProviderError:
            if self.fallback_query_planner is None or self.policy.max_query_plan_calls < 2:
                raise
        plan, metadata = claimed_plan(
            self.fallback_query_planner,
            f"{job.job_id}:query-plan:fallback",
            "paired-query-plan-fallback",
        )
        return plan, metadata, True