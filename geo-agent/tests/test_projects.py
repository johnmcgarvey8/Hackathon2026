import asyncio

from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.persistence import SQLAlchemyMeasurementRepository
from geo_agent.project_chat import ProjectChatRequest, ProjectChatService, ProjectConversationStore
from geo_agent.projects import ProjectCreate
from test_measurement_workflow import inputs


ALICE_TOKEN = "a" * 40
BOB_TOKEN = "b" * 40


def policy() -> MeasurementExecutionPolicy:
    return MeasurementExecutionPolicy(
        policy_id="project-tests",
        allowed_domains=("example.com",),
        locale="en-GB",
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
