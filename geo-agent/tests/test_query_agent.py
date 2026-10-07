import json

import httpx
import pytest
from pydantic import ValidationError

from geo_agent.contracts import Brief, PageSnapshot, QueryPlan
from geo_agent.query_agent import HostedQueryPlanner, QueryAgentSettings
from geo_agent.webiq import ProviderError, ProviderFailure


PROTOCOL_ENDPOINT = (
    "https://hackathon-2026-geo-optimiser.services.ai.azure.com/api/projects/"
    "proj-default/agents/MissionsAndMoments/endpoint/protocols/openai/responses"
)
PROJECT_ENDPOINT = (
    "https://hackathon-2026-geo-optimiser.services.ai.azure.com/api/projects/proj-default"
)
MISSIONS = (
    ("functional-planning", "before-journey"),
    ("functional-constraint", "early-journey"),
    ("transition-to-discovery", "mid-journey"),
    ("emotive-discovery", "inspiration"),
    ("decision-validation", "point-of-decision"),
)


def inputs():
    brief = Brief(
        url="https://example.com/page",
        audience="Marketing leaders",
        goal="Compare GEO visibility",
        locale="en-GB",
    )
    snapshot = PageSnapshot(
        url=brief.url,
        title="Example",
        content="Evidence for mission and moment queries.",
        provenance="live",
    )
    plan = QueryPlan.model_validate({
        "queries": [
            {
                "query_id": f"q-{index}",
                "priority": index,
                "mission": MISSIONS[index - 1][0],
                "moment": MISSIONS[index - 1][1],
                "rationale": "Relevant to the measurement goal",
                "intent": f"Compare option {index}",
                "branded": False,
                "chat_query": f"Which option supports mission {index}?",
                "grounding_query": f"option mission {index}",
                "evidence": [{
                    "evidence_id": "page-1",
                    "quote": "Evidence for mission and moment queries.",
                }],
            }
            for index in range(1, 6)
        ],
    })
    return brief, snapshot, plan


def response(plan, *, output_prefix=()):
    return {
        "id": "resp-query-agent",
        "status": "completed",
        "output": [
            *output_prefix,
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": plan.model_dump_json(),
                    "annotations": [],
                }],
            },
        ],
        "usage": {"input_tokens": 25, "output_tokens": 150, "total_tokens": 175},
        "content_filters": [],
    }


def settings():
    return QueryAgentSettings(
        protocol_endpoint=PROTOCOL_ENDPOINT,
        agent_version="7",
    )


def test_settings_extract_project_agent_and_require_complete_environment():
    configured = QueryAgentSettings.from_environment({
        "GEO_QUERY_AGENT_ENDPOINT": PROTOCOL_ENDPOINT,
        "GEO_QUERY_AGENT_VERSION": "7",
    })
    assert configured == settings()
    assert configured.project_endpoint == PROJECT_ENDPOINT
    assert configured.agent_name == "MissionsAndMoments"
    assert QueryAgentSettings.from_environment({}) is None
    with pytest.raises(ValueError, match="together"):
        QueryAgentSettings.from_environment({"GEO_QUERY_AGENT_VERSION": "7"})


@pytest.mark.parametrize("endpoint", [
    "http://hackathon-2026-geo-optimiser.services.ai.azure.com/api/projects/proj-default/agents/MissionsAndMoments/endpoint/protocols/openai/responses",
    "https://attacker.example/api/projects/proj-default/agents/MissionsAndMoments/endpoint/protocols/openai/responses",
    "https://hackathon-2026-geo-optimiser.services.ai.azure.com.attacker.example/api/projects/proj-default/agents/MissionsAndMoments/endpoint/protocols/openai/responses",
    "https://hackathon-2026-geo-optimiser.services.ai.azure.com/api/projects/proj-default/agents/MissionsAndMoments/endpoint/protocols/openai/responses?redirect=https://attacker.example",
    "https://hackathon-2026-geo-optimiser.services.ai.azure.com/api/projects/proj-default/agents/MissionsAndMoments",
])
def test_settings_reject_unsupported_endpoints(endpoint):
    with pytest.raises(ValidationError):
        QueryAgentSettings(protocol_endpoint=endpoint, agent_version="7")


def test_planner_pins_version_sends_brief_and_accepts_configured_tool_trace():
    brief, snapshot, plan = inputs()
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert str(request.url) == f"{PROJECT_ENDPOINT}/openai/v1/responses"
        assert request.headers["authorization"] == "Bearer test-token"
        assert body == {
            "agent_reference": {
                "type": "agent_reference",
                "name": "MissionsAndMoments",
                "version": "7",
            },
            "input": json.dumps({
                "url": str(brief.url),
                "locale": brief.locale,
                "audience": brief.audience,
                "goal": brief.goal,
            }, ensure_ascii=True),
            "store": False,
            "stream": False,
            "max_output_tokens": 2_000,
        }
        assert "tools" not in body and "tool_choice" not in body and "model" not in body
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=response(plan, output_prefix=(
                {"type": "mcp_list_tools", "status": "completed"},
                {"type": "mcp_call", "status": "completed"},
            )),
        )

    planner = HostedQueryPlanner(
        settings(),
        token_provider=lambda: "test-token",
        transport=httpx.MockTransport(handler),
    )
    generated, metadata = planner.propose_pairs(brief, snapshot)
    assert generated == plan
    assert metadata == {
        "model": "MissionsAndMoments@7",
        "response_id": "resp-query-agent",
        "input_tokens": 25,
        "output_tokens": 150,
    }
    assert len(requests) == 1


def test_planner_rejects_evidence_not_in_saved_snapshot():
    brief, snapshot, plan = inputs()
    invalid = plan.model_copy(update={
        "queries": tuple(
            query.model_copy(update={
                "evidence": tuple(
                    reference.model_copy(update={"quote": "Agent-only evidence"})
                    for reference in query.evidence
                ),
            })
            for query in plan.queries
        ),
    })
    planner = HostedQueryPlanner(
        settings(),
        token_provider=lambda: "test-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=response(invalid),
        )),
    )
    with pytest.raises(ProviderError) as caught:
        planner.propose_pairs(brief, snapshot)
    assert caught.value.code == ProviderFailure.PLAN_EVIDENCE


def test_planner_reassigns_exact_quote_to_saved_snapshot_passage():
    brief, snapshot, plan = inputs()
    snapshot = snapshot.model_copy(update={
        "content": ("x" * 1000) + "Evidence for mission and moment queries.",
    })
    planner = HostedQueryPlanner(
        settings(),
        token_provider=lambda: "test-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=response(plan),
        )),
    )
    generated, _metadata = planner.propose_pairs(brief, snapshot)
    assert {
        reference.evidence_id
        for query in generated.queries
        for reference in query.evidence
    } == {"page-2"}


def test_planner_drops_one_unsupported_quote_when_exact_evidence_remains():
    brief, snapshot, plan = inputs()
    plan = plan.model_copy(update={
        "queries": tuple(
            query.model_copy(update={
                "evidence": (
                    *query.evidence,
                    query.evidence[0].model_copy(update={"quote": "Unsupported agent quote"}),
                ),
            })
            for query in plan.queries
        ),
    })
    planner = HostedQueryPlanner(
        settings(),
        token_provider=lambda: "test-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=response(plan),
        )),
    )
    generated, _metadata = planner.propose_pairs(brief, snapshot)
    assert all(len(query.evidence) == 1 for query in generated.queries)


@pytest.mark.parametrize("status,code", [
    (403, ProviderFailure.AUTH),
    (429, ProviderFailure.RATE_LIMIT),
    (500, ProviderFailure.REQUEST),
])
def test_planner_maps_http_failures_without_retry(status, code):
    brief, snapshot, _plan = inputs()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"content-type": "application/json"},
            json={"error": {"message": "sensitive provider detail"}},
        )

    planner = HostedQueryPlanner(
        settings(),
        token_provider=lambda: "test-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderError) as caught:
        planner.propose_pairs(brief, snapshot)
    assert caught.value.code == code
    assert "sensitive" not in str(caught.value)
    assert len(calls) == 1


def test_planner_rejects_non_json_query_plan():
    brief, snapshot, plan = inputs()
    payload = response(plan)
    payload["output"][-1]["content"][0]["text"] = "```json\n{}\n```"
    planner = HostedQueryPlanner(
        settings(),
        token_provider=lambda: "test-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=payload,
        )),
    )
    with pytest.raises(ProviderError) as caught:
        planner.propose_pairs(brief, snapshot)
    assert caught.value.code == ProviderFailure.SCHEMA
