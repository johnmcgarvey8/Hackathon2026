from datetime import datetime
from enum import StrEnum

from pydantic import Field

from geo_agent.contracts import Contract, utc_now


class SpecialistAgentRole(StrEnum):
    GROUNDING_QUERY = "grounding-query"
    LLM_SURVEY = "llm-survey"
    RECOMMENDATIONS = "recommendations"


class SpecialistProviderMode(StrEnum):
    PROJECT_AGENT = "project-agent"
    ENVIRONMENT_AGENT = "environment-agent"
    BASELINE_PROVIDER = "baseline-provider"


class AgentStageStatus(StrEnum):
    NOT_REQUESTED = "not-requested"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentBindingSnapshot(Contract):
    project_endpoint: str = Field(min_length=1, max_length=1000)
    agent_name: str = Field(min_length=1, max_length=100)
    agent_version: str = Field(min_length=1, max_length=20)


class SpecialistAgentCall(Contract):
    role: SpecialistAgentRole
    provider_mode: SpecialistProviderMode
    provider_response_id: str | None = Field(default=None, max_length=500)
    agent_name: str | None = Field(default=None, max_length=100)
    agent_version: str | None = Field(default=None, max_length=20)
    model: str | None = Field(default=None, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class AgentStageRecord(Contract):
    role: SpecialistAgentRole
    status: AgentStageStatus
    provider_mode: SpecialistProviderMode
    job_id: str | None = Field(default=None, max_length=64)
    binding: AgentBindingSnapshot | None = None
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    provider_response_id: str | None = Field(default=None, max_length=500)
    error_code: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


def upsert_stage(
    stages: tuple[AgentStageRecord, ...],
    record: AgentStageRecord,
) -> tuple[AgentStageRecord, ...]:
    return tuple(item for item in stages if item.role != record.role) + (record,)
