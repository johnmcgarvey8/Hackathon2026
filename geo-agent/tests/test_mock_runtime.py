import sqlite3
from pathlib import Path
from threading import Event, Thread

from geo_agent.contracts import Brief
from geo_agent.evaluation_workflow import EvaluationRequest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobService, JobType
from geo_agent.measurement_workflow import MeasurementCoordinator, MeasurementState, OwnerIdentity
from geo_agent.mock_runtime import MockMeasurementRuntime
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.preparation import PreparationRequest
from test_measurement_api import policy
from test_measurement_workflow import inputs


def test_checked_in_mock_policy_allows_clarity_url():
    policy_path = Path(__file__).parents[1] / "measurement-policy.json"
    execution_policy = MeasurementExecutionPolicy.model_validate_json(policy_path.read_text(encoding="utf-8"))
    brief = Brief(url="https://clarity.microsoft.com/", audience="Research buyers", goal="Compare trusted options", locale="en-GB")

    execution_policy.validate_brief(brief)


def test_mock_runtime_completes_durable_three_profile_workflow_without_providers(tmp_path):
    profiles = tuple(
        inputs().profiles[0].model_copy(update={
            "profile_id": profile_id,
            "provider": "anthropic-messages" if profile_id == "claude-backed" else "openai-responses",
            "deployment": f"local-{profile_id}",
            "endpoint": f"https://{profile_id}.fixture.invalid/",
        })
        for profile_id in ("chatgpt-style", "claude-backed", "copilot-style")
    )
    execution_policy = policy(profiles)
    assert isinstance(execution_policy, MeasurementExecutionPolicy)
    repository = SQLiteMeasurementRepository(tmp_path / "runtime.sqlite3")
    runtime = MockMeasurementRuntime(repository, execution_policy)
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    brief = Brief.model_validate({
        **inputs().brief.model_dump(mode="json"),
        "url": "https://example.com/mock-page",
    })
    draft = repository.create(owner, brief=brief)
    JobService(repository).enqueue(
        draft.run_id,
        owner,
        draft.revision,
        JobType.PREPARE,
        "prepare-runtime",
        PreparationRequest(brief=brief, confirm_preparation_calls=True),
        policy_id=execution_policy.policy_id,
        policy_hash=execution_policy.policy_hash,
        operation_ceiling=3,
    )

    runtime.drain()
    prepared = repository.get(draft.run_id, owner)
    assert prepared.state == MeasurementState.AWAITING_APPROVAL
    assert prepared.inputs is not None
    assert prepared.inputs.profiles == profiles
    approved = MeasurementCoordinator(repository).approve(
        prepared.run_id,
        owner,
        prepared.revision,
        prepared.inputs.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-runtime",
        EvaluationRequest(confirm_evaluation_calls=True, include_recommendations=True),
        policy_id=execution_policy.policy_id,
        policy_hash=execution_policy.policy_hash,
        operation_ceiling=5 + 5 * len(execution_policy.profiles) + 1,
    )

    runtime.drain()
    completed = repository.get(draft.run_id, owner)
    assert completed.state == MeasurementState.READY
    assert completed.measurement is not None
    assert len(completed.measurement.retrievals) == 5
    assert len(completed.measurement.results) == 15
    assert completed.recommendations is not None
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        operation_count = connection.execute("SELECT COUNT(*) FROM operation_claims").fetchone()[0]
    assert operation_count == 24


def test_operation_identity_is_job_scoped_for_max_keys_and_multiple_owners(tmp_path):
    execution_policy = policy()
    repository = SQLiteMeasurementRepository(tmp_path / "operation-identity.sqlite3")
    runtime = MockMeasurementRuntime(repository, execution_policy)
    idempotency_key = "k" * 200

    for object_id in ("owner-a", "owner-b"):
        owner = OwnerIdentity(tenant_id="tenant-a", object_id=object_id)
        brief = Brief.model_validate({
            **inputs().brief.model_dump(mode="json"),
            "url": "https://example.com/mock-page",
        })
        draft = repository.create(owner, brief=brief)
        JobService(repository).enqueue(
            draft.run_id,
            owner,
            draft.revision,
            JobType.PREPARE,
            idempotency_key,
            PreparationRequest(brief=brief, confirm_preparation_calls=True),
            policy_id=execution_policy.policy_id,
            policy_hash=execution_policy.policy_hash,
            operation_ceiling=3,
        )
        runtime.drain()
        assert repository.get(draft.run_id, owner).state == MeasurementState.AWAITING_APPROVAL

    with sqlite3.connect(tmp_path / "operation-identity.sqlite3") as connection:
        keys = [
            row[0]
            for row in connection.execute(
                "SELECT operation_key FROM operation_claims ORDER BY operation_key"
            )
        ]
    assert len(keys) == len(set(keys)) == 6
    assert max(map(len, keys)) < 200


def test_mock_runtime_does_not_drop_a_concurrent_drain_notification(tmp_path):
    execution_policy = policy()
    repository = SQLiteMeasurementRepository(tmp_path / "wakeup.sqlite3")
    runtime = MockMeasurementRuntime(repository, execution_policy)
    entered = Event()
    release = Event()
    calls = []

    def run_once():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return None

    runtime.worker.run_once = run_once
    first = Thread(target=runtime.drain)
    first.start()
    assert entered.wait(5)
    runtime.drain()
    release.set()
    first.join(5)

    assert not first.is_alive()
    assert calls == [1, 2]