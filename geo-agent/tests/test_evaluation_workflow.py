import sqlite3

from geo_agent.contracts import EvaluationResult, Source
from geo_agent.evaluation_workflow import EvaluationHandler, EvaluationRequest
from geo_agent.jobs import JobService, JobState, JobType
from geo_agent.measurement_workflow import MeasurementCoordinator, MeasurementState, OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.recommendations import RecommendationProposal, validate_recommendations
from geo_agent.worker import Worker
from test_measurement_workflow import inputs
from test_preparation import policy


class FakeSearch:
    def __init__(self, fail_query=None):
        self.calls = []
        self.fail_query = fail_query

    def search(self, query, _locale):
        self.calls.append(query.query_id)
        if query.query_id == self.fail_query:
            raise RuntimeError("sensitive retrieval failure")
        return (Source(
            evidence_id=f"{query.query_id}-source-1",
            url=f"https://example.net/{query.query_id}",
            title="Comparison",
            excerpt=f"Comparison evidence for {query.query_id}",
            provenance="synthetic",
            returned_position=1,
            provider_trace_id=f"search-{query.query_id}",
        ),)


class FakeEvaluator:
    def __init__(self, profile, fail_query=None):
        self._profile = profile
        self.fail_query = fail_query
        self.calls = []

    @property
    def profile(self):
        return self._profile

    def evaluate(self, query, _locale, sources):
        self.calls.append(query.query_id)
        if query.query_id == self.fail_query:
            raise RuntimeError("sensitive fixture failure")
        return EvaluationResult(
            query_id=query.query_id,
            profile_id=self.profile.profile_id,
            provenance="synthetic",
            status="completed",
            answer=f"Answer for {query.query_id}",
            citation_ids=(sources[0].evidence_id,),
            sources=sources,
            model="fixture-model",
            response_id=f"response-{query.query_id}",
        )


class FakeRecommendations:
    def __init__(self):
        self.calls = 0

    def recommend(self, measurement):
        self.calls += 1
        return validate_recommendations(
            measurement,
            RecommendationProposal(tasks=(), reason="No draft changes selected by the fixture."),
        )


def three_profiles():
    base = inputs().profiles[0]
    return (
        base,
        base.model_copy(update={
            "profile_id": "claude-backed",
            "provider": "anthropic-messages",
            "deployment": "test-claude-deployment",
        }),
        base.model_copy(update={
            "profile_id": "copilot-style",
            "deployment": "test-copilot-deployment",
        }),
    )


def test_evaluation_reuses_five_packets_and_isolates_profile_failure(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "evaluate.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash
    )
    request = EvaluationRequest(confirm_evaluation_calls=True)
    job, _ = JobService(repository).enqueue(
        approved.run_id, owner, approved.revision, JobType.EVALUATE, "evaluate-1", request
    )
    search = FakeSearch()
    evaluator = FakeEvaluator(prepared.profiles[0], fail_query="q-3")

    result = Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(
            repository,
            execution_policy,
            search,
            (evaluator,),
        )},
    ).run_once()
    assert result is not None
    completed_job, completed_run = result

    assert completed_job.job_id == job.job_id
    assert completed_job.state == JobState.COMPLETED
    assert completed_run.state == MeasurementState.PARTIAL
    assert completed_run.measurement is not None
    assert len(completed_run.measurement.retrievals) == 5
    assert len(completed_run.measurement.results) == 5
    assert [item.query_id for item in completed_run.measurement.results if item.status == "error"] == ["q-3"]
    assert search.calls == ["q-1", "q-2", "q-3", "q-4", "q-5"]
    assert evaluator.calls == ["q-1", "q-2", "q-3", "q-4", "q-5"]
    for result_item in completed_run.measurement.results:
        packet = next(item for item in completed_run.measurement.retrievals if item.query_id == result_item.query_id)
        assert result_item.sources == packet.sources
    with sqlite3.connect(tmp_path / "evaluate.sqlite3") as connection:
        claims = connection.execute("SELECT operation_key, state, payload FROM operation_claims").fetchall()
    assert len(claims) == 10
    assert sum(state == "completed" for _, state, _ in claims) == 9
    failed_payload = next(payload for key, state, payload in claims if state == "failed")
    assert "RuntimeError" in failed_payload
    assert "sensitive fixture failure" not in failed_payload


def test_three_profiles_share_five_packets_across_fifteen_evaluations(tmp_path):
    profiles = three_profiles()
    execution_policy = policy().model_copy(update={"profiles": profiles})
    prepared = inputs().model_copy(update={
        "profiles": profiles,
        "policy_hash": execution_policy.policy_hash,
    })
    repository = SQLiteMeasurementRepository(tmp_path / "three-profiles.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash
    )
    job, _ = JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-1",
        EvaluationRequest(confirm_evaluation_calls=True),
    )
    evaluators = tuple(
        FakeEvaluator(profile, fail_query="q-3" if profile.profile_id == "claude-backed" else None)
        for profile in profiles
    )

    result = Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(repository, execution_policy, FakeSearch(), evaluators)},
    ).run_once()
    assert result is not None
    _, completed_run = result

    assert completed_run.state == MeasurementState.PARTIAL
    assert completed_run.measurement is not None
    assert len(completed_run.measurement.retrievals) == 5
    assert len(completed_run.measurement.results) == 15
    assert sum(item.status == "error" for item in completed_run.measurement.results) == 1
    assert all(evaluator.calls == ["q-1", "q-2", "q-3", "q-4", "q-5"] for evaluator in evaluators)
    for packet in completed_run.measurement.retrievals:
        packet_results = tuple(
            item for item in completed_run.measurement.results if item.query_id == packet.query_id
        )
        assert len(packet_results) == 3
        assert all(item.sources == packet.sources for item in packet_results)
    with sqlite3.connect(tmp_path / "three-profiles.sqlite3") as connection:
        claims = connection.execute("SELECT operation_key, state FROM operation_claims").fetchall()
    search_claims = [key for key, _ in claims if ":search:" in key]
    evaluator_claims = [key for key, _ in claims if ":evaluate:" in key]
    assert len(search_claims) == 5
    assert len(evaluator_claims) == 15
    assert sum(state == "failed" for _, state in claims) == 1


def test_retrieval_failure_skips_only_its_evaluator_and_later_queries_continue(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "retrieval-failure.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-1",
        EvaluationRequest(confirm_evaluation_calls=True),
    )
    search = FakeSearch(fail_query="q-2")
    evaluator = FakeEvaluator(prepared.profiles[0])

    result = Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(repository, execution_policy, search, (evaluator,))},
    ).run_once()
    assert result is not None
    _, completed_run = result

    assert search.calls == ["q-1", "q-2", "q-3", "q-4", "q-5"]
    assert evaluator.calls == ["q-1", "q-3", "q-4", "q-5"]
    assert completed_run.measurement is not None
    failed_packet = next(item for item in completed_run.measurement.retrievals if item.query_id == "q-2")
    failed_answer = next(item for item in completed_run.measurement.results if item.query_id == "q-2")
    assert failed_packet.status == "error"
    assert failed_answer.error == "Retrieval failed; evaluator not called."


def test_recommendations_are_invoked_once_after_complete_measurement(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "recommend.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash
    )
    job, _ = JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-1",
        EvaluationRequest(confirm_evaluation_calls=True, include_recommendations=True),
    )
    recommendations = FakeRecommendations()

    result = Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(
            repository,
            execution_policy,
            FakeSearch(),
            (FakeEvaluator(prepared.profiles[0]),),
            recommendations,
        )},
    ).run_once()
    assert result is not None
    _, completed_run = result

    assert recommendations.calls == 1
    assert completed_run.state == MeasurementState.READY
    assert completed_run.recommendations is not None
    assert completed_run.recommendations.status == "insufficient-evidence"
    with sqlite3.connect(tmp_path / "recommend.sqlite3") as connection:
        recommendation_claims = connection.execute(
            "SELECT COUNT(*) FROM operation_claims WHERE operation_key = ?",
            (f"{job.job_id}:recommend",),
        ).fetchone()[0]
    assert recommendation_claims == 1


def test_recommendations_are_not_invoked_without_explicit_request(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "recommend-disabled.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-1",
        EvaluationRequest(confirm_evaluation_calls=True),
    )
    recommendations = FakeRecommendations()

    Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(
            repository,
            execution_policy,
            FakeSearch(),
            (FakeEvaluator(prepared.profiles[0]),),
            recommendations,
        )},
    ).run_once()

    assert recommendations.calls == 0
    with sqlite3.connect(tmp_path / "recommend-disabled.sqlite3") as connection:
        recommendation_claims = connection.execute(
            "SELECT COUNT(*) FROM operation_claims WHERE operation_key LIKE '%:recommend'"
        ).fetchone()[0]
    assert recommendation_claims == 0