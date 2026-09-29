import asyncio
import json
from collections.abc import Callable
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from geo_agent.contracts import Contract, digest, identifier, utc_now
from geo_agent.foundry import azure_cli_token
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.persistence import SQLAlchemyMeasurementRepository, project_llm_operations
from geo_agent.project_foundry import ProjectFoundrySettings
from geo_agent.projects import Project
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict, NotFound


GOAL_SUMMARY_OPERATION = "measurement-goal-summary"
MAX_RESPONSE_BYTES = 100_000
MAX_OUTPUT_TOKENS = 160


class GoalSummaryInput(Contract):
    raw_goal: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=2000)
    audience: str | None = Field(default=None, max_length=1000)
    desired_outcome: str | None = Field(default=None, max_length=1000)
    target_kind: Literal["page", "domain"] | None = None

    @property
    def input_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class GoalSummaryResult(Contract):
    operation_id: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: str = Field(min_length=1, max_length=1000)
    fallback_used: bool
    provider_response_id: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    error_code: str | None = None


class GoalSummaryRequest(GoalSummaryInput):
    idempotency_key: str = Field(min_length=1, max_length=128)


class SummaryContent(BaseModel):
    type: str
    text: str | None = None
    annotations: list[dict] = Field(default_factory=list)


class SummaryOutput(BaseModel):
    type: str
    role: str | None = None
    status: str | None = None
    content: list[SummaryContent] = Field(default_factory=list)


class SummaryResponse(BaseModel):
    id: str = Field(min_length=1, max_length=500)
    status: str
    output: list[SummaryOutput]
    usage: dict | None = None
    content_filters: list[dict] = Field(default_factory=list)


class GoalSummaryProvider(Protocol):
    async def summarize(
        self,
        project: Project,
        request: GoalSummaryInput,
    ) -> GoalSummaryResult: ...


def _fallback(request: GoalSummaryInput, error_code: str) -> GoalSummaryResult:
    return GoalSummaryResult(
        operation_id=identifier(),
        input_hash=request.input_hash,
        summary=request.raw_goal,
        fallback_used=True,
        error_code=error_code,
    )


class FallbackGoalSummaryProvider:
    async def summarize(
        self,
        project: Project,
        request: GoalSummaryInput,
    ) -> GoalSummaryResult:
        return _fallback(request, "foundry-unavailable")


class FoundryGoalSummaryProvider:
    def __init__(
        self,
        settings: ProjectFoundrySettings,
        *,
        token_provider: Callable[[], str] = azure_cli_token,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.token_provider = token_provider
        self.transport = transport

    def _binding(self, project: Project):
        return project.foundry or self.settings.default_agent

    async def summarize(
        self,
        project: Project,
        request: GoalSummaryInput,
    ) -> GoalSummaryResult:
        binding = self._binding(project)
        prompt = {
            "task": (
                "Summarise the supplied measurement goal as one concise plain-text sentence. "
                "Preserve the user's intent. Do not add facts, advice, labels, markdown, or quotes."
            ),
            "untrusted_measurement_input": request.model_dump(mode="json"),
        }
        body = {
            "agent_reference": {
                "type": "agent_reference",
                "name": binding.agent_name,
                "version": binding.agent_version,
            },
            "input": [{
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=True),
            }],
            "store": False,
            "stream": False,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "tool_choice": "none",
        }
        try:
            token = await asyncio.to_thread(self.token_provider)
            content = bytearray()
            async with asyncio.timeout(50):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=45,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        f"{binding.project_endpoint}/openai/v1/responses",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                        },
                        json=body,
                    ) as response:
                        if response.status_code != 200:
                            return _fallback(request, f"foundry-http-{response.status_code}")
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_RESPONSE_BYTES:
                                return _fallback(request, "foundry-response-too-large")
            payload = SummaryResponse.model_validate_json(content)
        except ProviderError:
            return _fallback(request, "foundry-credentials")
        except (httpx.HTTPError, TimeoutError):
            return _fallback(request, "foundry-connection")
        except ValidationError:
            return _fallback(request, "foundry-invalid-response")
        if payload.status != "completed" or any(
            item.get("blocked") for item in payload.content_filters
        ):
            return _fallback(request, "foundry-incomplete")
        text = []
        for item in payload.output:
            if item.type == "reasoning":
                continue
            if (
                item.type != "message"
                or item.role != "assistant"
                or item.status != "completed"
            ):
                return _fallback(request, "foundry-unsupported-output")
            for part in item.content:
                if part.type != "output_text" or not part.text or part.annotations:
                    return _fallback(request, "foundry-unsupported-content")
                text.append(part.text)
        summary = " ".join(" ".join(text).strip().strip("\"'").split())
        if not summary or len(summary) > 1000:
            return _fallback(request, "foundry-invalid-summary")
        usage = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = (payload.usage or {}).get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[key] = value
        return GoalSummaryResult(
            operation_id=identifier(),
            input_hash=request.input_hash,
            summary=summary,
            fallback_used=False,
            provider_response_id=payload.id,
            usage=usage,
        )


class ProjectGoalSummaryService:
    def __init__(
        self,
        repository: SQLAlchemyMeasurementRepository,
        provider: GoalSummaryProvider,
    ):
        self.repository = repository
        self.provider = provider

    @staticmethod
    def _result(row) -> GoalSummaryResult:
        if row.status != "completed" or not row.summary:
            raise Conflict("Goal summary operation is not complete")
        return GoalSummaryResult(
            operation_id=row.operation_id,
            input_hash=row.input_hash,
            summary=row.summary,
            fallback_used=bool(row.fallback_used),
            provider_response_id=row.provider_response_id,
            usage=json.loads(row.usage_payload),
            error_code=row.error_code,
        )

    async def summarize(
        self,
        project: Project,
        owner: OwnerIdentity,
        request: GoalSummaryRequest,
    ) -> GoalSummaryResult:
        summary_input = GoalSummaryInput.model_validate(
            request.model_dump(exclude={"idempotency_key"})
        )
        operation_id = identifier()
        created_at = utc_now()
        try:
            with self.repository.engine.begin() as connection:
                connection.execute(insert(project_llm_operations).values(
                    operation_id=operation_id,
                    project_id=project.project_id,
                    owner_key=owner.key,
                    operation_type=GOAL_SUMMARY_OPERATION,
                    idempotency_key=request.idempotency_key,
                    input_hash=summary_input.input_hash,
                    status="running",
                    summary=None,
                    provider_response_id=None,
                    fallback_used=0,
                    usage_payload="{}",
                    error_code=None,
                    created_at=created_at.isoformat(),
                    completed_at=None,
                ))
        except IntegrityError:
            with self.repository.engine.connect() as connection:
                row = connection.execute(select(project_llm_operations).where(
                    project_llm_operations.c.owner_key == owner.key,
                    project_llm_operations.c.operation_type == GOAL_SUMMARY_OPERATION,
                    project_llm_operations.c.idempotency_key == request.idempotency_key,
                )).first()
            if row is None:
                raise Conflict("Goal summary operation could not be recovered") from None
            if row.project_id != project.project_id or row.input_hash != summary_input.input_hash:
                raise Conflict("Goal summary idempotency key is bound to another request")
            return self._result(row)

        provider_result = await self.provider.summarize(project, summary_input)
        completed_at = utc_now()
        with self.repository.engine.begin() as connection:
            connection.execute(update(project_llm_operations).where(
                project_llm_operations.c.operation_id == operation_id,
                project_llm_operations.c.owner_key == owner.key,
                project_llm_operations.c.status == "running",
            ).values(
                status="completed",
                summary=provider_result.summary,
                provider_response_id=provider_result.provider_response_id,
                fallback_used=int(provider_result.fallback_used),
                usage_payload=json.dumps(provider_result.usage, sort_keys=True),
                error_code=provider_result.error_code,
                completed_at=completed_at.isoformat(),
            ))
        return provider_result.model_copy(update={"operation_id": operation_id})

    def get(
        self,
        project_id: str,
        owner: OwnerIdentity,
        operation_id: str,
    ) -> GoalSummaryResult:
        with self.repository.engine.connect() as connection:
            row = connection.execute(select(project_llm_operations).where(
                project_llm_operations.c.operation_id == operation_id,
                project_llm_operations.c.project_id == project_id,
                project_llm_operations.c.owner_key == owner.key,
                project_llm_operations.c.operation_type == GOAL_SUMMARY_OPERATION,
            )).first()
        if row is None:
            raise NotFound("Goal summary operation not found")
        return self._result(row)
