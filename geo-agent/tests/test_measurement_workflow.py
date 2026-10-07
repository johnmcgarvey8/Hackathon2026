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
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.persistence import SQLiteMeasurementRepository
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
        "agent_conversations",
        "agent_capabilities",
    } <= tables