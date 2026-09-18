from threading import Event, Lock
from urllib.parse import urlsplit

from geo_agent.contracts import (
    Brief,
    EvaluationResult,
    EvidenceQuote,
    MeasurementResults,
    PageSnapshot,
    QueryPair,
    QueryPlan,
    SimulationProfile,
    Source,
)
from geo_agent.evaluation_workflow import EvaluationHandler
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.evidence_assessment import BrandAlias, BrandDefinition
from geo_agent.foundry import InferredBrand, PreparationAnalysis, PageEvidence, PageFinding, PageImprovement
from geo_agent.jobs import JobRepository, JobType
from geo_agent.preparation import PreparationHandler
from geo_agent.recommendations import RecommendationProposal, RecommendationReport, validate_recommendations
from geo_agent.worker import Worker


class SyntheticBrowse:
    def browse(self, brief: Brief) -> PageSnapshot:
        brand = "Microsoft Clarity" if urlsplit(str(brief.url)).hostname == "clarity.microsoft.com" else "Example Analytics"
        return PageSnapshot(
            url=brief.url,
            title="Synthetic GEO measurement page",
            content=(
                f"{brand}. Synthetic page fixture for {brief.audience}. The stated goal is {brief.goal}. "
                "The page describes practical comparison criteria, supporting evidence, and next steps. "
                "This content was generated locally for workflow testing and was not retrieved from the web."
            ),
            provenance="synthetic",
            provider_trace_id="local-synthetic-browse",
            content_format="text/plain",
        )


class SyntheticPreparationModel:
    @staticmethod
    def _quote(snapshot: PageSnapshot) -> str:
        return snapshot.content[:200]

    def analyse_preparation(self, snapshot: PageSnapshot, _passages: list[dict]) -> tuple[PreparationAnalysis, dict]:
        evidence = [PageEvidence(passage_id="page-1", quote=self._quote(snapshot))]
        purpose = PageFinding(
            text="The fixture supports a bounded GEO workflow demonstration.",
            basis="observed",
            evidence=evidence,
        )
        brand = "Microsoft Clarity" if snapshot.content.startswith("Microsoft Clarity.") else "Example Analytics"
        analysis = PreparationAnalysis(
            brand=InferredBrand(
                definition=BrandDefinition(name=brand,
                    aliases=(BrandAlias(text="Clarity", ambiguous=True),) if brand == "Microsoft Clarity" else (),
                    domains=(urlsplit(str(snapshot.url)).hostname,)),
                evidence=evidence,
                rationale="Synthetic brand inference from the saved fixture; no model or web request was made.",
            ),
            purpose=purpose,
            audience=PageFinding(
                text="The intended audience comes from the submitted brief.",
                basis="inferred",
                evidence=evidence,
            ),
            entities=[],
            questions_answered=[purpose],
            observations=[],
            improvements=[PageImprovement(
                hypothesis="Add reviewed evidence for each important comparison claim.",
                rationale="The synthetic excerpt contains only general comparison guidance.",
                evidence=evidence,
                verification="Review the real page and repeat an approved measurement.",
            )],
        )
        return analysis, {"model": "local-synthetic-analysis", "response_id": "synthetic-analysis-1"}

    def propose_pairs(self, brief: Brief, snapshot: PageSnapshot) -> tuple[QueryPlan, dict]:
        quote = self._quote(snapshot)
        queries = tuple(QueryPair(
            query_id=f"q-{index}",
            priority=index,
            rationale=f"Synthetic discovery scenario {index} for the approved goal.",
            intent=f"Evaluate discovery intent {index}",
            branded=False,
            chat_query=f"What evidence should buyers review for option {index}?",
            grounding_query=f"buyer evidence comparison option {index}",
            evidence=(EvidenceQuote(evidence_id="page-1", quote=quote),),
        ) for index in range(1, 6))
        return QueryPlan(queries=queries), {
            "model": "local-synthetic-query-planner",
            "response_id": "synthetic-query-plan-1",
        }


class SyntheticSearch:
    def search(self, query, _locale: str) -> tuple[Source, ...]:
        return (
            Source(
                evidence_id=f"{query.query_id}-comparison",
                url=f"https://example.net/comparison/{query.query_id}",
                title="Synthetic comparison source",
                excerpt="Synthetic comparison evidence for local workflow testing.",
                provenance="synthetic",
                returned_position=1,
                provider_trace_id=f"synthetic-search-{query.query_id}",
            ),
            Source(
                evidence_id=f"{query.query_id}-target",
                url="https://example.com/mock-page",
                title="Synthetic target source",
                excerpt="Synthetic target-page evidence for local workflow testing.",
                provenance="synthetic",
                returned_position=2,
                provider_trace_id=f"synthetic-search-{query.query_id}",
            ),
        )


class SyntheticEvaluator:
    def __init__(self, profile: SimulationProfile):
        self._profile = profile

    @property
    def profile(self) -> SimulationProfile:
        return self._profile

    def evaluate(self, query, _locale: str, sources: tuple[Source, ...]) -> EvaluationResult:
        source = sources[1] if int(query.query_id.removeprefix("q-")) <= 2 else sources[0]
        return EvaluationResult(
            query_id=query.query_id,
            profile_id=self.profile.profile_id,
            provenance="synthetic",
            status="completed",
            answer=f"Synthetic answer for {query.query_id}; no provider was called [{source.evidence_id}].",
            citation_ids=(source.evidence_id,),
            sources=sources,
            model=f"local-{self.profile.profile_id}",
            response_id=f"synthetic-{query.query_id}-{self.profile.profile_id}",
        )


class SyntheticRecommendations:
    def recommend(self, measurement: MeasurementResults) -> RecommendationReport:
        return validate_recommendations(
            measurement,
            RecommendationProposal(
                tasks=(),
                reason="Synthetic evidence is insufficient for draft content recommendations.",
            ),
        )


class MockMeasurementRuntime:
    def __init__(self, repository: JobRepository, policy: MeasurementExecutionPolicy):
        if policy.execution_mode != "mock":
            raise ValueError("The local mock runtime requires a mock execution policy")
        preparation = SyntheticPreparationModel()
        self.worker = Worker(repository, "local-mock-worker", {
            JobType.PREPARE: PreparationHandler(policy, SyntheticBrowse(), preparation, preparation),
            JobType.EVALUATE: EvaluationHandler(
                repository,
                policy,
                SyntheticSearch(),
                tuple(SyntheticEvaluator(profile) for profile in policy.profiles),
                SyntheticRecommendations(),
            ),
        }, policy_id=policy.policy_id, policy_hash=policy.policy_hash)
        self._lock = Lock()
        self._pending = Event()

    def drain(self) -> None:
        self._pending.set()
        while True:
            if not self._lock.acquire(blocking=False):
                return
            try:
                while True:
                    self._pending.clear()
                    while self.worker.run_once() is not None:
                        pass
                    if not self._pending.is_set():
                        break
            finally:
                self._lock.release()
            if not self._pending.is_set():
                return