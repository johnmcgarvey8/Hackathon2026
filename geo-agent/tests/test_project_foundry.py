import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from geo_agent.api import create_app
from geo_agent.contracts import Brief
from geo_agent.evidence_assessment import BrandDefinition
from geo_agent.geo_context import MAX_GEO_CONTEXT_BYTES, RESULT_DISTINCTIONS
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementEvent,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.persistence import (
    SQLiteMeasurementRepository,
    project_agent_budgets,
    project_conversations,
)
from geo_agent.project_chat import (
    ProjectChatRequest,
    ProjectChatService,
    ProjectConversationStore,
)
from geo_agent.project_foundry import HostedProjectAgent, ProjectFoundrySettings
from geo_agent.projects import FoundryProjectBinding, ProjectCreate, ProjectUpdate
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict
from test_artifacts import measurement_result, recommendation_report


ENDPOINT = "https://fixture.services.ai.azure.com/api/projects/test"


def settings(**changes):
    assert not changes
    return ProjectFoundrySettings(
        default_agent=FoundryProjectBinding(
            project_endpoint=ENDPOINT,
            agent_name="shared-geo",
            agent_version="2",
        ),
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
    assert ProjectFoundrySettings.from_environment({
        "GEO_FOUNDRY_AGENT_ENDPOINT": f"{ENDPOINT}/agents/shared-geo/endpoint/protocols/openai/responses",
        "GEO_FOUNDRY_AGENT_NAME": "shared-geo",
        "GEO_FOUNDRY_AGENT_VERSION": "2",
        "GEO_FOUNDRY_MAX_REQUESTS": "1",
    }) == settings()
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
    assert service.store.get(projects[0].project_id, original.conversation_id, owner) == result


def test_unbound_evaluation_request_routes_to_saved_project_measurement_without_provider_call(
    workspace,
):
    repository, owner, projects = workspace
    project = projects[0]
    run = repository.create(
        owner,
        brief=Brief(
            url="https://first.example/category/",
            audience="Category shoppers",
            goal="Evaluate the landing page",
            locale="en-GB",
        ),
        project_id=project.project_id,
    )

    def reject_provider(_request):
        pytest.fail("Workflow routing must not make a Foundry request")

    service = service_for(repository, reject_provider)
    conversation = service.store.create(project.project_id, owner)
    result = send(
        service,
        project,
        owner,
        conversation,
        text="Please evaluate this landing page: https://first.example/category/",
    )
    turn = result.turns[-1]
    assert turn.status == "completed"
    assert turn.mode == "workflow"
    assert run.run_id in turn.answer
    assert "Confirm live preparation" in turn.answer
    assert "Copied HTML or screenshots are not required" in turn.answer
    assert turn.provider_response_id is None


def test_unbound_workflow_follow_up_reports_approved_queries_are_ready_to_start(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "approved-workflow.sqlite3")
    owner = OwnerIdentity(tenant_id="local", object_id="operator")
    project = repository.create_project(
        owner,
        ProjectCreate(name="Approved Project", primary_domain="example.com"),
    )
    measurement = measurement_result()
    run = repository.create(owner, measurement.inputs, project_id=project.project_id)
    approved = MeasurementCoordinator(repository).approve(
        run.run_id,
        owner,
        run.revision,
        measurement.inputs.approval_hash,
    )

    def reject_provider(_request):
        pytest.fail("Approved workflow guidance must not make a Foundry request")

    service = service_for(repository, reject_provider)
    conversation = service.store.create(project.project_id, owner)
    result = send(
        service,
        project,
        owner,
        conversation,
        text="I approved the query hash. What is the next step?",
    )
    turn = result.turns[-1]
    assert turn.mode == "workflow"
    assert approved.run_id in turn.answer
    assert "saved queries are approved" in turn.answer
    assert "Start approved run" in turn.answer


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


def test_context_v2_is_deterministic_bounded_and_bound_to_one_run(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "context-v2.sqlite3")
    owner = OwnerIdentity(tenant_id="local", object_id="operator")
    project = repository.create_project(
        owner,
        ProjectCreate(name="Example Project", primary_domain="example.org"),
    )
    unrelated = repository.create_project(
        owner,
        ProjectCreate(name="Unrelated Private Project", primary_domain="private.example"),
    )
    measurement = measurement_result()
    run = repository.create(owner, measurement.inputs, project_id=project.project_id)
    approved = MeasurementCoordinator(repository).approve(
        run.run_id, owner, run.revision, measurement.inputs.approval_hash,
    )
    ready = repository.mutate(
        run.run_id,
        owner,
        approved.revision,
        lambda item: item.model_copy(update={
            "state": MeasurementState.READY,
            "measurement": measurement,
            "recommendations": recommendation_report(measurement),
            "events": (*item.events, MeasurementEvent(
                sequence=len(item.events) + 1,
                event_type="ready",
            )),
        }),
    )
    repository.save_brand_definition(
        ready.run_id,
        owner,
        BrandDefinition(name=project.name, domains=project.domains),
        0,
    )
    repository.create(
        owner,
        brief=Brief.model_validate({
            **measurement.inputs.brief.model_dump(mode="json"),
            "url": "https://private.example/secret",
        }),
        project_id=unrelated.project_id,
    )
    agent = HostedProjectAgent(repository, settings())
    conversation = ProjectChatService(repository, agent).store.create(
        project.project_id, owner, run_id=ready.run_id,
    )

    first, citations = agent.context(project, conversation)
    second, second_citations = agent.context(project, conversation)

    assert first == second
    assert citations == second_citations
    changed_project = repository.update_project(
        project.project_id,
        owner,
        ProjectUpdate(
            expected_revision=project.revision,
            name="Renamed Project",
            additional_domains=("changed.example",),
            active_goal="Changed after conversation creation",
        ),
    )
    repository.save_brand_definition(
        ready.run_id,
        owner,
        BrandDefinition(
            name="Changed Brand",
            domains=("changed.example",),
        ),
        1,
    )
    after_project_changes, changed_citations = agent.context(
        changed_project,
        conversation,
    )
    assert after_project_changes == first
    assert changed_citations == citations
    assert first["schema_version"] == "geo-context/v2"
    assert len(first["context_hash"]) == 64
    assert len(json.dumps(first, sort_keys=True).encode()) <= MAX_GEO_CONTEXT_BYTES
    assert tuple(first["citation_performance"]["result_distinctions"]) == RESULT_DISTINCTIONS
    assert {
        "query_plan",
        "webiq_evidence",
        "model_answers",
        "citation_performance",
        "brand_presence",
        "recommendations",
        "limitations_and_provenance",
    }.issubset(first)
    assert first["run"]["run_id"] == ready.run_id
    assert "Unrelated Private Project" not in json.dumps(first)
    assert "private.example/secret" not in json.dumps(first)
    assert {citation.source_id for citation in citations} >= {
        ready.run_id,
        f"{ready.run_id}-query-q-1",
        f"{ready.run_id}-answer-q-1-chatgpt-style",
        f"{ready.run_id}-evidence-q-1-target",
    }
    referenced = {
        f"{ready.run_id}-query-q-1",
        f"{ready.run_id}-evidence-q-1-target",
    }
    service = service_for(
        repository,
        lambda request: httpx.Response(
            200,
            json=completed_response(
                "Supported references: "
                + " ".join(f"[{source_id}]" for source_id in sorted(referenced))
                + " [unrelated-run-evidence]."
            ),
        ),
    )
    result = send(service, project, owner, conversation)
    assert {item.source_id for item in result.turns[-1].citations} == referenced
    assert result.turns[-1].context_hash == first["context_hash"]


def test_legacy_allowance_rows_are_untouched_by_hosted_chat_and_restart(workspace):
    repository, owner, projects = workspace
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=completed_response())

    with repository.engine.begin() as connection:
        connection.execute(project_agent_budgets.insert().values(
            owner_key=owner.key,
            request_limit=1,
            used=1,
        ))
    service = service_for(repository, handler)
    first = service.store.create(projects[0].project_id, owner)
    send(service, projects[0], owner, first)
    restarted = HostedProjectAgent(
        SQLiteMeasurementRepository(Path(repository.engine.url.database)),
        settings(),
    )
    changed = repository.update_project(projects[1].project_id, owner, ProjectUpdate(
        expected_revision=1,
        foundry=FoundryProjectBinding(project_endpoint=ENDPOINT, agent_name="different", agent_version="3"),
    ))
    assert restarted.status(changed)["can_send"] is True
    second = service.store.create(projects[1].project_id, owner)
    assert send(service, projects[1], owner, second).turns[-1].status == "completed"
    assert len(calls) == 2
    with repository.engine.connect() as connection:
        row = connection.execute(project_agent_budgets.select()).one()
    assert row.owner_key == owner.key
    assert row.request_limit == 1
    assert row.used == 1


def test_legacy_run_bound_conversation_backfills_and_persists_context(workspace):
    repository, owner, projects = workspace
    project = projects[0]
    measurement = measurement_result()
    run = repository.create(
        owner,
        measurement.inputs,
        brand_definition=BrandDefinition(
            name=project.name,
            domains=project.domains,
        ),
        project_id=project.project_id,
    )
    store = ProjectConversationStore(repository)
    conversation = store.create(project.project_id, owner, run.run_id)
    legacy = conversation.model_copy(update={"bound_project_context": None})
    with repository.engine.begin() as connection:
        connection.execute(project_conversations.update().where(
            project_conversations.c.conversation_id == conversation.conversation_id,
        ).values(payload=legacy.model_dump_json()))

    backfilled = store.get(
        project.project_id,
        conversation.conversation_id,
        owner,
    )
    assert backfilled.bound_project_context is not None
    before, before_citations = HostedProjectAgent(
        repository,
        settings(),
    ).context(project, backfilled)

    changed_project = repository.update_project(
        project.project_id,
        owner,
        ProjectUpdate(
            expected_revision=project.revision,
            name="Changed after backfill",
            active_goal="Changed after backfill",
        ),
    )
    repository.save_brand_definition(
        run.run_id,
        owner,
        BrandDefinition(name="Changed Brand", domains=("changed.example",)),
        1,
    )
    persisted = ProjectConversationStore(
        SQLiteMeasurementRepository(Path(repository.engine.url.database))
    ).get(project.project_id, conversation.conversation_id, owner)
    after, after_citations = HostedProjectAgent(
        repository,
        settings(),
    ).context(changed_project, persisted)

    assert persisted.bound_project_context == backfilled.bound_project_context
    assert after == before
    assert after_citations == before_citations


@pytest.mark.parametrize("status", [302, 400, 401, 403, 404, 429, 500])
def test_provider_errors_are_sanitized_saved_and_never_retried(workspace, status):
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


def test_credentials_failure_is_saved_without_provider_request(workspace):
    repository, owner, projects = workspace

    def no_credentials():
        raise ProviderError("Azure CLI is missing; sign in locally before using Foundry")

    service = ProjectChatService(repository, HostedProjectAgent(
        repository, settings(), token_provider=no_credentials,
    ))
    conversation = service.store.create(projects[0].project_id, owner)
    result = send(service, projects[0], owner, conversation)
    assert "Azure CLI is missing" in result.turns[-1].error


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


def test_one_in_flight_turn_per_conversation_is_retained(workspace):
    repository, owner, projects = workspace
    service = service_for(
        repository,
        lambda request: httpx.Response(200, json=completed_response()),
    )
    conversation = service.store.create(projects[0].project_id, owner)
    request = ProjectChatRequest(
        message="First explicit submission",
        expected_revision=0,
        idempotency_key="running-1",
    )
    claimed, created = service.store.claim(
        projects[0].project_id, conversation.conversation_id, owner, request,
    )
    assert created is True and claimed.turns[-1].status == "running"
    with pytest.raises(Conflict, match="already running"):
        service.store.claim(
            projects[0].project_id,
            conversation.conversation_id,
            owner,
            ProjectChatRequest(
                message="Second explicit submission",
                expected_revision=claimed.revision,
                idempotency_key="running-2",
            ),
        )


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
            assert "budget" not in status
            assert client.get(
                f"/api/v2/projects/{project['project_id']}/chat-status",
                headers={"Authorization": f"Bearer {'b' * 40}"},
            ).status_code == 404
