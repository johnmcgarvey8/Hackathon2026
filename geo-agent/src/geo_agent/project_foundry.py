import asyncio
import json
import re
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, ValidationError

from geo_agent.contracts import Contract
from geo_agent.foundry import azure_cli_token
from geo_agent.geo_context import build_geo_context_packet
from geo_agent.measurement_workflow import MeasurementState
from geo_agent.persistence import SQLAlchemyMeasurementRepository
from geo_agent.project_chat import ProjectAgentReply, ProjectChatCitation, ProjectChatRequest, ProjectConversation
from geo_agent.projects import FoundryProjectBinding, Project
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict


MAX_INPUT_BYTES = 512_000
MAX_RESPONSE_BYTES = 1_000_000
MAX_OUTPUT_TOKENS = 2000


class ProjectFoundrySettings(Contract):
    default_agent: FoundryProjectBinding

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "ProjectFoundrySettings | None":
        names = ("GEO_FOUNDRY_AGENT_ENDPOINT", "GEO_FOUNDRY_AGENT_NAME", "GEO_FOUNDRY_AGENT_VERSION")
        values = [environment.get(name, "").strip() for name in names]
        if not any(values):
            return None
        if not all(values):
            raise ValueError("Configure GEO_FOUNDRY_AGENT_ENDPOINT, GEO_FOUNDRY_AGENT_NAME and GEO_FOUNDRY_AGENT_VERSION together")
        endpoint, name, version = values
        parsed = urlsplit(endpoint)
        suffix = f"/agents/{name}/endpoint/protocols/openai/responses"
        if (
            not parsed.path.endswith(suffix)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GEO_FOUNDRY_AGENT_ENDPOINT must be the Responses endpoint for the configured agent name")
        project_endpoint = urlunsplit((
            parsed.scheme, parsed.netloc, parsed.path[:-len(suffix)], "", "",
        ))
        return cls(
            default_agent=FoundryProjectBinding(
                project_endpoint=project_endpoint,
                agent_name=name,
                agent_version=version,
            ),
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


class HostedProjectAgent:
    def __init__(
        self,
        repository: SQLAlchemyMeasurementRepository,
        settings: ProjectFoundrySettings,
        *,
        token_provider: Callable[[], str] = azure_cli_token,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.repository = repository
        self.settings = settings
        self.token_provider = token_provider
        self.transport = transport

    def binding(self, project: Project) -> FoundryProjectBinding:
        return project.foundry or self.settings.default_agent

    def status(self, project: Project) -> dict:
        binding = self.binding(project)
        knowledge_blocked = binding.knowledge_base_id is not None
        return {
            "mode": "foundry",
            "can_send": not project.archived and not knowledge_blocked,
            "organisational_context_available": False,
            "agent": {
                "name": binding.agent_name,
                "version": binding.agent_version,
                "scope": "project" if project.foundry else "shared-default",
            },
            "detail": (
                "This project is archived."
                if project.archived else
                "Project knowledge retrieval is not enabled in this runtime."
                if knowledge_blocked else
                "Foundry agent configured. Sending makes one live request, with at most "
                "2,000 output tokens and no retries. No organisational retrieval or tools "
                "are enabled. Azure service quota and access are checked when sending."
            ),
        }

    def context(self, project: Project, conversation: ProjectConversation) -> tuple[dict, tuple[ProjectChatCitation, ...]]:
        if conversation.project_id != project.project_id or conversation.owner != project.owner:
            raise Conflict("Conversation does not belong to this project")
        run = None
        brand_definition = None
        project_context = None
        if conversation.run_id is not None:
            run = self.repository.get_project_run(project.project_id, conversation.run_id, project.owner)
            binding = conversation.bound_project_context
            if binding is not None:
                project_context = {
                    "project_id": project.project_id,
                    "name": binding.name,
                    "domains": binding.domains,
                    "locale": binding.locale,
                    "goal": binding.goal,
                }
                if binding.brand_definition_version is not None:
                    brand_definition = self.repository.get_brand_definition(
                        run.run_id,
                        project.owner,
                        binding.brand_definition_version,
                    )
                    if (
                        binding.brand_definition_hash is not None
                        and brand_definition.definition_hash
                        != binding.brand_definition_hash
                    ):
                        raise Conflict(
                            "Bound brand definition no longer matches its saved hash"
                        )
            elif run.brief is not None:
                project_context = {
                    "project_id": project.project_id,
                    "name": run.brief.url.host,
                    "domains": (run.brief.url.host,),
                    "locale": run.brief.locale,
                    "goal": run.brief.goal,
                }
        packet, context_citations = build_geo_context_packet(
            project,
            run,
            brand_definition,
            project_context=project_context,
        )
        return packet, tuple(
            ProjectChatCitation.model_validate(item.model_dump(mode="json"))
            for item in context_citations
        )

    @staticmethod
    def _requests_measurement(message: str) -> bool:
        return bool(
            re.search(r"https?://", message, flags=re.IGNORECASE)
            or re.search(
                r"\b(?:evaluate|evaluation|analyse|analyze|measure|measurement|audit|"
                r"approve|approval|hash|bind|bound|run|context packet|next step)\b",
                message,
                flags=re.IGNORECASE,
            )
        )

    def _workflow_reply(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> ProjectAgentReply | None:
        if conversation.run_id is not None or not self._requests_measurement(request.message):
            return None
        runs = self.repository.list_project_runs(project.project_id, project.owner, limit=1)
        if not runs:
            answer = (
                "No project-bound measurement exists yet. Open the Project's Measurements "
                "workspace, select the exact in-scope page, and create the run. The measurement "
                "workflow retrieves the live page directly; copied HTML or screenshots are not required."
            )
        else:
            run = runs[0]
            page = str(run.brief.url) if run.brief is not None else "the selected project page"
            if run.state == MeasurementState.DRAFT:
                action = "Confirm live preparation to retrieve the page and generate the query plan."
            elif run.state == MeasurementState.AWAITING_APPROVAL:
                approval_current = (
                    run.inputs is not None
                    and run.approval is not None
                    and run.approval.input_hash == run.inputs.approval_hash
                )
                if approval_current:
                    action = (
                        "The saved queries are approved. Confirm the live evaluation calls and "
                        "select Start approved run."
                    )
                else:
                    query_count = len(run.inputs.query_plan.queries) if run.inputs is not None else 0
                    action = (
                        f"Review and approve the {query_count} saved queries, then start the live evaluation."
                    )
            elif run.state in {
                MeasurementState.PREPARING,
                MeasurementState.QUEUED,
                MeasurementState.EVALUATING,
                MeasurementState.RECOMMENDING,
            }:
                action = "Open the run to monitor its saved live progress."
            elif run.state in {
                MeasurementState.READY,
                MeasurementState.PARTIAL,
                MeasurementState.EXPORTED,
            }:
                action = (
                    "Open the completed run and choose Discuss this run to create an immutable "
                    "evidence-bound conversation."
                )
            else:
                action = "Open the run to review its saved status and available recovery actions."
            answer = (
                f"A project-bound measurement already exists for {page}. "
                f"Run {run.run_id} is {run.state.value}. {action} "
                "Copied HTML or screenshots are not required."
            )
        return ProjectAgentReply(
            answer=answer,
            mode="workflow",
            agent_name="GEO workflow router",
        )

    async def respond(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> ProjectAgentReply:
        status = self.status(project)
        if not status["can_send"]:
            raise Conflict(status["detail"])
        if request.use_organisational_context:
            raise Conflict("Organisational context is not enabled; disable it before sending")
        workflow_reply = self._workflow_reply(project, conversation, request)
        if workflow_reply is not None:
            return workflow_reply
        binding = self.binding(project)
        packet, citations = self.context(project, conversation)
        messages = [{
            "role": "user",
            "content": json.dumps({"GEO_CONTEXT_PACKET": packet}, ensure_ascii=True),
        }]
        # Rehydrate only this local conversation. Never reuse a cloud thread or response ID.
        for turn in conversation.turns[:-1]:
            if turn.status == "completed" and turn.mode == "foundry":
                messages.extend([
                    {"role": "user", "content": turn.message},
                    {"role": "assistant", "content": turn.answer},
                ])
        messages.append({"role": "user", "content": request.message})
        if len(json.dumps(messages).encode("utf-8")) > MAX_INPUT_BYTES:
            raise Conflict("Conversation context limit reached; start a new conversation")
        body = {
            "agent_reference": {
                "type": "agent_reference",
                "name": binding.agent_name,
                "version": binding.agent_version,
            },
            "input": messages,
            "store": False,
            "stream": False,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "tool_choice": "none",
        }
        # The project Responses route supports an explicit immutable agent version.
        # The supplied stable agent endpoint can otherwise follow its latest version.
        url = f"{binding.project_endpoint}/openai/v1/responses"
        try:
            token = await asyncio.to_thread(self.token_provider)
        except ProviderError as error:
            raise Conflict(str(error)) from None
        try:
            async with asyncio.timeout(95):
                async with httpx.AsyncClient(
                    transport=self.transport, timeout=90,
                    follow_redirects=False, trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST", url,
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                        json=body,
                    ) as response:
                        if response.status_code != 200:
                            message = {
                                401: "Azure sign-in is missing or expired. Sign in with Azure CLI.",
                                403: "Your Azure identity does not have access to this Foundry agent.",
                                404: "The configured Foundry project, agent version or Responses route was not found.",
                                429: "Foundry rate limit or quota reached.",
                            }.get(response.status_code, f"Foundry request failed (HTTP {response.status_code}).")
                            raise Conflict(f"{message} No automatic retry.")
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_RESPONSE_BYTES:
                                raise Conflict("Foundry response exceeded the size limit; no automatic retry")
            payload = AgentResponse.model_validate_json(content)
        except (httpx.HTTPError, TimeoutError):
            raise Conflict("Foundry connection failed or timed out; the request may have been processed. No automatic retry.") from None
        except ValidationError:
            raise Conflict("Foundry returned an invalid response; no automatic retry") from None
        if payload.status != "completed" or any(item.get("blocked") for item in payload.content_filters):
            raise Conflict("Foundry returned an incomplete or blocked response; no automatic retry")
        text = []
        for item in payload.output:
            if item.type == "reasoning":
                continue
            if item.type != "message" or item.role != "assistant" or item.status != "completed":
                raise Conflict("Foundry returned an unexpected tool or output; tools are disabled")
            for part in item.content:
                if part.type != "output_text" or not part.text or part.annotations:
                    raise Conflict("Foundry returned unsupported content or external citations; retrieval is disabled")
                text.append(part.text)
        answer = "\n".join(text).strip()
        if not answer or len(answer) > 10000:
            raise Conflict("Foundry returned an empty or oversized answer")
        cited = {match.group(1) for match in re.finditer(r"\[([A-Za-z0-9-]+)\]", answer)}
        usage = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = (payload.usage or {}).get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[key] = value
        return ProjectAgentReply(
            answer=answer,
            citations=tuple(citation for citation in citations if citation.source_id in cited),
            provider_response_id=payload.id,
            mode="foundry",
            agent_name=binding.agent_name,
            agent_version=binding.agent_version,
            context_hash=packet["context_hash"],
            usage=usage,
        )
