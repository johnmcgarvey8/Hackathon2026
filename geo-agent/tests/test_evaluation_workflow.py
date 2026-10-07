import sqlite3

import pytest

from geo_agent.contracts import EvaluationResult, Source
from geo_agent.evaluation_workflow import (
    EvaluationHandler,
    EvaluationRequest,
    EvaluatorRecoveryRequest,
)
from geo_agent.jobs import JobService, JobState, JobType
from geo_agent.measurement_workflow import MeasurementCoordinator, MeasurementState, OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.project_measurements import ProjectMeasurementOrchestrator
from geo_agent.recommendations import RecommendationProposal, validate_recommendations
from geo_agent.specialist_agents import (
    AgentStageStatus,
    SpecialistAgentRole,
    SpecialistProviderMode,
)
from geo_agent.specialist_foundry import HostedSpecialistAgent
from geo_agent.specialist_workflow import SpecialistStageHandler, SpecialistStageRequest
from geo_agent.worker import Worker
from geo_agent.workflow import Conflict
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


def test_evaluator_recovery_reuses_saved_searches_and_only_replaces_failed_answers(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "recover.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-initial",
        EvaluationRequest(confirm_evaluation_calls=True),
    )
    initial_search = FakeSearch()
    Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(
            repository,
            execution_policy,
            initial_search,
            (FakeEvaluator(prepared.profiles[0], fail_query="q-3"),),
        )},
    ).run_once()
    failed = repository.get(created.run_id, owner)
    assert failed.state == MeasurementState.PARTIAL

    JobService(repository).enqueue(
        failed.run_id,
        owner,
        failed.revision,
        JobType.RECOVER_EVALUATORS,
        "recover-evaluators",
        EvaluatorRecoveryRequest(confirm_evaluation_calls=True),
    )
    recovery_search = FakeSearch()
    recovery_evaluator = FakeEvaluator(prepared.profiles[0])
    Worker(
        repository,
        "worker-b",
        {JobType.RECOVER_EVALUATORS: EvaluationHandler(
            repository,
            execution_policy,
            recovery_search,
            (recovery_evaluator,),
        )},
    ).run_once()
    recovered = repository.get(created.run_id, owner)
    assert recovered.state == MeasurementState.READY
    assert recovery_search.calls == []
    assert recovery_evaluator.calls == ["q-3"]
    assert all(result.status == "completed" for result in recovered.measurement.results)
    assert recovered.measurement.retrievals == failed.measurement.retrievals


def test_evaluator_recovery_may_correct_only_the_provider_endpoint(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "endpoint-recovery.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-endpoint",
        EvaluationRequest(confirm_evaluation_calls=True),
    )
    Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(
            repository,
            execution_policy,
            FakeSearch(),
            (FakeEvaluator(prepared.profiles[0], fail_query="q-1"),),
        )},
    ).run_once()
    failed = repository.get(created.run_id, owner)
    corrected_profile = prepared.profiles[0].model_copy(update={
        "endpoint": "https://corrected.services.ai.azure.com/openai/v1/",
    })
    corrected_policy = execution_policy.model_copy(update={
        "profiles": (corrected_profile,),
        "budget_grant_id": "recovery-grant",
    })
    JobService(repository).enqueue(
        failed.run_id,
        owner,
        failed.revision,
        JobType.RECOVER_EVALUATORS,
        "recover-endpoint",
        EvaluatorRecoveryRequest(confirm_evaluation_calls=True),
    )
    evaluator = FakeEvaluator(corrected_profile)
    Worker(
        repository,
        "worker-b",
        {JobType.RECOVER_EVALUATORS: EvaluationHandler(
            repository,
            corrected_policy,
            FakeSearch(),
            (evaluator,),
        )},
    ).run_once()
    recovered = repository.get(created.run_id, owner)
    assert recovered.state == MeasurementState.READY
    assert evaluator.calls == ["q-1"]


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


def test_project_evaluation_rejects_synthetic_inputs_before_provider_calls(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "project-synthetic-evaluation.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "project-synthetic-evaluation",
        EvaluationRequest(confirm_evaluation_calls=True, project_bound=True),
    )
    search = FakeSearch()
    evaluator = FakeEvaluator(prepared.profiles[0])

    job, run = Worker(
        repository,
        "worker-a",
        {
            JobType.EVALUATE: EvaluationHandler(
                repository,
                execution_policy,
                search,
                (evaluator,),
            ),
        },
    ).run_once()

    assert job.state == JobState.FAILED
    assert job.error_code == "Conflict"
    assert run.state == MeasurementState.NEEDS_REVIEW
    assert search.calls == []
    assert evaluator.calls == []


def test_recommendations_run_once_in_independent_baseline_stage(tmp_path):
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

    assert recommendations.calls == 0
    assert completed_run.state == MeasurementState.READY
    assert completed_run.recommendations is None

    stage_job, queued_run = JobService(repository).enqueue(
        completed_run.run_id,
        owner,
        completed_run.revision,
        JobType.AGENT_STAGE,
        "recommendations-1",
        SpecialistStageRequest(
            role=SpecialistAgentRole.RECOMMENDATIONS,
            provider_mode=SpecialistProviderMode.BASELINE_PROVIDER,
            confirm_provider_call=True,
        ),
        operation_ceiling=1,
    )
    assert queued_run.state == MeasurementState.READY
    assert queued_run.agent_stages[0].status == AgentStageStatus.QUEUED
    stage_result = Worker(
        repository,
        "worker-a",
        {
            JobType.AGENT_STAGE: SpecialistStageHandler(
                repository,
                recommendations,
                HostedSpecialistAgent(token_provider=lambda: "unused"),
            ),
        },
    ).run_once()
    assert stage_result is not None
    _, completed_run = stage_result

    assert recommendations.calls == 1
    assert completed_run.state == MeasurementState.READY
    assert completed_run.recommendations is not None
    assert completed_run.recommendations.status == "insufficient-evidence"
    assert completed_run.agent_stages[0].status == AgentStageStatus.COMPLETED
    assert completed_run.agent_stages[0].provider_mode == SpecialistProviderMode.BASELINE_PROVIDER
    with sqlite3.connect(tmp_path / "recommend.sqlite3") as connection:
        recommendation_claims = connection.execute(
            "SELECT COUNT(*) FROM operation_claims WHERE operation_key = ?",
            (f"{stage_job.job_id}:recommend",),
        ).fetchone()[0]
    assert recommendation_claims == 1


def test_recommendation_retry_replay_returns_existing_job(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "recommend-retry.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-retry",
        EvaluationRequest(confirm_evaluation_calls=True, include_recommendations=True),
    )
    completed_job, completed_run = Worker(
        repository,
        "worker-a",
        {
            JobType.EVALUATE: EvaluationHandler(
                repository,
                execution_policy,
                FakeSearch(),
                (FakeEvaluator(prepared.profiles[0]),),
            ),
        },
    ).run_once()
    orchestrator = ProjectMeasurementOrchestrator(repository, execution_policy)
    stage_job, _ = orchestrator.reconcile_job(completed_job, completed_run)
    with pytest.raises(Conflict, match="specialist stage is active"):
        repository.mutate(
            completed_run.run_id,
            owner,
            repository.get(completed_run.run_id, owner).revision,
            lambda run: run,
        )
    repository.lease_one_job("worker-a")
    _, failed_run = repository.fail_job(
        stage_job.job_id,
        "worker-a",
        "ProviderError",
    )
    with pytest.raises(Conflict):
        orchestrator.retry_recommendations(
            failed_run,
            expected_revision=failed_run.revision,
            idempotency_key=stage_job.idempotency_key,
        )

    retry_job, _ = orchestrator.retry_recommendations(
        failed_run,
        expected_revision=failed_run.revision,
        idempotency_key="retry-recommendations",
    )
    replay_job, replay_run = orchestrator.retry_recommendations(
        failed_run,
        expected_revision=failed_run.revision,
        idempotency_key="retry-recommendations",
    )

    assert replay_job.job_id == retry_job.job_id
    assert replay_run.agent_stages[0].status == AgentStageStatus.QUEUED


def test_evaluator_recovery_requeues_recommendations_for_new_measurement(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "recommend-recovery.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-recovery",
        EvaluationRequest(confirm_evaluation_calls=True, include_recommendations=True),
    )
    failed_evaluator = FakeEvaluator(prepared.profiles[0], fail_query="q-2")
    completed_job, partial_run = Worker(
        repository,
        "worker-a",
        {
            JobType.EVALUATE: EvaluationHandler(
                repository,
                execution_policy,
                FakeSearch(),
                (failed_evaluator,),
            ),
        },
    ).run_once()
    orchestrator = ProjectMeasurementOrchestrator(repository, execution_policy)
    orchestrator.reconcile_job(completed_job, partial_run)
    queued_stage_run = repository.get(partial_run.run_id, owner)
    with pytest.raises(Conflict, match="specialist stage is active"):
        JobService(repository).enqueue(
            queued_stage_run.run_id,
            owner,
            queued_stage_run.revision,
            JobType.RECOVER_EVALUATORS,
            "blocked-recovery",
            EvaluatorRecoveryRequest(confirm_evaluation_calls=True),
        )
    _, recommended_run = Worker(
        repository,
        "worker-a",
        {
            JobType.AGENT_STAGE: SpecialistStageHandler(
                repository,
                FakeRecommendations(),
                HostedSpecialistAgent(token_provider=lambda: "unused"),
            ),
        },
    ).run_once()
    old_input_hash = recommended_run.agent_stages[0].input_hash
    JobService(repository).enqueue(
        recommended_run.run_id,
        owner,
        recommended_run.revision,
        JobType.RECOVER_EVALUATORS,
        "recover-evaluators",
        EvaluatorRecoveryRequest(confirm_evaluation_calls=True),
    )
    recovery_result = Worker(
        repository,
        "worker-a",
        {
            JobType.RECOVER_EVALUATORS: EvaluationHandler(
                repository,
                execution_policy,
                FakeSearch(),
                (FakeEvaluator(prepared.profiles[0]),),
            ),
        },
        on_job_finished=orchestrator.reconcile_job,
    ).run_once()

    assert recovery_result is not None
    recovered = repository.get(recommended_run.run_id, owner)
    assert recovered.state == MeasurementState.READY
    assert recovered.recommendations is None
    assert recovered.agent_stages[0].status == AgentStageStatus.QUEUED
    assert recovered.agent_stages[0].input_hash != old_input_hash


def test_completed_evaluation_is_durably_reconciled_after_callback_failure(tmp_path):
    execution_policy = policy()
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    repository = SQLiteMeasurementRepository(tmp_path / "durable-reconciliation.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id, owner, created.revision, prepared.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "e" * 200,
        EvaluationRequest(confirm_evaluation_calls=True, include_recommendations=True),
    )
    evaluation_result = Worker(
        repository,
        "worker-a",
        {
            JobType.EVALUATE: EvaluationHandler(
                repository,
                execution_policy,
                FakeSearch(),
                (FakeEvaluator(prepared.profiles[0]),),
            ),
        },
        on_job_finished=lambda _job, _run: (_ for _ in ()).throw(
            RuntimeError("synthetic callback failure")
        ),
    ).run_once()
    assert evaluation_result is not None
    assert evaluation_result[1].recommendations is None

    orchestrator = ProjectMeasurementOrchestrator(repository, execution_policy)
    stage_result = Worker(
        repository,
        "worker-b",
        {
            JobType.AGENT_STAGE: SpecialistStageHandler(
                repository,
                FakeRecommendations(),
                HostedSpecialistAgent(token_provider=lambda: "unused"),
            ),
        },
        on_job_finished=orchestrator.reconcile_job,
    ).run_once()

    assert stage_result is not None
    assert stage_result[0].job_type == JobType.AGENT_STAGE
    assert len(stage_result[0].idempotency_key) <= 200
    assert stage_result[1].recommendations is not None
    assert stage_result[1].agent_stages[0].status == AgentStageStatus.COMPLETED


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
