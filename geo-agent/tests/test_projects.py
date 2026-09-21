import asyncio

from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.evidence_assessment import BrandDefinition
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementEvent,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.persistence import SQLAlchemyMeasurementRepository
from geo_agent.project_chat import ProjectChatRequest, ProjectChatService, ProjectConversationStore
from geo_agent.projects import ProjectCreate
from test_artifacts import measurement_result, recommendation_report
from test_measurement_workflow import inputs


ALICE_TOKEN = "a" * 40
BOB_TOKEN = "b" * 40


def alice_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + ALICE_TOKEN}


def policy() -> MeasurementExecutionPolicy:
    return MeasurementExecutionPolicy(
        policy_id="project-tests",
        allowed_domains=("example.com",),
        locale="en-GB",
        profiles=inputs().profiles,
    )


def project_bound_policy() -> MeasurementExecutionPolicy:
    return MeasurementExecutionPolicy(
        policy_id="project-bound-tests",
        scope="project-bound",
        profiles=inputs().profiles,
    )


def project_payload(name: str = "Example Brand", domain: str = "example.com") -> dict:
    return {
        "name": name,
        "primary_domain": domain,
        "additional_domains": [],
        "default_locale": "en-GB",
        "active_goal": "Improve grounded discovery",
        "colour": "#0067b8",
    }


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


def test_project_chat_creates_and_completes_measurement_automatically(tmp_path):
    app = create_app(
        tmp_path / "agentic-project-chat.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
        measurement_auto_worker=True,
    )
    with TestClient(app, headers=alice_headers()) as client:
        project = client.post(
            "/api/v2/projects",
            json=project_payload(),
        ).json()
        conversation = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations",
        ).json()
        response = client.post(
            (
                f"/api/v2/projects/{project['project_id']}/conversations/"
                f"{conversation['conversation_id']}/messages"
            ),
            json={
                "message": "Measure https://example.com/category/ for grounded discovery",
                "expected_revision": conversation["revision"],
                "idempotency_key": "agentic-measurement-one",
                "use_organisational_context": False,
            },
        )
        assert response.status_code == 200, response.text
        saved = client.get(
            (
                f"/api/v2/projects/{project['project_id']}/conversations/"
                f"{conversation['conversation_id']}"
            ),
        ).json()
        assert saved["run_id"]
        assert saved["measurement_workflow"]["status"] == "completed"
        assert [turn["origin"] for turn in saved["turns"]] == ["user", "workflow"]
        assert saved["turns"][0]["mode"] == "workflow"
        assert saved["turns"][1]["mode"] == "mock"
        run = client.get(
            (
                f"/api/v2/projects/{project['project_id']}/runs/"
                f"{saved['run_id']}"
            ),
        ).json()
        assert run["state"] in {"ready", "partial"}
        assert run["approval"]["input_hash"] == run["approval_hash"]
        jobs = client.get(
            (
                f"/api/v2/projects/{project['project_id']}/runs/"
                f"{saved['run_id']}/jobs"
            ),
        ).json()
        assert [job["job_type"] for job in reversed(jobs)] == ["prepare", "evaluate"]


def test_second_run_bound_chat_does_not_steal_automatic_insights(tmp_path):
    app = create_app(
        tmp_path / "agentic-project-chat-binding.sqlite3",
        {ALICE_TOKEN: "alice"},
        measurement_policy=policy(),
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
        started = client.post(route, json={
            "message": "Measure https://example.com/category/ for pricing coverage",
            "expected_revision": original["revision"],
            "idempotency_key": "agentic-binding-start",
            "use_organisational_context": False,
        }).json()
        run_id = started["run_id"]
        second = client.post(
            f"/api/v2/projects/{project['project_id']}/conversations?run_id={run_id}",
        ).json()
        assert second["measurement_workflow"] is None

        from geo_agent.mock_runtime import MockMeasurementRuntime
        from geo_agent.persistence import SQLiteMeasurementRepository
        from geo_agent.project_chat import MockProjectAgent
        from geo_agent.project_chat_workflow import ProjectMeasurementChatWorkflow

        repository = SQLiteMeasurementRepository(tmp_path / "agentic-project-chat-binding.sqlite3")
        workflow = ProjectMeasurementChatWorkflow(repository, policy())
        agent = MockProjectAgent(repository)
        runtime = MockMeasurementRuntime(
            repository,
            policy(),
            on_job_finished=lambda job, run: workflow.reconcile_job(job, run, agent),
        )
        runtime.drain()

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
        assert [turn["origin"] for turn in saved_original["turns"]] == ["user", "workflow"]
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
        assert first.json()["turns"][-1]["mode"] == "mock"
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
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
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
    measurement = measurement_result()
    execution_policy = MeasurementExecutionPolicy(
        policy_id="project-results",
        allowed_domains=("example.org",),
        locale="en-GB",
        profiles=measurement.inputs.profiles,
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
        assert client.get(
            f"{route}/evidence/q-1-alternative",
        ).json()["evidence_id"] == "q-1-alternative"
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
        assert result["turns"][0]["status"] == "completed"
        assert result["turns"][0]["mode"] == "mock"
        assert "Organisational grounding is simulated" in result["turns"][0]["answer"]
        assert result["turns"][0]["citations"][0]["source_class"] == "geo-evidence"
        assert client.post(route, json=request).json() == result
        assert client.post(route, json={**request, "message": "Different"}).status_code == 409

        assert client.get(conversations).json()[0]["conversation_id"] == conversation["conversation_id"]
        bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
        assert client.get(
            f"{conversations}/{conversation['conversation_id']}",
            headers=bob_headers,
        ).status_code == 404


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
        measurement_policy=policy(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        project = client.post("/api/v2/projects", json=project_payload()).json()
        status = client.get(f"/api/v2/projects/{project['project_id']}/chat-status").json()
        assert status["mode"] == "mock"
        assert status["can_send"] is True
        assert status["organisational_context_available"] is False
