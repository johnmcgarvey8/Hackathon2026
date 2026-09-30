import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.contracts import Provenance
from geo_agent.evidence_assessment import BrandDefinition, build_competitor_summary
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobType
from geo_agent.measurement_budget import MeasurementBudgetGrant, MeasurementOperationAllowances
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementEvent,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.persistence import SQLAlchemyMeasurementRepository, SQLiteMeasurementRepository
from geo_agent.project_chat import ProjectChatRequest, ProjectChatService, ProjectConversationStore
from geo_agent.project_chat import MockProjectAgent
from geo_agent.project_chat_workflow import ProjectMeasurementChatWorkflow
from geo_agent.project_measurements import ProjectMeasurementOrchestrator
from geo_agent.projects import ProjectCreate, ProjectUpdate
from geo_agent.workflow import Conflict
from test_artifacts import measurement_result, recommendation_report
from test_measurement_workflow import inputs


ALICE_TOKEN = "a" * 40
BOB_TOKEN = "b" * 40


def alice_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + ALICE_TOKEN}


def bob_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + BOB_TOKEN}


def policy() -> MeasurementExecutionPolicy:
    return MeasurementExecutionPolicy(
        policy_id="project-tests",
        execution_mode="live",
        allowed_domains=("example.com",),
        locale="en-GB",
        profiles=inputs().profiles,
        budget_grant_id="project-tests-grant",
    )


def project_bound_policy() -> MeasurementExecutionPolicy:
    return MeasurementExecutionPolicy(
        policy_id="project-bound-tests",
        execution_mode="live",
        scope="project-bound",
        profiles=inputs().profiles,
        budget_grant_id="project-bound-tests-grant",
    )


def mock_policy() -> MeasurementExecutionPolicy:
    return policy().model_copy(update={"execution_mode": "mock", "budget_grant_id": None})


def project_payload(name: str = "Example Brand", domain: str = "example.com") -> dict:
    return {
        "name": name,
        "primary_domain": domain,
        "additional_domains": [],
        "default_locale": "en-GB",
        "active_goal": "Improve grounded discovery",
        "colour": "#0067b8",
    }


def send_chat(client, route: str, conversation: dict, message: str, key: str) -> dict:
    response = client.post(route, json={
        "message": message,
        "expected_revision": conversation["revision"],
        "idempotency_key": key,
        "use_organisational_context": False,
    })
    assert response.status_code == 200, response.text
    return response.json()


def start_chat_measurement(
    client,
    route: str,
    conversation: dict,
    *,
    url: str,
    goal: str,
    audience: str,
    outcome: str,
    key: str,
) -> dict:
    conversation = send_chat(
        client, route, conversation, f"Measure {url} for {goal}", f"{key}-start",
    )
    conversation = send_chat(
        client, route, conversation, audience, f"{key}-audience",
    )
    conversation = send_chat(
        client, route, conversation, outcome, f"{key}-outcome",
    )
    return send_chat(
        client, route, conversation, "Confirm", f"{key}-confirm",
    )


def project_grant(execution_policy, owner, *, grant_id=None, runs=1):
    return MeasurementBudgetGrant(
        grant_id=grant_id or execution_policy.budget_grant_id,
        policy_id=execution_policy.policy_id,
        policy_hash=execution_policy.policy_hash,
        owner=owner,
        allowances=MeasurementOperationAllowances(
            webiq_browse=runs,
            page_analysis_model=runs,
            paired_query_plan=runs,
            webiq_search=5 * runs,
            profile_evaluator=5 * len(execution_policy.profiles) * runs,
            recommendation_model=runs,
        ),
        maximum_authorized_cost_usd=Decimal("50.00"),
        approval="Approved project test capacity",
        approved_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
    )


def live_inputs(execution_policy, brief=None):
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    selected_brief = brief or prepared.brief
    snapshot = prepared.snapshot.model_copy(update={
        "url": selected_brief.url,
        "provenance": Provenance.LIVE,
    })
    return prepared.model_copy(update={"brief": selected_brief, "snapshot": snapshot})


def test_projects_are_owner_scoped_and_revisioned(tmp_path):
    app = create_app(
        tmp_path / "projects.sqlite3",
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        created = client.post("/api/v2/projects", json=project_payload())
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["initials"] == "EB"
        assert project["domains"] == ["example.com"]
        assert project["revision"] == 1
        assert project["run_count"] == 0
        assert project["foundry_status"] == "manual-setup-required"
        assert "owner" not in project

        assert [item["project_id"] for item in client.get("/api/v2/projects").json()] == [
            project["project_id"]
        ]
        route = f"/api/v2/projects/{project['project_id']}"
        updated = client.patch(route, json={
            "expected_revision": 1,
            "name": "Example Commerce",
            "additional_domains": ["shop.example.com"],
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["revision"] == 2
        assert updated.json()["domains"] == ["example.com", "shop.example.com"]
        assert client.patch(route, json={"expected_revision": 1, "name": "Stale"}).status_code == 409

        bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
        assert client.get(route, headers=bob_headers).status_code == 404
        assert client.get("/api/v2/projects", headers=bob_headers).json() == []

        archived = client.post(f"{route}/archive", json={"expected_revision": 2})
        assert archived.status_code == 200
        assert archived.json()["archived"] is True
        assert archived.json()["revision"] == 3


def test_project_domains_are_unique_per_owner(tmp_path):
    app = create_app(
        tmp_path / "project-domains.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        assert client.post("/api/v2/projects", json=project_payload()).status_code == 201
        duplicate = client.post("/api/v2/projects", json=project_payload("Other Brand"))
        assert duplicate.status_code == 409
        invalid = client.post("/api/v2/projects", json=project_payload(domain="https://example.com/path"))
        assert invalid.status_code == 422


def test_project_competitors_are_normalized_revisioned_and_disjoint(tmp_path):
    app = create_app(
        tmp_path / "project-competitors.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        payload = {
            **project_payload(),
            "competitor_domains": ["HTTPS://Competitor.example/", "rival.example"],
        }
        created = client.post("/api/v2/projects", json=payload)
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["competitor_domains"] == [
            "competitor.example",
            "rival.example",
        ]

        duplicate = client.post("/api/v2/projects", json={
            **project_payload("Duplicate competitors", "other.example"),
            "competitor_domains": ["rival.example", "RIVAL.EXAMPLE"],
        })
        assert duplicate.status_code == 422

        route = f"/api/v2/projects/{project['project_id']}"
        overlap = client.patch(route, json={
            "expected_revision": project["revision"],
            "primary_domain": "competitor.example",
        })
        assert overlap.status_code == 422

        updated = client.patch(route, json={
            "expected_revision": project["revision"],
            "competitor_domains": ["new-rival.example"],
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["competitor_domains"] == ["new-rival.example"]


def test_project_runs_snapshot_competitors_and_summarize_citations(tmp_path):
    database = tmp_path / "project-competitor-runs.sqlite3"
    repository = SQLAlchemyMeasurementRepository(
        f"sqlite:///{database}",
        initialize_schema=True,
    )
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    project = repository.create_project(
        owner,
        ProjectCreate.model_validate({
            **project_payload(),
            "competitor_domains": ["alternative.example"],
        }),
    )
    measurement = measurement_result("live")
    run = repository.create(
        owner,
        measurement.inputs,
        project_id=project.project_id,
        competitor_domains=project.competitor_domains,
    )
    assert run.competitor_domains == ("alternative.example",)

    summary = build_competitor_summary(
        measurement,
        run.competitor_domains,
    )
    assert summary["status"] == "cited"
    assert summary["grounding_domains"] == ("alternative.example",)
    assert summary["grounding_source_count"] == 5
    assert summary["citation_count"] == 3

    changed = repository.update_project(
        project.project_id,
        owner,
        ProjectUpdate(
            expected_revision=project.revision,
            competitor_domains=("different.example",),
        ),
    )
    assert changed.competitor_domains == ("different.example",)
    assert repository.get(run.run_id, owner).competitor_domains == (
        "alternative.example",
    )


def test_project_briefs_bind_runs_and_prevent_cross_project_access(tmp_path):
    app = create_app(
        tmp_path / "project-runs.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        first = client.post("/api/v2/projects", json=project_payload()).json()
        second = client.post(
            "/api/v2/projects",
            json=project_payload("Shop", "shop.example.com"),
        ).json()
        brief = inputs().brief.model_dump(mode="json")
        created = client.post(
            f"/api/v2/projects/{first['project_id']}/briefs",
            json=brief,
        )
        assert created.status_code == 201, created.text
        run = created.json()
        first_runs = client.get(f"/api/v2/projects/{first['project_id']}/runs").json()
        assert [item["run_id"] for item in first_runs] == [run["run_id"]]
        assert client.get(
            f"/api/v2/projects/{first['project_id']}/runs/{run['run_id']}"
        ).status_code == 200
        assert client.get(
            f"/api/v2/projects/{second['project_id']}/runs/{run['run_id']}"
        ).status_code == 404

        wrong_domain = client.post(
            f"/api/v2/projects/{second['project_id']}/briefs",
            json=brief,
        )
        assert wrong_domain.status_code == 422
        assert wrong_domain.json()["detail"] == "Brief URL must belong to the project"


def test_brief_domain_matching_ignores_www_prefix_and_host_casing(tmp_path):
    app = create_app(
        tmp_path / "project-www.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload("WWW Brand", "www.example.com"),
        ).json()
        brief = inputs().brief.model_dump(mode="json")
        brief["url"] = "https://Example.com/visit"

        created = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=brief,
        )
        assert created.status_code == 201, created.text

        subdomain = inputs().brief.model_dump(mode="json")
        subdomain["url"] = "https://shop.example.com/visit"
        assert client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=subdomain,
        ).status_code == 201

        unrelated = inputs().brief.model_dump(mode="json")
        unrelated["url"] = "https://notexample.com/visit"
        rejected = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=unrelated,
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == "Brief URL must belong to the project"


def test_project_bound_policy_derives_scope_from_each_saved_project(tmp_path):
    app = create_app(
        tmp_path / "project-bound-policy.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=project_bound_policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload("Dynamic Retail Project", "retail.example"),
        ).json()
        brief = {
            **inputs().brief.model_dump(mode="json"),
            "url": "https://shop.retail.example/category/",
        }
        created = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=brief,
        )
        assert created.status_code == 201, created.text
        run = created.json()
        assert run["brief"]["url"] == brief["url"]

        direct = client.post("/api/v2/briefs", json=brief)
        assert direct.status_code == 409
        assert direct.json()["detail"] == "This measurement policy requires a project-bound run"

        prepared = client.post(
            f"/api/v2/projects/{project['project_id']}/runs/{run['run_id']}/prepare",
            json={
                "expected_revision": run["revision"],
                "idempotency_key": "project-bound-prepare",
                "confirm_preparation_calls": True,
            },
        )
        assert prepared.status_code == 202, prepared.text


def test_automatic_measurement_command_enqueues_live_preparation_and_reports_capacity(tmp_path):
    database = tmp_path / "automatic-command.sqlite3"
    execution_policy = policy()
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    repository = SQLiteMeasurementRepository(database)
    repository.bind_measurement_budget(
        project_grant(execution_policy, owner, runs=2),
        execution_policy,
    )
    app = create_app(database, {ALICE_TOKEN: "alice"}, measurement_policy=execution_policy)

    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        before = client.get(
            f"/api/v2/projects/{project['project_id']}/measurement-capacity"
        )
        assert before.status_code == 200
        assert before.json()["authorized_runs"] is None
        assert before.json()["consumed_runs"] == 0
        assert before.json()["unlimited"] is True

        response = client.post(
            f"/api/v2/projects/{project['project_id']}/measurements",
            json=inputs().brief.model_dump(mode="json"),
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        assert set(payload) == {"run", "job", "capacity"}
        assert payload["run"]["state"] == "preparing"
        assert payload["job"]["job_type"] == "prepare"
        assert payload["capacity"]["remaining_runs"] is None
        assert payload["capacity"]["unlimited"] is True
        saved_job = repository.get_job(payload["job"]["job_id"], owner)
        assert saved_job.request["project_bound"] is True
        leased = repository.lease_one_job("manual-live-worker")
        prepared = live_inputs(
            execution_policy,
            repository.get(payload["run"]["run_id"], owner).brief,
        )
        completed = repository.complete_job(
            leased.job_id,
            "manual-live-worker",
            lambda current: current.model_copy(update={
                "inputs": prepared,
                "state": MeasurementState.AWAITING_APPROVAL,
                "events": (*current.events, MeasurementEvent(
                    sequence=len(current.events) + 1,
                    event_type="awaiting-query-approval",
                )),
            }),
        )
        ProjectMeasurementOrchestrator(repository, execution_policy).reconcile_job(*completed)
        advanced = repository.get(payload["run"]["run_id"], owner)
        jobs = repository.list_run_jobs(advanced.run_id, owner)
        evaluation_job = next(job for job in jobs if job.job_type == JobType.EVALUATE)
        assert advanced.state == MeasurementState.QUEUED
        assert advanced.approval.input_hash == prepared.approval_hash
        assert evaluation_job.request == {
            "confirm_evaluation_calls": True,
            "include_recommendations": True,
            "project_bound": True,
        }


def test_direct_measurement_confirms_persisted_goal_summary_with_fallback(tmp_path):
    database = tmp_path / "confirmed-goal-summary.sqlite3"
    execution_policy = policy()
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    repository = SQLiteMeasurementRepository(database)
    repository.bind_measurement_budget(
        project_grant(execution_policy, owner),
        execution_policy,
    )
    app = create_app(
        database,
        {ALICE_TOKEN: "alice"},
        measurement_policy=execution_policy,
    )

    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        raw_goal = "Understand whether enterprise buyers can find this page in AI answers"
        summary = client.post(
            f"/api/v2/projects/{project['project_id']}/measurement-goal-summaries",
            json={
                "raw_goal": raw_goal,
                "url": "https://example.com/category/",
                "audience": raw_goal,
                "target_kind": "page",
                "idempotency_key": "direct-summary",
            },
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["summary"] == raw_goal
        assert summary.json()["fallback_used"] is True

        created = client.post(
            f"/api/v2/projects/{project['project_id']}/measurements",
            json={
                "url": "https://example.com/category/",
                "audience": raw_goal,
                "goal": "Evaluate AI visibility for enterprise buyers.",
                "locale": "en-GB",
                "raw_goal": raw_goal,
                "goal_summary_operation_id": summary.json()["operation_id"],
            },
        )
        assert created.status_code == 202, created.text
        assert created.json()["run"]["brief"]["goal"] == (
            "Evaluate AI visibility for enterprise buyers."
        )

        mismatch = client.post(
            f"/api/v2/projects/{project['project_id']}/measurements",
            json={
                "url": "https://example.com/other/",
                "audience": raw_goal,
                "goal": "Mismatched summary",
                "locale": "en-GB",
                "raw_goal": raw_goal,
                "goal_summary_operation_id": summary.json()["operation_id"],
            },
        )
        assert mismatch.status_code == 409


def test_project_repository_rejects_synthetic_inputs_and_results(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "project-live-invariant.sqlite3")
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    project = repository.create_project(owner, ProjectCreate.model_validate(project_payload()))
    with pytest.raises(Conflict, match="live page snapshot"):
        repository.create(owner, inputs(), project_id=project.project_id)

    execution_policy = policy()
    prepared = live_inputs(execution_policy)
    run = repository.create(owner, prepared, project_id=project.project_id)
    approved = MeasurementCoordinator(repository).approve(
        run.run_id, owner, run.revision, prepared.approval_hash,
    )
    synthetic_measurement = measurement_result()
    with pytest.raises(Conflict, match="match the run inputs|live"):
        repository.mutate(
            run.run_id,
            owner,
            approved.revision,
            lambda current: current.model_copy(update={
                "measurement": synthetic_measurement,
                "state": MeasurementState.READY,
            }),
        )


def test_application_does_not_start_a_mock_measurement_worker(tmp_path):
    app = create_app(
        tmp_path / "agentic-project-chat.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        conversation = send_chat(
            client,
            route,
            conversation,
            "Measure https://example.com/category/ for grounded discovery",
            "agentic-measurement-one",
        )
        assert conversation["run_id"] is None
        assert conversation["measurement_workflow"]["pending_field"] == "audience"
        conversation = send_chat(
            client, route, conversation, "Category shoppers", "agentic-audience",
        )
        conversation = send_chat(
            client,
            route,
            conversation,
            "Identify AI visibility and citation gaps",
            "agentic-outcome",
        )
        assert conversation["measurement_workflow"]["status"] == "awaiting-confirmation"
        conversation = send_chat(
            client, route, conversation, "Confirm", "agentic-confirm",
        )
        saved = client.get(
            (
                f"/api/v2/projects/{project['project_id']}/conversations/"
                f"{conversation['conversation_id']}"
            ),
        ).json()
        assert saved["run_id"] is not None
        assert saved["linked_run_ids"] == [saved["run_id"]]
        assert saved["turns"][-1]["status"] == "completed"
        assert "has started" in saved["turns"][-1]["answer"]
        runs = client.get(f"/api/v2/projects/{project['project_id']}/runs").json()
        assert len(runs) == 1
        assert runs[0]["state"] == "preparing"


def test_second_run_bound_chat_does_not_steal_automatic_insights(tmp_path):
    database = tmp_path / "agentic-project-chat-binding.sqlite3"
    execution_policy = policy()
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    repository = SQLiteMeasurementRepository(database)
    repository.bind_measurement_budget(project_grant(execution_policy, owner), execution_policy)
    app = create_app(
        database,
        {ALICE_TOKEN: "alice"},
        measurement_policy=execution_policy,
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload(),
        ).json()
        original = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{original['conversation_id']}/messages"
        )
        started = send_chat(
            client,
            route,
            original,
            "Measure https://example.com/category/ for pricing coverage",
            "agentic-binding-start",
        )
        started = send_chat(
            client, route, started, "Retail decision makers", "agentic-binding-audience",
        )
        started = send_chat(
            client, route, started, "Find missing citations", "agentic-binding-outcome",
        )
        started = send_chat(
            client, route, started, "Confirm", "agentic-binding-confirm",
        )
        run_id = started["run_id"]
        second = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations?run_id={run_id}",
        ).json()
        assert second["measurement_workflow"] is None

        workflow = ProjectMeasurementChatWorkflow(repository, execution_policy)
        automatic = ProjectMeasurementOrchestrator(repository, execution_policy)
        agent = MockProjectAgent(repository)
        run = repository.get(run_id, owner)
        prepared = live_inputs(execution_policy, run.brief)
        prepare_job = repository.lease_one_job("test-live-worker")
        assert prepare_job is not None
        completed_prepare = repository.complete_job(
            prepare_job.job_id,
            "test-live-worker",
            lambda current: current.model_copy(update={
                "inputs": prepared,
                "state": MeasurementState.AWAITING_APPROVAL,
                "events": (*current.events, MeasurementEvent(
                    sequence=len(current.events) + 1,
                    event_type="awaiting-query-approval",
                )),
            }),
        )
        automatic.reconcile_job(*completed_prepare)
        workflow.reconcile_job(
            completed_prepare[0],
            repository.get(run_id, owner),
            agent,
        )
        evaluate_job = repository.lease_one_job("test-live-worker")
        assert evaluate_job is not None and evaluate_job.job_type == JobType.EVALUATE
        base_measurement = measurement_result("live")
        pairs = {pair.query_id: pair for pair in prepared.query_plan.queries}
        retrievals = tuple(packet.model_copy(update={
            "grounding_query": pairs[packet.query_id].grounding_query,
        }) for packet in base_measurement.retrievals)
        measurement = base_measurement.model_copy(update={
            "inputs": prepared,
            "retrievals": retrievals,
            "results": tuple(
                result for result in base_measurement.results
                if result.profile_id == prepared.profiles[0].profile_id
            ),
        })
        completed_evaluate = repository.complete_job(
            evaluate_job.job_id,
            "test-live-worker",
            lambda current: current.model_copy(update={
                "measurement": measurement,
                "state": MeasurementState.READY,
                "events": (*current.events, MeasurementEvent(
                    sequence=len(current.events) + 1,
                    event_type="ready",
                )),
            }),
        )
        automatic.reconcile_job(*completed_evaluate)
        workflow.reconcile_job(*completed_evaluate, agent)

        saved_original = client.get(
            (
                f"/api/v2/projects/{project['project_id']}/conversations/"
                f"{original['conversation_id']}"
            ),
        ).json()
        saved_second = client.get(
            (
                f"/api/v2/projects/{project['project_id']}/conversations/"
                f"{second['conversation_id']}"
            ),
        ).json()
        assert saved_original["measurement_workflow"]["status"] == "completed"
        assert [turn["origin"] for turn in saved_original["turns"]] == [
            "user",
            "user",
            "user",
            "user",
            "workflow",
        ]
        assert saved_second["turns"] == []
        repository.close()


def test_collecting_measurement_can_be_cancelled(tmp_path):
    app = create_app(
        tmp_path / "agentic-project-chat-cancel.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload(),
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        collecting = client.post(route, json={
            "message": "Please start a measurement",
            "expected_revision": conversation["revision"],
            "idempotency_key": "collecting-start",
            "use_organisational_context": False,
        }).json()
        assert collecting["measurement_workflow"]["status"] == "collecting"
        cancelled = client.post(route, json={
            "message": "Never mind, cancel that",
            "expected_revision": collecting["revision"],
            "idempotency_key": "collecting-cancel",
            "use_organisational_context": False,
        })
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["measurement_workflow"] is None
        assert "cancelled" in cancelled.json()["turns"][-1]["answer"].lower()


def test_domain_only_measurement_requires_scope_goal_audience_outcome_and_confirmation(tmp_path):
    app = create_app(
        tmp_path / "domain-chat.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload(),
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        conversation = send_chat(
            client, route, conversation, "example.com", "domain-target",
        )
        assert conversation["run_id"] is None
        assert conversation["measurement_workflow"]["pending_field"] == "target-kind"
        conversation = send_chat(
            client,
            route,
            conversation,
            "A domain-level check for any of our brand URLs",
            "domain-scope",
        )
        conversation = send_chat(
            client, route, conversation, "Evaluate AI visibility", "domain-goal",
        )
        conversation = send_chat(
            client, route, conversation, "Enterprise buyers", "domain-audience",
        )
        conversation = send_chat(
            client,
            route,
            conversation,
            "Establish whether owned URLs appear",
            "domain-outcome",
        )
        assert conversation["measurement_workflow"]["status"] == "awaiting-confirmation"
        assert client.get(
            f"/api/v2/projects/{project['project_id']}/runs",
        ).json() == []
        conversation = send_chat(
            client, route, conversation, "Confirm", "domain-confirm",
        )
        run = client.get(
            f"/api/v2/projects/{project['project_id']}/runs",
        ).json()[0]
        assert conversation["run_id"] == run["run_id"]
        assert run["brief"]["audience"] == "Enterprise buyers"
        assert "Domain-level brand URL visibility" in run["brief"]["goal"]
        assert "Establish whether owned URLs appear" in run["brief"]["goal"]
        assert (
            conversation["measurement_workflow"]["desired_outcome"]
            == "Establish whether owned URLs appear"
        )


def test_complete_initial_prompt_parses_bare_domain_and_context_upfront(tmp_path):
    app = create_app(
        tmp_path / "upfront-domain-chat.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload("Halfords", "www.halfords.com"),
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        conversation = send_chat(
            client,
            route,
            conversation,
            (
                "Please evaluate halfords.com - I'm looking to see if any of my "
                "pages show up for any queries related to car enthusiasts."
            ),
            "upfront-domain-context",
        )
        workflow = conversation["measurement_workflow"]
        assert workflow["status"] == "awaiting-confirmation"
        assert workflow["url"] == "https://halfords.com/"
        assert workflow["target_kind"] == "domain"
        assert workflow["goal"] == "Evaluate AI visibility"
        assert workflow["audience"] == "car enthusiasts"
        assert workflow["desired_outcome"] == (
            "See if any of my pages show up for any queries related to car enthusiasts"
        )
        assert "Please confirm this measurement setup" in conversation["turns"][-1]["answer"]


def test_one_conversation_links_and_switches_between_project_runs(tmp_path):
    app = create_app(
        tmp_path / "multi-run-chat.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        first = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=inputs().brief.model_dump(mode="json"),
        ).json()
        second = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json={
                **inputs().brief.model_dump(mode="json"),
                "url": "https://example.com/other/",
            },
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
            params={"run_id": first["run_id"]},
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        switched = send_chat(
            client, route, conversation, second["run_id"], "switch-run",
        )
        assert switched["run_id"] == second["run_id"]
        assert switched["linked_run_ids"] == [first["run_id"], second["run_id"]]


def test_one_conversation_can_create_multiple_measurement_runs(tmp_path):
    app = create_app(
        tmp_path / "multi-created-run-chat.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        conversation = start_chat_measurement(
            client,
            route,
            conversation,
            url="https://example.com/first/",
            goal="Evaluate AI visibility",
            audience="Enterprise buyers",
            outcome="Find citation gaps",
            key="first-run",
        )
        first_run_id = conversation["run_id"]
        conversation = start_chat_measurement(
            client,
            route,
            conversation,
            url="https://example.com/second/",
            goal="Evaluate crawlability",
            audience="Technical evaluators",
            outcome="Find crawlability blockers",
            key="second-run",
        )
        second_run_id = conversation["run_id"]
        assert second_run_id != first_run_id
        assert conversation["linked_run_ids"] == [first_run_id, second_run_id]
        assert len(conversation["measurement_workflows"]) == 2
        assert len(client.get(
            f"/api/v2/projects/{project['project_id']}/runs",
        ).json()) == 2

        compared = send_chat(
            client,
            route,
            conversation,
            f"Compare {first_run_id} and {second_run_id}",
            "compare-runs",
        )
        assert compared["comparison_run_ids"] == [first_run_id, second_run_id]


def test_new_chat_accepts_project_run_id_and_replay_does_not_duplicate(tmp_path):
    app = create_app(
        tmp_path / "run-id-project-chat.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload(),
        ).json()
        run = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=inputs().brief.model_dump(mode="json"),
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        body = {
            "message": run["run_id"],
            "expected_revision": conversation["revision"],
            "idempotency_key": "bind-existing-run",
            "use_organisational_context": False,
        }
        first = client.post(route, json=body)
        assert first.status_code == 200, first.text
        assert first.json()["run_id"] == run["run_id"]
        assert first.json()["turns"][-1]["status"] == "failed"
        replay = client.post(route, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert len(client.get(
            f"/api/v2/projects/{project['project_id']}/runs",
        ).json()) == 1


def test_project_scoped_run_contract_jobs_and_cancellation(tmp_path):
    app = create_app(
        tmp_path / "project-actions.sqlite3",
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        first = client.post("/api/v2/projects", json=project_payload()).json()
        second = client.post(
            "/api/v2/projects",
            json=project_payload("Other Project", "other.example.com"),
        ).json()
        run = client.post(
            f"/api/v2/projects/{first['project_id']}/briefs",
            json=inputs().brief.model_dump(mode="json"),
        ).json()
        route = f"/api/v2/projects/{first['project_id']}/runs/{run['run_id']}"

        assert run["project_id"] == first["project_id"]
        assert run["available_actions"]["prepare"] is True
        assert run["available_actions"]["discuss"] is True
        assert run["result_availability"]["query_plan"] is False
        assert run["operation_estimates"]["preparation"]["total"] == 3
        assert run["progress"]["job_id"] is None
        assert client.get(route).json() == client.get(
            f"/api/v2/runs/{run['run_id']}",
        ).json()
        assert client.get(
            f"/api/v2/projects/{second['project_id']}/runs/{run['run_id']}",
        ).status_code == 404

        request = {
            "expected_revision": run["revision"],
            "idempotency_key": "project-prepare",
            "confirm_preparation_calls": True,
        }
        queued = client.post(f"{route}/prepare", json=request)
        assert queued.status_code == 202, queued.text
        payload = queued.json()
        job = payload["job"]
        assert payload["run"]["available_actions"]["cancel"] is True
        assert payload["run"]["latest_job"] == job
        assert client.get(f"{route}/jobs").json() == [job]
        assert client.get(f"{route}/jobs/{job['job_id']}").json() == job
        assert client.get(f"{route}/progress").json()["job_id"] == job["job_id"]
        other_route = (
            f"/api/v2/projects/{second['project_id']}/runs/{run['run_id']}"
        )
        assert client.get(f"{other_route}/jobs").status_code == 404
        assert client.get(f"{other_route}/progress").status_code == 404

        cancel_route = f"{route}/jobs/{job['job_id']}/cancel"
        assert client.post(
            cancel_route, json={"expected_revision": run["revision"]},
        ).status_code == 409
        cancelled = client.post(
            cancel_route,
            json={"expected_revision": payload["run"]["revision"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["run"]["state"] == "cancelled"
        assert cancelled.json()["run"]["available_actions"]["cancel"] is False
        assert cancelled.json()["run"]["available_actions"]["revise_queries"] is False


def test_project_scoped_query_approval_and_start_are_revision_bound(tmp_path):
    database = tmp_path / "project-approval.sqlite3"
    execution_policy = policy()
    repository = SQLAlchemyMeasurementRepository(
        f"sqlite:///{database}",
        initialize_schema=True,
    )
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    project = repository.create_project(
        owner,
        ProjectCreate.model_validate(project_payload()),
    )
    other = repository.create_project(
        owner,
        ProjectCreate.model_validate(project_payload("Other", "other.example.com")),
    )
    prepared = live_inputs(execution_policy)
    run = repository.create(owner, prepared, project_id=project.project_id)
    app = create_app(database, {ALICE_TOKEN: "alice"}, measurement_policy=execution_policy)

    with TestClient(app, headers=alice_headers()) as client:
        route = f"/api/v2/projects/{project.project_id}/runs/{run.run_id}"
        queries = [item.model_dump(mode="json") for item in prepared.query_plan.queries]
        queries[0]["chat_query"] = "Which revised option best supports this audience?"
        assert client.put(
            f"{route}/queries",
            json={"expected_revision": 999, "queries": queries},
        ).status_code == 409
        revised = client.put(
            f"{route}/queries",
            json={"expected_revision": run.revision, "queries": queries},
        )
        assert revised.status_code == 200, revised.text
        revised_run = revised.json()
        assert revised_run["approval_hash"] != prepared.approval_hash
        assert revised_run["available_actions"]["approve"] is True
        wrong_route = f"/api/v2/projects/{other.project_id}/runs/{run.run_id}"
        assert client.post(
            f"{wrong_route}/query-approval",
            json={
                "expected_revision": revised_run["revision"],
                "input_hash": revised_run["approval_hash"],
            },
        ).status_code == 404
        approved = client.post(
            f"{route}/query-approval",
            json={
                "expected_revision": revised_run["revision"],
                "input_hash": revised_run["approval_hash"],
            },
        )
        assert approved.status_code == 200, approved.text
        approved_run = approved.json()
        assert approved_run["available_actions"]["start"] is True
        assert client.post(
            f"{route}/start",
            json={
                "expected_revision": approved_run["revision"],
                "idempotency_key": "project-evaluate",
                "confirm_evaluation_calls": False,
            },
        ).status_code == 409
        queued = client.post(
            f"{route}/start",
            json={
                "expected_revision": approved_run["revision"],
                "idempotency_key": "project-evaluate",
                "confirm_evaluation_calls": True,
                "include_recommendations": True,
            },
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["run"]["state"] == "queued"


def test_project_scoped_results_reviews_and_artifacts_are_isolated(tmp_path):
    database = tmp_path / "project-results.sqlite3"
    measurement = measurement_result("live")
    execution_policy = MeasurementExecutionPolicy(
        policy_id="project-results",
        execution_mode="live",
        allowed_domains=("example.org",),
        locale="en-GB",
        profiles=measurement.inputs.profiles,
        budget_grant_id="project-results-grant",
    )
    repository = SQLAlchemyMeasurementRepository(
        f"sqlite:///{database}",
        initialize_schema=True,
    )
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    project = repository.create_project(
        owner,
        ProjectCreate(
            name="Example Project",
            primary_domain="example.org",
            default_locale="en-GB",
        ),
    )
    other = repository.create_project(
        owner,
        ProjectCreate(name="Other Project", primary_domain="other.example"),
    )
    created = repository.create(
        owner,
        measurement.inputs,
        brand_definition=BrandDefinition(name=project.name, domains=project.domains),
        project_id=project.project_id,
    )
    approved = MeasurementCoordinator(repository).approve(
        created.run_id,
        owner,
        created.revision,
        measurement.inputs.approval_hash,
    )
    ready = repository.mutate(
        created.run_id,
        owner,
        approved.revision,
        lambda run: run.model_copy(update={
            "measurement": measurement,
            "recommendations": recommendation_report(measurement),
            "state": MeasurementState.READY,
            "events": (*run.events, MeasurementEvent(
                sequence=len(run.events) + 1,
                event_type="ready",
            )),
        }),
    )
    app = create_app(database, {ALICE_TOKEN: "alice"}, measurement_policy=execution_policy)

    with TestClient(app, headers=alice_headers()) as client:
        route = f"/api/v2/projects/{project.project_id}/runs/{ready.run_id}"
        wrong = f"/api/v2/projects/{other.project_id}/runs/{ready.run_id}"
        detail = client.get(route).json()
        assert detail["result_availability"] == {
            "query_plan": True,
            "webiq_evidence": True,
            "model_answers": True,
            "citation_performance": True,
            "brand_presence": True,
            "recommendations": True,
            "limitations_and_provenance": True,
            "content_strategy": True,
            "artifact": False,
        }
        assert detail["available_actions"]["review_recommendations"] is True
        assert detail["available_actions"]["export"] is True
        assert client.get(f"{route}/events").status_code == 200
        evidence = client.get(
            f"{route}/evidence/q-1-alternative",
        ).json()
        assert evidence["evidence_id"] == "q-1-alternative"
        assert evidence["evidence_type"] == "grounding-citation"
        assert evidence["query_id"] == "q-1"
        assert evidence["grounding_query"] == "fixture search 1"
        assert client.get(
            f"{route}/evidence-assessment",
        ).json()["status"] == "ready"
        assert client.get(
            f"{route}/content-strategy",
        ).json()["status"] == "ready"
        assert client.get(f"{route}/artifacts").json() == []

        for suffix in (
            "events",
            "evidence/q-1-alternative",
            "evidence-assessment",
            "content-strategy",
            "artifacts",
        ):
            assert client.get(f"{wrong}/{suffix}").status_code == 404

        review_body = {
            "expected_revision": ready.revision,
            "decisions": [{"task_id": "rec-1", "decision": "accepted"}],
        }
        assert client.post(
            f"{wrong}/recommendation-review", json=review_body,
        ).status_code == 404
        reviewed = client.post(f"{route}/recommendation-review", json=review_body)
        assert reviewed.status_code == 200, reviewed.text
        reviewed_run = reviewed.json()
        assert reviewed_run["recommendation_review"]["decisions"] == [
            {"task_id": "rec-1", "decision": "accepted"},
        ]

        assert client.post(
            f"{route}/exports", json={"expected_revision": ready.revision},
        ).status_code == 409
        exported = client.post(
            f"{route}/exports",
            json={"expected_revision": reviewed_run["revision"]},
        )
        assert exported.status_code == 201, exported.text
        artifact = exported.json()["artifact"]
        assert exported.json()["download_url"].startswith(
            f"/api/v2/projects/{project.project_id}/runs/{ready.run_id}/artifacts/",
        )
        assert client.get(f"{route}/artifacts").json() == [artifact]
        download = client.get(f"{route}/artifacts/{artifact['artifact_id']}")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert client.get(
            f"{wrong}/artifacts/{artifact['artifact_id']}",
        ).status_code == 404
        artifact_files = list(
            (database.parent / "measurement-artifacts").rglob("*.zip")
        )
        assert len(artifact_files) == 1
        assert client.delete(route).status_code == 204
        assert not artifact_files[0].exists()
        assert client.get(route).status_code == 404


def test_project_chat_is_scoped_persistent_and_idempotent(tmp_path):
    app = create_app(
        tmp_path / "project-chat.sqlite3",
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        run = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=inputs().brief.model_dump(mode="json"),
        ).json()
        conversations = f"/api/v2/projects/{project['project_id']}/conversations"
        created = client.post(conversations, params={"run_id": run["run_id"]})
        assert created.status_code == 201, created.text
        conversation = created.json()
        assert conversation["project_id"] == project["project_id"]
        assert conversation["run_id"] == run["run_id"]
        assert conversation["revision"] == 0

        request = {
            "message": "What does this run contain?",
            "expected_revision": 0,
            "idempotency_key": "turn-1",
            "use_organisational_context": True,
        }
        route = f"{conversations}/{conversation['conversation_id']}/messages"
        first = client.post(route, json=request)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["revision"] == 2
        assert result["turns"][0]["status"] == "failed"
        assert "Organisational context is not enabled" in result["turns"][0]["error"]
        assert client.post(route, json=request).json() == result
        assert client.post(route, json={**request, "message": "Different"}).status_code == 409

        assert client.get(conversations).json()[0]["conversation_id"] == conversation["conversation_id"]
        bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
        assert client.get(
            f"{conversations}/{conversation['conversation_id']}",
            headers=bob_headers,
        ).status_code == 404


def test_deleting_project_chat_retains_measurement_run(tmp_path):
    app = create_app(
        tmp_path / "delete-chat.sqlite3",
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        run = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=inputs().brief.model_dump(mode="json"),
        ).json()
        conversations = f"/api/v2/projects/{project['project_id']}/conversations"
        conversation = client.post(
            conversations,
            params={"run_id": run["run_id"]},
        ).json()
        route = f"{conversations}/{conversation['conversation_id']}"

        assert client.delete(
            route,
            headers=bob_headers(),
        ).status_code == 404
        assert client.delete(route).status_code == 204
        assert client.get(route).status_code == 404
        assert client.get(
            f"/api/v2/projects/{project['project_id']}/runs/{run['run_id']}",
        ).status_code == 200
        assert client.delete(route).status_code == 404


def test_deleting_measurement_run_reconciles_retained_chat(tmp_path):
    app = create_app(
        tmp_path / "delete-run.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        first = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=inputs().brief.model_dump(mode="json"),
        ).json()
        second = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json={
                **inputs().brief.model_dump(mode="json"),
                "url": "https://example.com/second/",
            },
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
            params={"run_id": first["run_id"]},
        ).json()
        message_route = (
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}/messages"
        )
        conversation = send_chat(
            client,
            message_route,
            conversation,
            second["run_id"],
            "switch-before-delete",
        )
        assert conversation["run_id"] == second["run_id"]

        run_route = (
            f"/api/v2/projects/{project['project_id']}/runs/{second['run_id']}"
        )
        assert client.delete(run_route).status_code == 204
        assert client.get(run_route).status_code == 404
        retained = client.get(
            f"/api/v2/projects/{project['project_id']}/conversations/"
            f"{conversation['conversation_id']}"
        ).json()
        assert retained["run_id"] is None
        assert retained["linked_run_ids"] == [first["run_id"]]
        assert second["run_id"] not in retained["bound_run_contexts"]
        assert all(
            workflow["run_id"] != second["run_id"]
            for workflow in retained["measurement_workflows"]
        )
        assert client.get(
            f"/api/v2/projects/{project['project_id']}/runs/{first['run_id']}",
        ).status_code == 200


def test_deleting_run_releases_unfulfilled_export_idempotency_key(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "delete-export-request.sqlite3")
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    project = repository.create_project(
        owner,
        ProjectCreate.model_validate(project_payload()),
    )
    first = repository.create(
        owner,
        brief=inputs().brief,
        project_id=project.project_id,
    )
    assert repository.reserve_export_request(
        owner,
        first.run_id,
        "reusable-export-key",
        "a" * 64,
    ) is None

    repository.delete_project_run(project.project_id, first.run_id, owner)
    second = repository.create(
        owner,
        brief=inputs().brief,
        project_id=project.project_id,
    )

    assert repository.reserve_export_request(
        owner,
        second.run_id,
        "reusable-export-key",
        "b" * 64,
    ) is None
    repository.close()


def test_active_run_and_project_deletion_are_blocked(tmp_path):
    database = tmp_path / "active-delete.sqlite3"
    repository = SQLAlchemyMeasurementRepository(
        f"sqlite:///{database}",
        initialize_schema=True,
    )
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    project = repository.create_project(
        owner,
        ProjectCreate.model_validate(project_payload()),
    )
    run = repository.create(owner, brief=inputs().brief, project_id=project.project_id)
    repository.mutate(
        run.run_id,
        owner,
        run.revision,
        lambda current: current.model_copy(update={
            "state": MeasurementState.PREPARING,
            "events": (*current.events, MeasurementEvent(
                sequence=len(current.events) + 1,
                event_type="preparation-started",
            )),
        }),
    )
    app = create_app(database, {ALICE_TOKEN: "alice"}, measurement_policy=policy())

    with TestClient(app, headers=alice_headers()) as client:
        run_route = f"/api/v2/projects/{project.project_id}/runs/{run.run_id}"
        assert client.delete(run_route).status_code == 409
        assert client.delete(f"/api/v2/projects/{project.project_id}").status_code == 409
        assert client.get(run_route).status_code == 200
        assert client.get(f"/api/v2/projects/{project.project_id}").status_code == 200


def test_deleting_project_cascades_chats_and_measurements(tmp_path):
    database = tmp_path / "delete-project.sqlite3"
    app = create_app(
        database,
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        run = client.post(
            f"/api/v2/projects/{project['project_id']}/briefs",
            json=inputs().brief.model_dump(mode="json"),
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
            params={"run_id": run["run_id"]},
        ).json()
        project_route = f"/api/v2/projects/{project['project_id']}"

        assert client.delete(
            project_route,
            headers=bob_headers(),
        ).status_code == 404
        assert client.delete(project_route).status_code == 204
        assert client.get(project_route).status_code == 404
        assert client.get(f"{project_route}/runs/{run['run_id']}").status_code == 404
        assert client.get(
            f"{project_route}/conversations/{conversation['conversation_id']}",
        ).status_code == 404
        assert client.delete(project_route).status_code == 404


def test_project_chat_persists_unexpected_agent_failure(tmp_path):
    class FailingAgent:
        async def respond(self, project, conversation, request):
            raise RuntimeError("provider response must not be exposed")

    repository = SQLAlchemyMeasurementRepository(
        f"sqlite:///{tmp_path / 'project-chat-failure.sqlite3'}",
        initialize_schema=True,
    )
    owner = OwnerIdentity(tenant_id="local", object_id="alice")
    project = repository.create_project(
        owner,
        ProjectCreate.model_validate(project_payload()),
    )
    store = ProjectConversationStore(repository)
    conversation = store.create(project.project_id, owner)
    result = asyncio.run(ProjectChatService(repository, FailingAgent()).respond(
        project.project_id,
        conversation.conversation_id,
        owner,
        ProjectChatRequest(
            message="What is the current status?",
            expected_revision=0,
            idempotency_key="failure-turn",
        ),
    ))

    assert result.revision == 2
    assert result.turns[0].status == "failed"
    assert result.turns[0].error == (
        "The agent request failed. Check the configured runtime and try again."
    )
    assert "provider response" not in result.turns[0].error
    assert store.get(project.project_id, conversation.conversation_id, owner) == result


def test_projects_work_without_measurement_policy_and_never_mock_chat(tmp_path):
    app = create_app(
        tmp_path / "projects-no-mock.sqlite3",
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        created = client.post("/api/v2/projects", json=project_payload())
        assert created.status_code == 201
        project_id = created.json()["project_id"]
        route = f"/api/v2/projects/{project_id}"
        assert client.get(route).status_code == 200
        assert client.get(f"{route}/runs").json() == []
        assert client.get("/api/v2/projects").json()[0]["project_id"] == project_id
        status = client.get(f"{route}/chat-status")
        assert status.status_code == 200
        assert status.json()["mode"] == "unavailable"
        assert status.json()["can_send"] is False
        assert status.json()["organisational_context_available"] is False
        assert client.get(
            f"{route}/chat-status",
            headers={"Authorization": f"Bearer {BOB_TOKEN}"},
        ).status_code == 404
        assert client.get(f"{route}/chat-status", headers={"Authorization": ""}).status_code == 401

        brief = client.post(f"{route}/briefs", json=inputs().brief.model_dump(mode="json"))
        assert brief.status_code == 503
        conversation = client.post(f"{route}/conversations").json()
        message = client.post(
            f"{route}/conversations/{conversation['conversation_id']}/messages",
            json={
                "message": "Show me real evidence",
                "expected_revision": 0,
                "idempotency_key": "not-mocked",
            },
        )
        assert message.status_code == 200
        turn = message.json()["turns"][0]
        assert turn["status"] == "failed"
        assert turn["mode"] is None
        assert turn["answer"] is None
        assert "manual setup" in turn["error"]
        assert client.get(f"{route}/conversations").json()[0]["conversation_id"] == conversation["conversation_id"]


def test_mock_project_chat_status_is_explicit(tmp_path):
    app = create_app(
        tmp_path / "projects-explicit-mock.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=mock_policy(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        status = client.get(f"/api/v2/projects/{project['project_id']}/chat-status").json()
        assert status["mode"] == "mock"
        assert status["can_send"] is True
        assert status["organisational_context_available"] is False
