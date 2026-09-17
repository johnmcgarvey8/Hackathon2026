from typing import Literal, Protocol

from geo_agent.contracts import Brief, Contract, MeasurementInputs, ModelCall, PageSnapshot, Provenance, QueryPlan
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.foundry import PreparationAnalysis
from geo_agent.jobs import ClaimedOperationRunner, JobType, WorkflowJob
from geo_agent.measurement_workflow import MeasurementEvent, MeasurementRun, MeasurementState, Mutation
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict


class PreparationRequest(Contract):
    brief: Brief
    confirm_preparation_calls: Literal[True]


class BrowseProvider(Protocol):
    def browse(self, brief: Brief) -> PageSnapshot: ...


class PageAnalysisProvider(Protocol):
    def analyse_preparation(self, snapshot: PageSnapshot, passages: list[dict]) -> tuple[PreparationAnalysis, dict]: ...


class QueryPlanProvider(Protocol):
    def propose_pairs(self, brief: Brief, snapshot: PageSnapshot) -> tuple[QueryPlan, dict]: ...


class PreparationHandler:
    def __init__(
        self,
        policy: MeasurementExecutionPolicy,
        browse: BrowseProvider,
        analysis: PageAnalysisProvider,
        query_planner: QueryPlanProvider,
    ):
        self.policy = policy
        self.browse = browse
        self.analysis = analysis
        self.query_planner = query_planner

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
        request = PreparationRequest.model_validate(job.request)
        self.policy.validate_brief(request.brief)

        def browse_call() -> tuple[PageSnapshot, dict[str, str | int | bool | None]]:
            snapshot = self.browse.browse(request.brief)
            return snapshot, {
                "provider": "fixture" if self.policy.execution_mode == "mock" else "webiq",
                "trace_id": snapshot.provider_trace_id,
            }

        snapshot = operations.call(
            f"{job.idempotency_key}:browse",
            "webiq-browse",
            browse_call,
        )
        expected_provenance = Provenance.SYNTHETIC if self.policy.execution_mode == "mock" else Provenance.LIVE
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

        page_analysis = operations.call(
            f"{job.idempotency_key}:page-analysis",
            "page-analysis-model",
            lambda: self._analyse(snapshot, passages),
        )
        query_plan, query_metadata = self._plan_with_metadata(job, operations, request.brief, snapshot)
        query_plan.validate_evidence(snapshot)
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
            return run.model_copy(update={
                "inputs": prepared,
                "page_analysis": page_analysis,
                "state": MeasurementState.AWAITING_APPROVAL,
                "events": (*run.events, MeasurementEvent(
                    sequence=len(run.events) + 1,
                    event_type="awaiting-query-approval",
                )),
            })

        return mutation

    def _analyse(self, snapshot: PageSnapshot, passages: list[dict]):
        report, metadata = self.analysis.analyse_preparation(snapshot, passages)
        return report, self._metadata(metadata)

    def _plan_with_metadata(
        self,
        job: WorkflowJob,
        operations: ClaimedOperationRunner,
        brief: Brief,
        snapshot: PageSnapshot,
    ) -> tuple[QueryPlan, dict[str, str | int | bool | None]]:
        captured: dict[str, str | int | bool | None] = {}

        def plan_call():
            plan, metadata = self.query_planner.propose_pairs(brief, snapshot)
            captured.update(self._metadata(metadata))
            return plan, captured

        plan = operations.call(
            f"{job.idempotency_key}:query-plan",
            "paired-query-plan",
            plan_call,
        )
        return plan, captured