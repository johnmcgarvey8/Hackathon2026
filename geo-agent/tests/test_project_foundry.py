import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from geo_agent.api import create_app
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.project_chat import ProjectChatRequest, ProjectChatService
from geo_agent.project_foundry import HostedProjectAgent, ProjectFoundrySettings
from geo_agent.projects import FoundryProjectBinding, ProjectCreate, ProjectUpdate
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict


ENDPOINT = "https://fixture.services.ai.azure.com/api/projects/test"


def settings(**changes):
    return ProjectFoundrySettings(
        default_agent=FoundryProjectBinding(
            project_endpoint=ENDPOINT,
            agent_name="shared-geo",
            agent_version="2",
        ),
        **changes,
    )


def completed_response(text="No measured evidence was supplied."):
    return {
        "id": "resp-test",
        "status": "completed",
        "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "usage": {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
    }


@pytest.fixture
def workspace(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "hosted.sqlite3")
    owner = OwnerIdentity(tenant_id="local", object_id="operator")
    projects = [
        repository.create_project(owner, ProjectCreate(name=name, primary_domain=domain))
        for name, domain in (("First Brand", "first.example"), ("Second Brand", "second.example"))
    ]
    return repository, owner, projects


def service_for(repository, handler, **kwargs):
    agent = HostedProjectAgent(
        repository, settings(**kwargs),
        token_provider=lambda: "dummy-azure-token",
        transport=httpx.MockTransport(handler),
    )
    return ProjectChatService(repository, agent)


def send(service, project, owner, conversation, text="What can you help with?", key="turn-1"):
    return asyncio.run(service.respond(
        project.project_id, conversation.conversation_id, owner,
        ProjectChatRequest(
            message=text, expected_revision=conversation.revision, idempotency_key=key,
        ),
    ))


def test_environment_extracts_agent_and_defaults_without_deployment_or_knowledge():
    result = ProjectFoundrySettings.from_environment({
        "GEO_FOUNDRY_AGENT_ENDPOINT": f"{ENDPOINT}/agents/shared-geo/endpoint/protocols/openai/responses",
        "GEO_FOUNDRY_AGENT_NAME": "shared-geo",
        "GEO_FOUNDRY_AGENT_VERSION": "2",
    })
    assert result == settings()
    assert result.default_agent.model_deployment is None
    assert result.default_agent.knowledge_base_id is None
    assert ProjectFoundrySettings.from_environment({}) is None
    with pytest.raises(ValueError, match="together"):
        ProjectFoundrySettings.from_environment({"GEO_FOUNDRY_AGENT_NAME": "shared-geo"})


@pytest.mark.parametrize("endpoint", [
    "http://fixture.services.ai.azure.com/api/projects/test",
    "https://attacker.example/api/projects/test",
    "https://fixture.services.ai.azure.com.attacker.example/api/projects/test",
    "https://127.0.0.1/api/projects/test",
    "https://user:pass@fixture.services.ai.azure.com/api/projects/test",
    f"{ENDPOINT}/../other",
    f"{ENDPOINT}?redirect=https://attacker.example",
    "https://fixture.services.ai.azure.com:444/api/projects/test",
])
def test_endpoint_cannot_forward_credentials_to_unapproved_destinations(endpoint):
    with pytest.raises(ValidationError):
        FoundryProjectBinding(project_endpoint=endpoint, agent_name="shared", agent_version="2")


def test_pinned_agent_request_persists_response_and_replay_does_not_spend(workspace):
    repository, owner, projects = workspace
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert str(request.url) == f"{ENDPOINT}/openai/v1/responses"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer dummy-azure-token"
        assert body["agent_reference"] == {"name": "shared-geo", "version": "2", "type": "agent_reference"}
        assert body["tool_choice"] == "none"
        assert body["store"] is False
        assert body["stream"] is False
        assert body["max_output_tokens"] == 2000
        assert "conversation" not in body and "previous_response_id" not in body
        assert "instructions" not in body and "model" not in body
        assert owner.object_id not in request.content.decode()
        return httpx.Response(200, json=completed_response())

    service = service_for(repository, handler)
    original = service.store.create(projects[0].project_id, owner)
    result = send(service, projects[0], owner, original)
    assert result.turns[-1].status == "completed"
    assert result.turns[-1].agent_name == "shared-geo"
    assert result.turns[-1].agent_version == "2"
    assert len(result.turns[-1].context_hash) == 64
    assert result.turns[-1].usage["total_tokens"] == 18
    assert result.turns[-1].provider_response_id == "resp-test"
    assert result.turns[-1].mode == "foundry"
    assert send(service, projects[0], owner, original) == result
    assert len(calls) == 1
    assert service.agent.budget(projects[0])["used"] == 1
    assert service.store.get(projects[0].project_id, original.conversation_id, owner) == result


def test_shared_agent_keeps_each_project_context_and_history_separate(workspace):
    repository, owner, projects = workspace
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completed_response())

    service = service_for(repository, handler)
    first = service.store.create(projects[0].project_id, owner)
    second = service.store.create(projects[1].project_id, owner)
    first = send(service, projects[0], owner, first, text="First-only private draft")
    send(service, projects[1], owner, second, text="Second-only private draft")
    send(service, projects[0], owner, first, text="Continue first", key="turn-2")
    second_wire = json.dumps(requests[1])
    first_wire = json.dumps(requests[2])
    assert "First-only" not in second_wire and "first.example" not in second_wire
    assert "Second-only" not in first_wire and "second.example" not in first_wire
    assert "First-only private draft" in first_wire
    assert "Second Brand" in second_wire
    assert service.agent.budget(projects[1])["used"] == 3


def test_budget_persists_across_restart_projects_and_agent_versions(workspace):
    repository, owner, projects = workspace
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completed_response())

    service = service_for(repository, handler, max_requests=1)
    first = service.store.create(projects[0].project_id, owner)
    send(service, projects[0], owner, first)
    restarted = HostedProjectAgent(SQLiteMeasurementRepository(Path(repository.engine.url.database)), settings(max_requests=1))
    assert restarted.budget(projects[1])["remaining"] == 0
    changed = repository.update_project(projects[1].project_id, owner, ProjectUpdate(
        expected_revision=1,
        foundry=FoundryProjectBinding(project_endpoint=ENDPOINT, agent_name="different", agent_version="3"),
    ))
    assert restarted.status(changed)["can_send"] is False
    second = service.store.create(projects[1].project_id, owner)
    assert send(service, projects[1], owner, second).turns[-1].status == "failed"
    assert len(calls) == 1
    with pytest.raises(Conflict, match="cannot reset"):
        HostedProjectAgent(repository, settings(max_requests=2)).budget(changed)


@pytest.mark.parametrize("status", [302, 400, 401, 403, 404, 429, 500])
def test_provider_errors_are_sanitized_charged_and_never_retried(workspace, status):
    repository, owner, projects = workspace
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://attacker.example"}, json={"detail": "sensitive-provider-detail"})

    service = service_for(repository, handler)
    conversation = service.store.create(projects[0].project_id, owner)
    result = send(service, projects[0], owner, conversation)
    turn = result.turns[-1]
    assert turn.status == "failed"
    assert "sensitive-provider-detail" not in turn.error
    assert turn.answer is None
    assert len(calls) == 1
    assert service.agent.budget(projects[0])["used"] == 1
    assert send(service, projects[0], owner, conversation) == result
    assert len(calls) == 1


def test_timeouts_finish_turn_and_do_not_replay(workspace):
    repository, owner, projects = workspace
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private details")

    service = service_for(repository, handler)
    conversation = service.store.create(projects[0].project_id, owner)
    result = send(service, projects[0], owner, conversation)
    assert result.turns[-1].status == "failed"
    assert "may have been processed" in result.turns[-1].error
    assert send(service, projects[0], owner, conversation) == result
    assert len(calls) == 1


def test_credentials_failure_does_not_spend(workspace):
    repository, owner, projects = workspace

    def no_credentials():
        raise ProviderError("Azure CLI is missing; sign in locally before using Foundry")

    service = ProjectChatService(repository, HostedProjectAgent(
        repository, settings(), token_provider=no_credentials,
    ))
    conversation = service.store.create(projects[0].project_id, owner)
    result = send(service, projects[0], owner, conversation)
    assert "Azure CLI is missing" in result.turns[-1].error
    assert service.agent.budget(projects[0])["used"] == 0


@pytest.mark.parametrize("case", ["tool", "annotation", "incomplete", "empty", "oversized", "invalid"])
def test_unsupported_response_is_not_presented_as_success(workspace, case):
    repository, owner, projects = workspace
    payload = completed_response()
    if case == "tool":
        payload["output"] = [{"type": "mcp_call"}]
    elif case == "annotation":
        payload["output"][0]["content"][0]["annotations"] = [{"type": "url_citation", "url": "https://private.example"}]
    elif case == "incomplete":
        payload["status"] = "incomplete"
    elif case == "empty":
        payload["output"] = []
    elif case == "oversized":
        payload["output"][0]["content"][0]["text"] = "x" * 1_000_001
    elif case == "invalid":
        payload = {"unexpected": "response"}
    service = service_for(repository, lambda request: httpx.Response(200, json=payload))
    conversation = service.store.create(projects[0].project_id, owner)
    result = send(service, projects[0], owner, conversation)
    assert result.turns[-1].status == "failed"
    assert result.turns[-1].answer is None
    assert service.agent.budget(projects[0])["used"] == 1


def test_parallel_budget_reservations_cannot_exceed_shared_limit(workspace):
    repository, owner, projects = workspace
    agent = HostedProjectAgent(repository, settings(max_requests=1))
    agent.budget(projects[0])

    def reserve(project):
        try:
            agent.reserve(project)
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, projects))
    assert sorted(results) == [False, True]
    assert agent.budget(projects[0])["used"] == 1


def test_status_uses_shared_default_for_current_and_future_projects(tmp_path):
    token = "a" * 40
    app = create_app(tmp_path / "api.sqlite3", {token: "operator", "b" * 40: "other"}, project_foundry=settings())
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        for domain in ("first.example", "second.example"):
            project = client.post("/api/v2/projects", json={"name": domain, "primary_domain": domain}).json()
            assert project["foundry"] is None
            status = client.get(f"/api/v2/projects/{project['project_id']}/chat-status").json()
            assert status["mode"] == "foundry"
            assert status["can_send"] is True
            assert status["agent"] == {"name": "shared-geo", "version": "2", "scope": "shared-default"}
            assert status["organisational_context_available"] is False
            assert status["budget"] == {"limit": 6, "used": 0, "remaining": 6}
            assert client.get(
                f"/api/v2/projects/{project['project_id']}/chat-status",
                headers={"Authorization": f"Bearer {'b' * 40}"},
            ).status_code == 404
