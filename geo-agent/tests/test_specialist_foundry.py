import json

import httpx
import pytest

from geo_agent.contracts import Contract
from geo_agent.projects import FoundryProjectBinding
from geo_agent.specialist_agents import (
    SpecialistAgentRole,
    SpecialistProviderMode,
)
from geo_agent.specialist_foundry import (
    HostedSpecialistAgent,
    SpecialistFoundrySettings,
)
from geo_agent.webiq import ProviderError


class SpecialistOutput(Contract):
    value: str


def response_payload(text: str, *, annotations=()):
    return {
        "id": "response-1",
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{
                "type": "output_text",
                "text": text,
                "annotations": list(annotations),
            }],
        }],
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }


def test_specialist_settings_require_complete_pinned_binding():
    with pytest.raises(ValueError, match="Configure"):
        SpecialistFoundrySettings.from_environment({
            "GEO_FOUNDRY_RECOMMENDATIONS_AGENT_NAME": "recommendations",
        })

    settings = SpecialistFoundrySettings.from_environment({
        "GEO_FOUNDRY_RECOMMENDATIONS_AGENT_ENDPOINT": (
            "https://example.services.ai.azure.com/api/projects/geo/"
            "agents/recommendations/endpoint/protocols/openai/responses"
        ),
        "GEO_FOUNDRY_RECOMMENDATIONS_AGENT_NAME": "recommendations",
        "GEO_FOUNDRY_RECOMMENDATIONS_AGENT_VERSION": "7",
    })

    assert settings.binding(SpecialistAgentRole.RECOMMENDATIONS) == FoundryProjectBinding(
        project_endpoint="https://example.services.ai.azure.com/api/projects/geo",
        agent_name="recommendations",
        agent_version="7",
    )
    assert settings.binding(SpecialistAgentRole.GROUNDING_QUERY) is None


def test_hosted_specialist_uses_pinned_agent_and_disables_tools_and_storage():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(
            200,
            json=response_payload(json.dumps({"value": "validated"})),
        )

    output, call = HostedSpecialistAgent(
        token_provider=lambda: "token",
        transport=httpx.MockTransport(handler),
    ).invoke(
        FoundryProjectBinding(
            project_endpoint="https://example.services.ai.azure.com/api/projects/geo",
            agent_name="recommendations",
            agent_version="7",
        ),
        SpecialistAgentRole.RECOMMENDATIONS,
        SpecialistProviderMode.ENVIRONMENT_AGENT,
        {"measurement_hash": "a" * 64},
        SpecialistOutput,
    )

    assert output == SpecialistOutput(value="validated")
    assert call.provider_response_id == "response-1"
    assert call.agent_version == "7"
    assert call.input_tokens == 12
    assert len(requests) == 1
    assert str(requests[0].url).endswith("/api/projects/geo/openai/v1/responses")
    body = json.loads(requests[0].content)
    assert body["agent_reference"] == {
        "type": "agent_reference",
        "name": "recommendations",
        "version": "7",
    }
    assert body["store"] is False
    assert body["stream"] is False
    assert body["tool_choice"] == "none"


@pytest.mark.parametrize(
    "payload",
    [
        response_payload("not-json"),
        response_payload(
            json.dumps({"value": "blocked"}),
            annotations=({"type": "url_citation"},),
        ),
        {
            "id": "response-1",
            "status": "completed",
            "output": [{
                "type": "function_call",
                "role": "assistant",
                "status": "completed",
                "content": [],
            }],
        },
    ],
)
def test_hosted_specialist_fails_closed_without_retry(payload):
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload)

    with pytest.raises(ProviderError, match="no automatic retry"):
        HostedSpecialistAgent(
            token_provider=lambda: "token",
            transport=httpx.MockTransport(handler),
        ).invoke(
            FoundryProjectBinding(
                project_endpoint="https://example.services.ai.azure.com/api/projects/geo",
                agent_name="recommendations",
                agent_version="7",
            ),
            SpecialistAgentRole.RECOMMENDATIONS,
            SpecialistProviderMode.PROJECT_AGENT,
            {},
            SpecialistOutput,
        )

    assert calls == 1
