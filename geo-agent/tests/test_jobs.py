from datetime import timedelta
import time

import pytest

from geo_agent.contracts import utc_now
from geo_agent.jobs import (
    ClaimedOperationRunner,
    JobService,
    JobState,
    JobType,
    LeaseLost,
    OperationClaimState,
)
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementEvent,
    MeasurementRun,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.worker import Worker
from geo_agent.workflow import Conflict, NotFound
from test_measurement_workflow import inputs
from test_evaluation_workflow import three_profiles


@pytest.fixture
def owner() -> OwnerIdentity:
    return OwnerIdentity(tenant_id="tenant-a", object_id="user-a")


@pytest.fixture
def repository(tmp_path) -> SQLiteMeasurementRepository:
    return SQLiteMeasurementRepository(tmp_path / "jobs.sqlite3")


def approved_run(repository, owner):
    created = repository.create(owner, inputs())
    return MeasurementCoordinator(repository).approve(
        created.run_id,
        owner,
        created.revision,
        created.inputs.approval_hash,
    )


def test_brand_creation_rolls_back_and_versions_survive_restart(repository, owner, monkeypatch):
    from geo_agent.evidence_assessment import BrandDefinition

    definition = BrandDefinition(name="Microsoft Clarity")
    with monkeypatch.context() as patch:
        def fail_insert(*args):
            raise RuntimeError("synthetic definition insert failure")
        patch.setattr(repository, "_insert_brand_definition", fail_insert)
        with pytest.raises(RuntimeError, match="synthetic"):
            repository.create(owner, inputs(), brand_definition=definition)
    assert repository.list_runs(owner) == ()
    run = repository.create(owner, inputs(), brand_definition=definition)
    before = repository.get(run.run_id, owner).model_dump_json()
    record = repository.save_brand_definition(run.run_id, owner, BrandDefinition(name="Clarity Analytics"), 1)
    assert record.definition_version == 2
    assert repository.get(run.run_id, owner).model_dump_json() == before
    from geo_agent.persistence import SQLAlchemyMeasurementRepository
    restarted = SQLAlchemyMeasurementRepository(str(repository.engine.url))
    assert restarted.get_brand_definition(run.run_id, owner, 1).definition == definition
    assert restarted.get_brand_definition(run.run_id, owner) == record
    restarted.engine.dispose()


def test_brand_definition_concurrent_writers_cannot_overwrite(repository, owner):
    from concurrent.futures import ThreadPoolExecutor
    from geo_agent.evidence_assessment import BrandDefinition

    run = repository.create(owner, inputs())
    def save(name):
        try:
            return repository.save_brand_definition(run.run_id, owner, BrandDefinition(name=name), 0)
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, ("First brand", "Second brand")))
    assert sum(outcome is not None for outcome in outcomes) == 1
    assert repository.get_brand_definition(run.run_id, owner).definition_version == 1


def test_brand_migration_preserves_existing_records(tmp_path, owner):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from geo_agent.persistence import SQLAlchemyMeasurementRepository
    from geo_agent.evidence_assessment import BrandDefinition

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    database_url = f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0002_measurement_budget")
    repository = SQLAlchemyMeasurementRepository(database_url)
    run = MeasurementRun(
        owner=owner,
        inputs=inputs(),
        brief=inputs().brief,
        state=MeasurementState.AWAITING_APPROVAL,
        events=(MeasurementEvent(sequence=1, event_type="awaiting-query-approval"),),
    )
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO measurement_runs (run_id, owner_key, revision, payload) VALUES (?, ?, ?, ?)",
            (run.run_id, owner.key, run.revision, run.model_dump_json()),
        )
        connection.exec_driver_sql(
            "INSERT INTO run_events (run_id, sequence, payload) VALUES (?, ?, ?)",
            (run.run_id, 1, run.events[0].model_dump_json()),
        )
    before = run.model_dump_json()
    assert "run_brand_definitions" not in inspect(repository.engine).get_table_names()
    command.upgrade(config, "head")
    assert repository.get(run.run_id, owner).model_dump_json() == before
    record = repository.save_brand_definition(run.run_id, owner, BrandDefinition(name="Microsoft Clarity"), 0)
    assert record.definition_version == 1
    assert repository.get(run.run_id, owner).model_dump_json() == before
    repository.engine.dispose()


def test_enqueue_is_transactional_and_idempotent(repository, owner):
    run = approved_run(repository, owner)
    service = JobService(repository)

    first, queued = service.enqueue(
        run.run_id, owner, run.revision, JobType.EVALUATE, "evaluate-1", {"mode": "mock"}
    )
    replay, replayed_run = service.enqueue(
        run.run_id, owner, run.revision, JobType.EVALUATE, "evaluate-1", {"mode": "mock"}
    )

    assert replay == first
    assert replayed_run == queued
    assert queued.state == MeasurementState.QUEUED
    assert queued.revision == run.revision + 1
    with pytest.raises(Conflict, match="different job request"):
        service.enqueue(
            run.run_id, owner, queued.revision, JobType.EVALUATE, "evaluate-1", {"mode": "live"}
        )


def test_enqueue_replay_survives_terminal_run_revision(repository, owner):
    draft = repository.create(owner)
    request = {"url": "fixture"}
    job, _ = JobService(repository).enqueue(
        draft.run_id,
        owner,
        draft.revision,
        JobType.PREPARE,
        "prepare-terminal-replay",
        request,
    )

    def handler(_job, _operations):
        prepared = inputs()
        return lambda run: run.model_copy(update={
            "inputs": prepared,
            "brief": prepared.brief,
            "state": MeasurementState.AWAITING_APPROVAL,
            "events": (*run.events, MeasurementEvent(
                sequence=len(run.events) + 1,
                event_type="awaiting-query-approval",
            )),
        })

    completed_job, completed_run = Worker(
        repository,
        "worker-a",
        {JobType.PREPARE: handler},
    ).run_once()
    replayed_job, replayed_run = JobService(repository).enqueue(
        draft.run_id,
        owner,
        draft.revision,
        JobType.PREPARE,
        "prepare-terminal-replay",
        request,
    )

    assert replayed_job == completed_job
    assert replayed_job.job_id == job.job_id
    assert replayed_run == completed_run


def test_enqueue_rejects_stale_or_unapproved_run(repository, owner):
    draft = repository.create(owner)
    with pytest.raises(Conflict, match="Evaluation requires"):
        JobService(repository).enqueue(
            draft.run_id, owner, draft.revision, JobType.EVALUATE, "evaluate-1", {}
        )

    run = approved_run(repository, owner)
    with pytest.raises(Conflict, match="Stale measurement revision"):
        JobService(repository).enqueue(
            run.run_id, owner, run.revision - 1, JobType.EVALUATE, "evaluate-2", {}
        )


def test_only_one_worker_can_lease_and_claim_once(repository, owner):
    run = approved_run(repository, owner)
    job, _ = JobService(repository).enqueue(
        run.run_id, owner, run.revision, JobType.EVALUATE, "evaluate-1", {}
    )
    now = utc_now()

    leased = repository.lease_one_job("worker-a", now=now)
    assert leased is not None
    assert leased.job_id == job.job_id
    assert leased.state == JobState.LEASED
    assert repository.lease_one_job("worker-b", now=now) is None
    claim = repository.claim_operation(
        job.job_id, "worker-a", "evaluate-1:search:q-1", "webiq-search", now=now
    )
    assert claim.state == OperationClaimState.CLAIMED
    with pytest.raises(Conflict, match="already claimed"):
        repository.claim_operation(
            job.job_id, "worker-a", "evaluate-1:search:q-1", "webiq-search", now=now
        )


def test_worker_renews_lease_during_slow_handler(repository, owner):
    draft = repository.create(owner)
    JobService(repository).enqueue(
        draft.run_id,
        owner,
        draft.revision,
        JobType.PREPARE,
        "slow-renewed-job",
        {},
    )

    def handler(_job, _operations):
        time.sleep(3.2)
        prepared = inputs()
        return lambda run: run.model_copy(update={
            "inputs": prepared,
            "brief": prepared.brief,
            "state": MeasurementState.AWAITING_APPROVAL,
            "events": (*run.events, MeasurementEvent(
                sequence=len(run.events) + 1,
                event_type="awaiting-query-approval",
            )),
        })

    result = Worker(
        repository,
        "worker-a",
        {JobType.PREPARE: handler},
        lease_seconds=3,
        heartbeat_seconds=0.25,
    ).run_once()

    assert result is not None
    job, run = result
    assert job.state == JobState.COMPLETED
    assert run.state == MeasurementState.AWAITING_APPROVAL


def test_cancelled_job_rejects_stale_fencing_token(repository, owner):
    run = approved_run(repository, owner)
    job, _ = JobService(repository).enqueue(
        run.run_id,
        owner,
        run.revision,
        JobType.EVALUATE,
        "cancel-fenced-job",
        {},
    )
    leased = repository.lease_one_job("worker-a")
    assert leased is not None and leased.lease_token is not None
    repository.cancel_job(job.job_id, owner)

    with pytest.raises(LeaseLost):
        repository.complete_job(
            job.job_id,
            "worker-a",
            lambda current: current,
            leased.lease_token,
        )


def test_expired_lease_is_not_replayed_and_marks_run_needs_review(repository, owner):
    run = approved_run(repository, owner)
    job, _ = JobService(repository).enqueue(
        run.run_id, owner, run.revision, JobType.EVALUATE, "evaluate-1", {}
    )
    now = utc_now()
    repository.lease_one_job("worker-a", lease_seconds=10, now=now)

    recovered = repository.recover_interrupted(now + timedelta(seconds=11))
    assert len(recovered) == 1
    assert recovered[0].job_id == job.job_id
    assert recovered[0].state == JobState.FAILED
    assert recovered[0].error_code == "lease-expired"
    assert repository.get(run.run_id, owner).state == MeasurementState.NEEDS_REVIEW
    assert repository.lease_one_job("worker-b", now=now + timedelta(seconds=11)) is None


def test_recovery_is_fenced_against_a_changed_lease_token(repository, owner, monkeypatch):
    run = approved_run(repository, owner)
    job, _ = JobService(repository).enqueue(
        run.run_id,
        owner,
        run.revision,
        JobType.EVALUATE,
        "fenced-recovery",
        {},
    )
    now = utc_now()
    leased = repository.lease_one_job("worker-a", lease_seconds=10, now=now)
    original = repository._job_from_row

    def stale_job(row):
        restored = original(row)
        if restored.job_id == job.job_id:
            return restored.model_copy(update={"lease_token": "stale-fencing-token"})
        return restored

    monkeypatch.setattr(repository, "_job_from_row", stale_job)
    recovered = repository.recover_interrupted(now + timedelta(seconds=11))

    assert recovered == ()
    monkeypatch.setattr(repository, "_job_from_row", original)
    current = repository.get_job(job.job_id, owner)
    assert current.state == JobState.LEASED
    assert current.lease_token == leased.lease_token
    assert repository.get(run.run_id, owner).state == MeasurementState.EVALUATING


def test_cancellation_is_owner_scoped_and_updates_run(repository, owner):
    run = approved_run(repository, owner)
    job, _ = JobService(repository).enqueue(
        run.run_id, owner, run.revision, JobType.EVALUATE, "evaluate-1", {}
    )
    other = OwnerIdentity(tenant_id=owner.tenant_id, object_id="user-b")

    with pytest.raises(NotFound):
        JobService(repository).cancel(job.job_id, other)
    cancelled_job, cancelled_run = JobService(repository).cancel(job.job_id, owner)
    assert cancelled_job.state == JobState.CANCELLED
    assert cancelled_job.lease_holder is None
    assert cancelled_run.state == MeasurementState.CANCELLED
    assert cancelled_run.events[-1].event_type == "cancelled"


def test_worker_claims_before_dispatch_and_completes_atomically(repository, owner):
    draft = repository.create(owner)
    job, queued = JobService(repository).enqueue(
        draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-1", {"url": "fixture"}
    )
    observed_states = []

    def handler(_job, operations: ClaimedOperationRunner):
        prepared = operations.call(
            "prepare-1:browse",
            "webiq-browse",
            lambda: (
                inputs(),
                {"provider": "fixture", "trace_id": "trace-1"},
            ),
        )
        with repository.engine.connect() as connection:
            observed_states.append(connection.exec_driver_sql(
                "SELECT state FROM operation_claims WHERE operation_key = 'prepare-1:browse'"
            ).scalar_one())
        return lambda run: run.model_copy(update={
            "inputs": prepared,
            "state": MeasurementState.AWAITING_APPROVAL,
            "events": (*run.events, MeasurementEvent(
                sequence=len(run.events) + 1,
                event_type="awaiting-query-approval",
            )),
        })

    completed_job, completed_run = Worker(
        repository, "worker-a", {JobType.PREPARE: handler}
    ).run_once()

    assert observed_states == [OperationClaimState.COMPLETED.value]
    assert completed_job.job_id == job.job_id
    assert completed_job.state == JobState.COMPLETED
    assert completed_run.revision == queued.revision + 1
    assert completed_run.state == MeasurementState.AWAITING_APPROVAL
    checkpoints = repository.list_operation_checkpoints(completed_run.run_id, owner)
    assert len(checkpoints) == 1
    assert checkpoints[0].output["schema_version"] == "geo-inputs/v2"


def test_queue_admission_enforces_owner_and_global_limits(repository):
    owners = [
        OwnerIdentity(tenant_id="tenant-a", object_id=f"user-{index}")
        for index in range(6)
    ]
    for index in range(4):
        draft = repository.create(owners[0])
        JobService(repository).enqueue(
            draft.run_id,
            owners[0],
            draft.revision,
            JobType.PREPARE,
            f"queue-{owners[0].object_id}-{index}",
            {},
        )
    owner_overflow = repository.create(owners[0])
    with pytest.raises(Conflict, match="at most 4"):
        JobService(repository).enqueue(
            owner_overflow.run_id,
            owners[0],
            owner_overflow.revision,
            JobType.PREPARE,
            "owner-queue-overflow",
            {},
        )
    for owner in owners[1:5]:
        for index in range(4):
            draft = repository.create(owner)
            JobService(repository).enqueue(
                draft.run_id,
                owner,
                draft.revision,
                JobType.PREPARE,
                f"queue-{owner.object_id}-{index}",
                {},
            )
    global_overflow = repository.create(owners[5])
    with pytest.raises(Conflict, match="at most 20"):
        JobService(repository).enqueue(
            global_overflow.run_id,
            owners[5],
            global_overflow.revision,
            JobType.PREPARE,
            "global-queue-overflow",
            {},
        )


def test_progress_updates_without_run_revision_and_is_owner_scoped(repository, owner):
    draft = repository.create(owner)
    job, run = JobService(repository).enqueue(
        draft.run_id, owner, draft.revision, JobType.PREPARE, "progress-prepare", {}
    )
    queued = repository.get_run_progress(run.run_id, owner)
    assert queued.job_state == JobState.QUEUED
    assert sum(item.planned for item in queued.operations) == 3
    repository.lease_one_job("worker-a")
    claim = repository.claim_operation(job.job_id, "worker-a", "progress:browse", "webiq-browse")
    active = repository.get_run_progress(run.run_id, owner)
    assert active.run_revision == queued.run_revision
    assert active.current_operation == "webiq-browse"
    assert active.operations[0].in_flight == 1
    repository.record_operation(claim.claim_id, "worker-a", OperationClaimState.COMPLETED,
                                {"secret": "must-not-appear"})
    completed = repository.get_run_progress(run.run_id, owner)
    assert completed.operations[0].completed == 1
    assert completed.operations[0].in_flight == 0
    assert completed.current_operation is None
    assert completed.run_revision == run.revision
    assert "must-not-appear" not in completed.model_dump_json()
    assert "operation_key" not in completed.model_dump_json()
    assert repository.get(run.run_id, owner) == run
    with pytest.raises(NotFound):
        repository.get_run_progress(run.run_id, OwnerIdentity(tenant_id="other", object_id="other"))


@pytest.mark.parametrize("profile_count", [1, 3])
def test_progress_totals_and_unresolved_cancelled_work(repository, owner, profile_count):
    packet = inputs()
    packet = packet.model_copy(update={"profiles": three_profiles()[:profile_count]})
    run = repository.create(owner, packet)
    run = MeasurementCoordinator(repository).approve(run.run_id, owner, run.revision, packet.approval_hash)
    job, _ = JobService(repository).enqueue(
        run.run_id, owner, run.revision, JobType.EVALUATE, "progress-evaluate", {"include_recommendations": True}
    )
    repository.lease_one_job("worker-a")
    repository.claim_operation(job.job_id, "worker-a", "progress:search", "webiq-search")
    repository.cancel_job(job.job_id, owner)
    progress = repository.get_run_progress(run.run_id, owner)
    assert [item.planned for item in progress.operations] == [5, 5 * profile_count, 1]
    assert progress.operations[0].unresolved == 1
    assert progress.operations[0].in_flight == 0
    assert progress.operations[0].not_attempted == 4
    assert progress.operations[1].not_attempted == 5 * profile_count
    assert progress.operations[2].optional
    assert progress.current_operation is None


def test_worker_failure_is_sanitized_and_never_requeued(repository, owner):
    draft = repository.create(owner)
    job, _ = JobService(repository).enqueue(
        draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-1", {}
    )
    called = []

    def handler(_job, operations: ClaimedOperationRunner):
        operations.call(
            "prepare-1:browse",
            "webiq-browse",
            lambda: (_ for _ in ()).throw(RuntimeError("secret provider detail")),
        )
        called.append(True)
        raise AssertionError("unreachable")

    failed_job, failed_run = Worker(
        repository, "worker-a", {JobType.PREPARE: handler}
    ).run_once()

    assert called == []
    assert failed_job.job_id == job.job_id
    assert failed_job.state == JobState.FAILED
    assert failed_job.error_code == "RuntimeError"
    assert failed_run.state == MeasurementState.NEEDS_REVIEW
    assert repository.lease_one_job("worker-b") is None
    with repository.engine.connect() as connection:
        payload = connection.exec_driver_sql(
            "SELECT payload FROM operation_claims WHERE operation_key = 'prepare-1:browse'"
        ).scalar_one()
    assert "RuntimeError" in payload
    assert "secret provider detail" not in payload