import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from geo_agent.contracts import Brief, SimulationProfile
from geo_agent.evaluation_workflow import EvaluationHandler, EvaluationRequest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobService, JobType
from geo_agent.live_runtime import LiveMeasurementRuntime
from geo_agent.measurement_budget import MeasurementBudgetGrant, MeasurementOperationAllowances
from geo_agent.measurement_workflow import MeasurementCoordinator, MeasurementState, OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.preparation import PreparationHandler, PreparationRequest
from geo_agent.providers import ClaudeMessagesEvaluator, OpenAIResponsesEvaluator, simulation_instructions
from geo_agent.webiq import BROWSE_ENDPOINT, SEARCH_ENDPOINT
from test_foundry import response_payload
from test_providers import claude_payload


def live_policy(
    profile_ids=("chatgpt-style", "claude-backed", "copilot-style"),
) -> MeasurementExecutionPolicy:
    endpoint = "https://geo-runtime.services.ai.azure.com"
    profiles = tuple(
        SimulationProfile(
            profile_id=profile_id,
            provider="anthropic-messages" if profile_id == "claude-backed" else "openai-responses",
            deployment=f"test-{profile_id}",
            endpoint=f"{endpoint}/anthropic" if profile_id == "claude-backed" else f"{endpoint}/openai/v1/",
            prompt_version="geo-evaluator-v2",
            instructions=simulation_instructions(profile_id),
        )
        for profile_id in profile_ids
    )
    return MeasurementExecutionPolicy(
        policy_id="clarity-live-runtime-test",
        execution_mode="live",
        allowed_domains=("clarity.microsoft.com",),
        locale="en-GB",
        profiles=profiles,
        budget_grant_id="clarity-live-runtime-test-grant",
    )


def budget_grant(policy, owner=None, *, recommendations=False, authorized_runs=1):
    return MeasurementBudgetGrant(
        grant_id=policy.budget_grant_id,
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash,
        owner=owner or OwnerIdentity(tenant_id="tenant-a", object_id="user-a"),
        allowances=MeasurementOperationAllowances(
            webiq_browse=authorized_runs,
            page_analysis_model=authorized_runs,
            paired_query_plan=authorized_runs,
            webiq_search=5 * authorized_runs,
            profile_evaluator=5 * len(policy.profiles) * authorized_runs,
            recommendation_model=authorized_runs if recommendations else 0,
        ),
        maximum_authorized_cost_usd=Decimal("50.00"),
        approval="Approved intercepted live-runtime test budget",
        approved_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )


def test_live_runtime_composes_real_adapters_without_provider_calls(tmp_path):
    def reject_token_call() -> str:
        pytest.fail("Runtime construction must not acquire a provider token")

    repository = SQLiteMeasurementRepository(tmp_path / "runtime.sqlite3")
    execution_policy = live_policy()
    runtime = LiveMeasurementRuntime(
        repository,
        execution_policy,
        budget_grant=budget_grant(execution_policy),
        webiq_api_key="test-webiq-key",
        preparation_endpoint="https://geo-runtime.services.ai.azure.com/openai/v1/",
        preparation_deployment="test-preparation",
        token_provider=reject_token_call,
    )

    preparation = runtime.worker.handlers[JobType.PREPARE]
    evaluation = runtime.worker.handlers[JobType.EVALUATE]
    assert isinstance(preparation, PreparationHandler)
    assert isinstance(evaluation, EvaluationHandler)
    assert isinstance(evaluation.evaluators["chatgpt-style"], OpenAIResponsesEvaluator)
    assert isinstance(evaluation.evaluators["claude-backed"], ClaudeMessagesEvaluator)
    assert isinstance(evaluation.evaluators["copilot-style"], OpenAIResponsesEvaluator)


def test_live_runtime_accepts_one_profile_and_rejects_two(tmp_path):
    single_profile_policy = live_policy(("chatgpt-style",))
    runtime = LiveMeasurementRuntime(
        SQLiteMeasurementRepository(tmp_path / "single.sqlite3"),
        single_profile_policy,
        budget_grant=budget_grant(single_profile_policy),
        webiq_api_key="test-webiq-key",
        preparation_endpoint="https://geo-runtime.services.ai.azure.com/openai/v1/",
        preparation_deployment="test-preparation",
        token_provider=lambda: "test-token",
    )
    assert set(runtime.worker.handlers[JobType.EVALUATE].evaluators) == {"chatgpt-style"}

    invalid_payload = live_policy().model_dump(mode="json")
    invalid_payload["profiles"] = invalid_payload["profiles"][:2]
    with pytest.raises(ValueError, match="one- or three-profile roster"):
        MeasurementExecutionPolicy.model_validate(invalid_payload)


def test_live_runtime_rejects_mock_policy(tmp_path):
    execution_policy = live_policy().model_copy(update={"execution_mode": "mock", "budget_grant_id": None})
    repository = SQLiteMeasurementRepository(tmp_path / "runtime.sqlite3")

    with pytest.raises(ValueError, match="requires a live execution policy"):
        LiveMeasurementRuntime(
            repository,
            execution_policy,
            budget_grant=budget_grant(live_policy()),
            webiq_api_key="test-webiq-key",
            preparation_endpoint="https://geo-runtime.services.ai.azure.com/openai/v1/",
            preparation_deployment="test-preparation",
        )


def test_live_runtime_executes_bounded_durable_score_only_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: pytest.fail("Must not resolve DNS"))
    calls = Counter()
    page_text = "Microsoft Clarity helps product teams understand website behaviour and user journeys."
    quote = "Microsoft Clarity helps product teams"
    queries = [{
        "query_id": f"q-{index}",
        "priority": index,
        "rationale": f"Compare behavioural analytics option {index}",
        "intent": f"Discover analytics tools {index}",
        "branded": False,
        "chat_query": f"Which behavioural analytics tools support use case {index}?",
        "grounding_query": f"behavioural analytics tools use case {index}",
        "evidence": [{"evidence_id": "page-1", "quote": quote}],
    } for index in range(1, 6)]
    query_ids = {query["chat_query"]: query["query_id"] for query in queries}

    def handler(request):
        body = json.loads(request.content)
        url = str(request.url)
        if url == BROWSE_ENDPOINT:
            calls["browse"] += 1
            return httpx.Response(200, json={
                "url": body["url"],
                "title": "Microsoft Clarity",
                "content": page_text,
                "traceId": "trace-browse",
            })
        if url == SEARCH_ENDPOINT:
            calls["search"] += 1
            query_id = f"q-{body['query'].rsplit(' ', 1)[-1]}"
            return httpx.Response(200, json={
                "traceId": f"trace-{query_id}",
                "webResults": [{
                    "url": "https://clarity.microsoft.com/",
                    "title": "Microsoft Clarity",
                    "content": "Behavioural analytics evidence from the exact target page.",
                }],
            })
        schema = body.get("text", {}).get("format", {}).get("name")
        if schema == "PreparationAnalysis":
            calls["page-analysis"] += 1
            finding = {
                "text": "Website behavioural analytics",
                "basis": "observed",
                "evidence": [{"passage_id": "page-1", "quote": quote}],
            }
            return httpx.Response(200, json=response_payload({
                "brand": {
                    "definition": {"name": "Microsoft Clarity", "aliases": [{"text": "Clarity", "ambiguous": True}],
                                   "domains": ["clarity.microsoft.com"]},
                    "evidence": finding["evidence"], "rationale": "The page identifies Microsoft Clarity.",
                },
                "purpose": finding,
                "audience": {**finding, "basis": "inferred"},
                "entities": [],
                "questions_answered": [],
                "observations": [],
                "improvements": [],
            }))
        if schema == "QueryPlan":
            calls["query-plan"] += 1
            return httpx.Response(200, json=response_payload({"queries": queries}))
        if url.endswith("/anthropic/v1/messages"):
            calls["claude-backed"] += 1
            payload = json.loads(body["messages"][0]["content"])
            query_id = query_ids[payload["query"]]
            citation_id = f"{query_id}-source-1"
            return httpx.Response(200, json=claude_payload({
                "answer": f"Supported [{citation_id}]",
                "citation_ids": [citation_id],
            }))
        calls[body["model"].removeprefix("test-")] += 1
        payload = json.loads(body["input"])
        query_id = query_ids[payload["query"]]
        citation_id = f"{query_id}-source-1"
        return httpx.Response(200, json=response_payload({
            "answer": f"Supported [{citation_id}]",
            "citation_ids": [citation_id],
        }))

    repository = SQLiteMeasurementRepository(tmp_path / "runtime.sqlite3")
    execution_policy = live_policy()
    runtime = LiveMeasurementRuntime(
        repository,
        execution_policy,
        budget_grant=budget_grant(execution_policy),
        webiq_api_key="test-webiq-key",
        preparation_endpoint="https://geo-runtime.services.ai.azure.com/openai/v1/",
        preparation_deployment="test-preparation",
        token_provider=lambda: "test-token",
        webiq_transport=httpx.MockTransport(handler),
        foundry_transport=httpx.MockTransport(handler),
        webiq_url_validator=lambda value: value,
    )
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    brief = Brief(
        url="https://clarity.microsoft.com/",
        audience="Digital marketers and product teams",
        goal="Compare website behavioural analytics tools",
        locale="en-GB",
    )
    draft = repository.create(owner, brief=brief)
    JobService(repository).enqueue(
        draft.run_id,
        owner,
        draft.revision,
        JobType.PREPARE,
        "prepare-live-runtime",
        PreparationRequest(brief=brief, confirm_preparation_calls=True),
    )

    runtime.run_once()
    prepared = repository.get(draft.run_id, owner)
    assert prepared.state == MeasurementState.AWAITING_APPROVAL
    assert prepared.inputs is not None
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
        "evaluate-live-runtime",
        EvaluationRequest(confirm_evaluation_calls=True),
    )

    runtime.run_once()
    completed = repository.get(draft.run_id, owner)
    assert completed.state == MeasurementState.READY
    assert completed.measurement is not None
    assert len(completed.measurement.retrievals) == 5
    assert len(completed.measurement.results) == 15
    assert all(result.provenance == "live" for result in completed.measurement.results)
    assert calls == {
        "browse": 1,
        "page-analysis": 1,
        "query-plan": 1,
        "search": 5,
        "chatgpt-style": 5,
        "claude-backed": 5,
        "copilot-style": 5,
    }
    with sqlite3.connect(tmp_path / "runtime.sqlite3") as connection:
        operation_count = connection.execute("SELECT COUNT(*) FROM operation_claims").fetchone()[0]
        budget_count = connection.execute(
            "SELECT SUM(consumed) FROM measurement_budget_usage"
        ).fetchone()[0]
    assert operation_count == 23
    assert budget_count == 23