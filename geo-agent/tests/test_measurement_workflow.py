import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from geo_agent.contracts import (
    Brief,
    EvidenceQuote,
    MeasurementInputs,
    PageSnapshot,
    QueryPair,
    QueryPlan,
    SimulationProfile,
    digest,
)
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementEvent,
    MeasurementRun,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.jobs import JobState, JobType, WorkflowJob
from geo_agent.persistence import SQLAlchemyMeasurementRepository, SQLiteMeasurementRepository
from geo_agent.providers import simulation_instructions
from geo_agent.workflow import Conflict, NotFound, RunStore


def inputs(goal: str = "Help families plan a visit") -> MeasurementInputs:
    brief = Brief(url="https://example.com/visit", audience="Families", goal=goal, locale="en-GB")
    snapshot = PageSnapshot(
        url=brief.url,
        title="Plan a visit",
        content="Family visits include gardens, food and seasonal events.",
        provenance="synthetic",
    )
    plan = QueryPlan(queries=tuple(
        QueryPair(
            query_id=f"q-{index}",
            priority=index,
            rationale=f"Planning need {index}",
            intent=f"Plan visit {index}",
            chat_query=f"What should a family know before visit {index}?",
            grounding_query=f"family visit information {index}",
            evidence=(EvidenceQuote(evidence_id="page-1", quote="Family visits"),),
        )
        for index in range(1, 6)
    ))
    profile = SimulationProfile(
        profile_id="chatgpt-style",
        provider="openai-responses",
        deployment="test-deployment",
        endpoint="https://fixture.services.ai.azure.com/openai/v1/",
        prompt_version="test/v1",
        instructions=simulation_instructions("chatgpt-style"),
    )
    return MeasurementInputs(
        brief=brief,
        snapshot=snapshot,
        query_plan=plan,
        profiles=(profile,),
        policy_hash=digest({"policy": "mock-only"}),
    )


@pytest.fixture
def owner() -> OwnerIdentity:
    return OwnerIdentity(tenant_id="tenant-a", object_id="user-a")


def test_repository_roundtrips_without_touching_legacy_runs(tmp_path, owner):
    database = tmp_path / "runs.sqlite3"
    RunStore(database)
    repository = SQLiteMeasurementRepository(database)
    prepared = inputs()
    created = repository.create(owner, prepared)

    restored = SQLiteMeasurementRepository(database).get(created.run_id, owner)
    assert restored == created
    assert restored.state == MeasurementState.AWAITING_APPROVAL
    assert restored.inputs == prepared

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
    assert {"runs", "measurement_runs", "run_events", "approvals"} <= tables


def test_owner_scope_and_stale_revision_are_enforced(tmp_path, owner):
    repository = SQLiteMeasurementRepository(tmp_path / "runs.sqlite3")
    created = repository.create(owner, inputs())
    other = OwnerIdentity(tenant_id=owner.tenant_id, object_id="user-b")

    with pytest.raises(NotFound):
        repository.get(created.run_id, other)

    coordinator = MeasurementCoordinator(repository)
    approved = coordinator.approve(created.run_id, owner, 1, created.inputs.approval_hash)
    assert approved.revision == 2
    assert approved.approval.actor == owner
    assert [event.event_type for event in approved.events] == ["awaiting-query-approval", "queries-approved"]
    with pytest.raises(Conflict, match="Stale measurement revision"):
        coordinator.approve(created.run_id, owner, 1, created.inputs.approval_hash)


def test_revision_invalidates_exact_input_approval(tmp_path, owner):
    repository = SQLiteMeasurementRepository(tmp_path / "runs.sqlite3")
    coordinator = MeasurementCoordinator(repository)
    created = repository.create(owner, inputs())
    approved = coordinator.approve(created.run_id, owner, 1, created.inputs.approval_hash)

    changed = inputs(goal="Compare family visit options")
    revised = coordinator.revise_inputs(approved.run_id, owner, approved.revision, changed)
    assert revised.revision == 3
    assert revised.inputs == changed
    assert revised.approval is None
    assert revised.state == MeasurementState.AWAITING_APPROVAL
    assert revised.events[-1].event_type == "inputs-revised"

    with pytest.raises(Conflict, match="does not match"):
        coordinator.approve(revised.run_id, owner, revised.revision, approved.inputs.approval_hash)


def test_repository_rejects_identity_and_event_rewrites(tmp_path, owner):
    repository = SQLiteMeasurementRepository(tmp_path / "runs.sqlite3")
    created = repository.create(owner, inputs())

    with pytest.raises(Conflict, match="cannot change identity"):
        repository.mutate(
            created.run_id,
            owner,
            created.revision,
            lambda run: run.model_copy(update={
                "owner": OwnerIdentity(tenant_id="tenant-b", object_id="user-b"),
            }),
        )
    with pytest.raises(Conflict, match="append-only"):
        repository.mutate(
            created.run_id,
            owner,
            created.revision,
            lambda run: run.model_copy(update={"events": ()}),
        )


def test_alembic_upgrade_preserves_legacy_table(tmp_path):
    database = tmp_path / "migrated.sqlite3"
    RunStore(database)
    with sqlite3.connect(database) as connection:
        legacy_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()[0]

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        migrated_legacy_schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()[0]
    assert legacy_schema == migrated_legacy_schema
    assert {
        "measurement_runs",
        "run_events",
        "approvals",
        "workflow_jobs",
        "operation_claims",
        "measurement_budget_grants",
        "measurement_budget_usage",
        "measurement_budget_consumptions",
        "artifacts",
        "artifact_create_requests",
        "agent_conversations",
        "agent_capabilities",
    } <= tables


def test_sqlite_repository_upgrades_recognized_unversioned_pre_mcp_schema(tmp_path, owner):
    from sqlalchemy import inspect

    database = tmp_path / "pre-mcp.sqlite3"
    RunStore(database)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "0003_brand_definitions")
    prepared = inputs()
    run = MeasurementRun(
        owner=owner,
        brief=prepared.brief,
        inputs=prepared,
        state=MeasurementState.AWAITING_APPROVAL,
        events=(MeasurementEvent(
            sequence=1,
            event_type="awaiting-query-approval",
        ),),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO measurement_runs (run_id, owner_key, revision, payload) "
            "VALUES (?, ?, ?, ?)",
            (run.run_id, owner.key, run.revision, run.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO run_events (run_id, sequence, payload) VALUES (?, ?, ?)",
            (run.run_id, 1, run.events[0].model_dump_json()),
        )
        connection.execute("DROP TABLE alembic_version")

    repository = SQLiteMeasurementRepository(database)

    assert repository.get(run.run_id, owner) == run
    columns = {column["name"] for column in inspect(repository.engine).get_columns("measurement_runs")}
    assert {"state", "created_at", "updated_at", "summary_payload"} <= columns
    with repository.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0005_nullable_export_reservations"
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM runs"
        ).scalar_one() == 0
    repository.close()


def test_sqlite_repository_rejects_partially_applied_mcp_migration(tmp_path):
    database = tmp_path / "partial-mcp.sqlite3"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "0003_brand_definitions")
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE measurement_runs ADD COLUMN state VARCHAR(40)")

    with pytest.raises(Conflict, match="partially migrated"):
        SQLiteMeasurementRepository(database)


def test_migration_moves_legacy_queued_job_to_review_state(tmp_path, owner):
    database = tmp_path / "queued-before-mcp.sqlite3"
    database_url = f"sqlite:///{database.as_posix()}"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0003_brand_definitions")
    run = MeasurementRun(
        owner=owner,
        brief=inputs().brief,
        state=MeasurementState.PREPARING,
        revision=2,
        events=(
            MeasurementEvent(sequence=1, event_type="draft"),
            MeasurementEvent(sequence=2, event_type="preparation-queued"),
        ),
    )
    job = WorkflowJob.create(
        run,
        JobType.PREPARE,
        "legacy-queued-job",
        {"brief": run.brief.model_dump(mode="json")},
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO measurement_runs (run_id, owner_key, revision, payload) "
            "VALUES (?, ?, ?, ?)",
            (run.run_id, owner.key, run.revision, run.model_dump_json()),
        )
        connection.executemany(
            "INSERT INTO run_events (run_id, sequence, payload) VALUES (?, ?, ?)",
            [
                (run.run_id, event.sequence, event.model_dump_json())
                for event in run.events
            ],
        )
        connection.execute(
            "INSERT INTO workflow_jobs "
            "(job_id, run_id, owner_key, job_type, idempotency_key, state, "
            "lease_holder, lease_expires_at, completed_at, error_code, payload, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.job_id,
                job.run_id,
                owner.key,
                job.job_type.value,
                job.idempotency_key,
                job.state.value,
                None,
                None,
                None,
                None,
                job.model_dump_json(),
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )

    command.upgrade(config, "head")
    repository = SQLAlchemyMeasurementRepository(database_url)
    migrated_job = repository.get_job(job.job_id, owner)
    migrated_run = repository.get(run.run_id, owner)

    assert migrated_job.state == JobState.FAILED
    assert migrated_job.error_code == "migration-review-required"
    assert migrated_run.state == MeasurementState.NEEDS_REVIEW
    assert migrated_run.events[-1].event_type == "job-interrupted"
    assert repository.queue_status(owner)["queued_global"] == 0
    repository.close()


def test_revision_0004_export_reservation_is_upgraded_to_nullable(tmp_path, owner):
    database = tmp_path / "old-export-reservation.sqlite3"
    database_url = f"sqlite:///{database.as_posix()}"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0004_mcp_execution_foundation")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "ALTER TABLE artifact_create_requests RENAME TO old_artifact_create_requests"
        )
        connection.execute(
            "CREATE TABLE artifact_create_requests ("
            "owner_key VARCHAR(64) NOT NULL, "
            "idempotency_key VARCHAR(200) NOT NULL, "
            "request_hash VARCHAR(64) NOT NULL, "
            "artifact_id VARCHAR(64) NOT NULL REFERENCES artifacts(artifact_id), "
            "created_at VARCHAR(40) NOT NULL, "
            "PRIMARY KEY (owner_key, idempotency_key))"
        )
        connection.execute("DROP TABLE old_artifact_create_requests")
        connection.execute("PRAGMA foreign_keys = ON")

    repository = SQLiteMeasurementRepository(database)

    with repository.engine.connect() as connection:
        artifact_id = next(
            row
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(artifact_create_requests)"
            )
            if row[1] == "artifact_id"
        )
        assert artifact_id[3] == 0
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0005_nullable_export_reservations"
    assert repository.reserve_export_request(
        owner,
        "post-upgrade-export",
        "a" * 64,
    ) is None
    repository.close()