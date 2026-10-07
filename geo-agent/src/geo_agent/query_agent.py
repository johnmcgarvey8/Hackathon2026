import json
import re
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from geo_agent.contracts import Brief, Contract, PageSnapshot, QueryPlan
from geo_agent.foundry import _anchor_page_quote, azure_cli_token
from geo_agent.webiq import ProviderError, ProviderFailure


MAX_RESPONSE_BYTES = 100_000
MAX_OUTPUT_TOKENS = 2_000
_AGENT_PATH = re.compile(
    r"/api/projects/(?P<project>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"/agents/(?P<agent>[A-Za-z0-9][A-Za-z0-9._-]{0,99})"
    r"/endpoint/protocols/openai/responses"
)
_TOOL_OUTPUT_TYPES = {
    "computer_call",
    "file_search_call",
    "function_call",
    "mcp_call",
    "mcp_list_tools",
    "web_search_call",
}


def _endpoint_parts(endpoint: str) -> tuple[str, str]:
    parsed = urlsplit(endpoint.strip())
    match = _AGENT_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or not parsed.hostname.endswith(".services.ai.azure.com")
        or parsed.port not in {None, 443}
        or match is None
    ):
        raise ValueError("Use a direct Microsoft Foundry hosted-agent Responses endpoint")
    project_endpoint = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        f"/api/projects/{match.group('project')}",
        "",
        "",
    ))
    return project_endpoint, match.group("agent")


class QueryAgentSettings(Contract):
    protocol_endpoint: str = Field(min_length=1, max_length=1_000)
    agent_version: str = Field(pattern=r"^[1-9][0-9]*$")

    @model_validator(mode="after")
    def validate_endpoint(self) -> "QueryAgentSettings":
        _endpoint_parts(self.protocol_endpoint)
        return self

    @property
    def project_endpoint(self) -> str:
        return _endpoint_parts(self.protocol_endpoint)[0]

    @property
    def agent_name(self) -> str:
        return _endpoint_parts(self.protocol_endpoint)[1]

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "QueryAgentSettings | None":
        endpoint = environment.get("GEO_QUERY_AGENT_ENDPOINT", "").strip()
        version = environment.get("GEO_QUERY_AGENT_VERSION", "").strip()
        if not endpoint and not version:
            return None
        if not endpoint or not version:
            raise ValueError(
                "Configure GEO_QUERY_AGENT_ENDPOINT and GEO_QUERY_AGENT_VERSION together"
            )
        return cls(protocol_endpoint=endpoint, agent_version=version)


class AgentResponseContent(BaseModel):
    type: str
    text: str | None = None
    annotations: list[dict] = Field(default_factory=list)


class AgentResponseOutput(BaseModel):
    type: str
    role: str | None = None
    status: str | None = None
    content: list[AgentResponseContent] = Field(default_factory=list)


class AgentResponse(BaseModel):
    id: str = Field(min_length=1, max_length=500)
    status: str
    output: list[AgentResponseOutput]
    usage: dict | None = None
    content_filters: list[dict] = Field(default_factory=list)


class HostedQueryPlanner:
    def __init__(
        self,
        settings: QueryAgentSettings,
        *,
        token_provider: Callable[[], str] = azure_cli_token,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = QueryAgentSettings.model_validate(settings.model_dump())
        self._token_provider = token_provider
        self._transport = transport

    def _token(self) -> str:
        try:
            token = self._token_provider()
            if not isinstance(token, str) or not token or any(character.isspace() for character in token):
                raise ValueError
            return token
        except ProviderError:
            raise
        except Exception:
            raise ProviderError(
                "Hosted query-agent token acquisition failed",
                code=ProviderFailure.AUTH,
            ) from None

    @staticmethod
    def _metadata(payload: AgentResponse, settings: QueryAgentSettings) -> dict:
        usage = {}
        for key in ("input_tokens", "output_tokens"):
            value = (payload.usage or {}).get(key)
            usage[key] = (
                value
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                else None
            )
        return {
            "model": f"{settings.agent_name}@{settings.agent_version}",
            "response_id": payload.id,
            **usage,
        }

    @staticmethod
    def _query_plan(payload: AgentResponse, snapshot: PageSnapshot) -> QueryPlan:
        if payload.status != "completed" or any(
            item.get("blocked") for item in payload.content_filters
        ):
            raise ProviderError(
                "Hosted query agent returned an incomplete or blocked response",
                code=ProviderFailure.INCOMPLETE,
            )
        texts = []
        for output in payload.output:
            if output.type == "reasoning" or output.type in _TOOL_OUTPUT_TYPES:
                continue
            if output.type != "message" or output.role != "assistant" or output.status != "completed":
                raise ProviderError(
                    "Hosted query agent returned an unsupported output",
                    code=ProviderFailure.SCHEMA,
                )
            for content in output.content:
                if content.type != "output_text" or not content.text or content.annotations:
                    raise ProviderError(
                        "Hosted query agent returned unsupported response content",
                        code=ProviderFailure.SCHEMA,
                    )
                texts.append(content.text)
        if len(texts) != 1:
            raise ProviderError(
                "Hosted query agent must return exactly one JSON query plan",
                code=ProviderFailure.SCHEMA,
            )
        try:
            plan = QueryPlan.model_validate_json(texts[0])
        except ValidationError:
            raise ProviderError(
                "Hosted query-agent output failed QueryPlan validation",
                code=ProviderFailure.SCHEMA,
            ) from None
        if [query.priority for query in plan.queries] != list(range(1, 6)):
            raise ProviderError(
                "Hosted query agent returned an invalid query plan order",
                code=ProviderFailure.PLAN_ORDER,
            )
        passages = {
            f"page-{offset // 1000 + 1}": snapshot.content[offset:offset + 1000]
            for offset in range(0, min(len(snapshot.content), 10000), 1000)
        }
        anchored_queries = []
        for query in plan.queries:
            anchored_evidence = []
            for reference in query.evidence:
                evidence_id = reference.evidence_id
                anchored_quote = _anchor_page_quote(
                    passages.get(evidence_id, ""),
                    reference.quote,
                )
                if anchored_quote is None:
                    for candidate_id, passage in passages.items():
                        anchored_quote = _anchor_page_quote(passage, reference.quote)
                        if anchored_quote is not None:
                            evidence_id = candidate_id
                            break
                if anchored_quote is None:
                    continue
                anchored_evidence.append(reference.model_copy(update={
                    "evidence_id": evidence_id,
                    "quote": anchored_quote,
                }))
            if not anchored_evidence:
                raise ProviderError(
                    "Hosted query-agent evidence does not match the saved page snapshot",
                    code=ProviderFailure.PLAN_EVIDENCE,
                )
            anchored_queries.append(query.model_copy(update={
                "evidence": tuple(anchored_evidence),
            }))
        plan = plan.model_copy(update={"queries": tuple(anchored_queries)})
        try:
            plan.validate_evidence(snapshot)
        except ValueError:
            raise ProviderError(
                "Hosted query-agent evidence does not match the saved page snapshot",
                code=ProviderFailure.PLAN_EVIDENCE,
            ) from None
        return plan

    def propose_pairs(self, brief: Brief, snapshot: PageSnapshot) -> tuple[QueryPlan, dict]:
        if brief.url != snapshot.url or not snapshot.content.strip():
            raise ProviderError(
                "Hosted query planning requires the saved snapshot for the submitted page"
            )
        body = {
            "agent_reference": {
                "type": "agent_reference",
                "name": self.settings.agent_name,
                "version": self.settings.agent_version,
            },
            "input": json.dumps({
                "url": str(brief.url),
                "locale": brief.locale,
                "audience": brief.audience,
                "goal": brief.goal,
            }, ensure_ascii=True),
            "store": False,
            "stream": False,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        try:
            content = bytearray()
            with httpx.Client(
                transport=self._transport,
                timeout=90,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    f"{self.settings.project_endpoint}/openai/v1/responses",
                    headers={
                        "Authorization": f"Bearer {self._token()}",
                        "Accept": "application/json",
                    },
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        code = (
                            ProviderFailure.AUTH
                            if response.status_code in {401, 403}
                            else ProviderFailure.RATE_LIMIT
                            if response.status_code == 429
                            else ProviderFailure.REQUEST
                        )
                        raise ProviderError(
                            f"Hosted query-agent request failed (HTTP {response.status_code}); "
                            "no automatic retry",
                            code=code,
                        )
                    if response.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                        raise ProviderError(
                            "Hosted query agent returned an unsupported content type",
                            code=ProviderFailure.REQUEST,
                        )
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise ProviderError(
                                "Hosted query-agent response exceeded the size limit",
                                code=ProviderFailure.REQUEST,
                            )
            payload = AgentResponse.model_validate_json(content)
        except ProviderError:
            raise
        except httpx.HTTPError:
            raise ProviderError(
                "Hosted query-agent connection failed or timed out; the request may have been "
                "processed. No automatic retry.",
                code=ProviderFailure.CONNECTION,
            ) from None
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            raise ProviderError(
                "Hosted query agent returned an invalid response",
                code=ProviderFailure.SCHEMA,
            ) from None
        return self._query_plan(payload, snapshot), self._metadata(payload, self.settings)
