import json
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, ValidationError

from geo_agent.contracts import Contract
from geo_agent.foundry import azure_cli_token
from geo_agent.projects import FoundryProjectBinding, SpecialistAgentBinding
from geo_agent.specialist_agents import (
    SpecialistAgentCall,
    SpecialistAgentRole,
    SpecialistProviderMode,
)
from geo_agent.webiq import ProviderError


MAX_RESPONSE_BYTES = 1_000_000
MAX_OUTPUT_TOKENS = 2000


class SpecialistFoundrySettings(Contract):
    bindings: tuple[SpecialistAgentBinding, ...] = ()

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
    ) -> "SpecialistFoundrySettings":
        bindings = []
        for role, prefix in (
            (SpecialistAgentRole.RECOMMENDATIONS, "GEO_FOUNDRY_RECOMMENDATIONS_AGENT"),
            (SpecialistAgentRole.GROUNDING_QUERY, "GEO_FOUNDRY_GROUNDING_QUERY_AGENT"),
            (SpecialistAgentRole.LLM_SURVEY, "GEO_FOUNDRY_LLM_SURVEY_AGENT"),
        ):
            names = (f"{prefix}_ENDPOINT", f"{prefix}_NAME", f"{prefix}_VERSION")
            values = [environment.get(name, "").strip() for name in names]
            if not any(values):
                continue
            if not all(values):
                raise ValueError(f"Configure {', '.join(names)} together")
            endpoint, name, version = values
            parsed = urlsplit(endpoint)
            suffix = f"/agents/{name}/endpoint/protocols/openai/responses"
            if not parsed.path.endswith(suffix) or parsed.query or parsed.fragment:
                raise ValueError(
                    f"{names[0]} must be the Responses endpoint for the configured agent name"
                )
            project_endpoint = urlunsplit((
                parsed.scheme,
                parsed.netloc,
                parsed.path[:-len(suffix)],
                "",
                "",
            ))
            bindings.append(SpecialistAgentBinding(
                role=role,
                binding=FoundryProjectBinding(
                    project_endpoint=project_endpoint,
                    agent_name=name,
                    agent_version=version,
                ),
            ))
        return cls(bindings=tuple(bindings))

    def binding(
        self,
        role: SpecialistAgentRole,
    ) -> FoundryProjectBinding | None:
        return next(
            (item.binding for item in self.bindings if item.role == role),
            None,
        )


class ResponseContent(BaseModel):
    type: str
    text: str | None = None
    annotations: list[dict] = Field(default_factory=list)


class ResponseOutput(BaseModel):
    type: str
    role: str | None = None
    status: str | None = None
    content: list[ResponseContent] = Field(default_factory=list)


class AgentResponse(BaseModel):
    id: str = Field(min_length=1, max_length=500)
    status: str
    output: list[ResponseOutput]
    usage: dict | None = None
    content_filters: list[dict] = Field(default_factory=list)


class HostedSpecialistAgent:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str] = azure_cli_token,
        transport: httpx.BaseTransport | None = None,
    ):
        self.token_provider = token_provider
        self.transport = transport

    def invoke(
        self,
        binding: FoundryProjectBinding,
        role: SpecialistAgentRole,
        provider_mode: SpecialistProviderMode,
        context: dict,
        output_type: type[BaseModel],
    ) -> tuple[BaseModel, SpecialistAgentCall]:
        body = {
            "agent_reference": {
                "type": "agent_reference",
                "name": binding.agent_name,
                "version": binding.agent_version,
            },
            "input": [{
                "role": "user",
                "content": json.dumps({
                    "SPECIALIST_ROLE": role.value,
                    "CONTEXT": context,
                    "OUTPUT_CONTRACT": output_type.__name__,
                }, ensure_ascii=True),
            }],
            "store": False,
            "stream": False,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "tool_choice": "none",
        }
        url = f"{binding.project_endpoint}/openai/v1/responses"
        try:
            token = self.token_provider()
            with httpx.Client(
                transport=self.transport,
                timeout=90,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        raise ProviderError(
                            f"Foundry specialist agent request failed (HTTP {response.status_code}); "
                            "no automatic retry"
                        )
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise ProviderError(
                                "Foundry specialist agent response exceeded the size limit; "
                                "no automatic retry"
                            )
        except ProviderError:
            raise
        except (httpx.HTTPError, OSError, ValueError):
            raise ProviderError(
                "Foundry specialist agent connection failed or timed out; no automatic retry"
            ) from None
        try:
            payload = AgentResponse.model_validate_json(content)
        except ValidationError:
            raise ProviderError(
                "Foundry specialist agent returned an invalid response; no automatic retry"
            ) from None
        if payload.status != "completed" or any(
            item.get("blocked") for item in payload.content_filters
        ):
            raise ProviderError(
                "Foundry specialist agent returned an incomplete or blocked response; "
                "no automatic retry"
            )
        texts = []
        for item in payload.output:
            if item.type == "reasoning":
                continue
            if item.type != "message" or item.role != "assistant" or item.status != "completed":
                raise ProviderError(
                    "Foundry specialist agent returned unexpected output; no automatic retry"
                )
            for part in item.content:
                if part.type != "output_text" or not part.text or part.annotations:
                    raise ProviderError(
                        "Foundry specialist agent returned unsupported content; "
                        "no automatic retry"
                    )
                texts.append(part.text)
        text = "\n".join(texts).strip()
        try:
            parsed = output_type.model_validate_json(text)
        except (ValidationError, ValueError, TypeError):
            raise ProviderError(
                "Foundry specialist agent output did not match the required contract; "
                "no automatic retry"
            ) from None
        usage = payload.usage or {}
        return parsed, SpecialistAgentCall(
            role=role,
            provider_mode=provider_mode,
            provider_response_id=payload.id,
            agent_name=binding.agent_name,
            agent_version=binding.agent_version,
            input_tokens=usage.get("input_tokens")
            if isinstance(usage.get("input_tokens"), int) else None,
            output_tokens=usage.get("output_tokens")
            if isinstance(usage.get("output_tokens"), int) else None,
        )
