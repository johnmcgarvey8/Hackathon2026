from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import Field

from geo_agent.contracts import Contract, identifier, utc_now
from geo_agent.measurement_workflow import OwnerIdentity


class PrincipalType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"


class AgentScope(StrEnum):
    READ = "geo.read"
    WRITE = "geo.write"
    EXECUTE = "geo.execute"
    CANCEL = "geo.cancel"
    EXPORT = "geo.export"


class AgentPrincipal(Contract):
    principal_id: str = Field(min_length=1, max_length=200)
    owner: OwnerIdentity
    principal_type: PrincipalType = PrincipalType.AGENT
    scopes: tuple[AgentScope, ...] = (
        AgentScope.READ,
        AgentScope.WRITE,
        AgentScope.EXECUTE,
        AgentScope.CANCEL,
        AgentScope.EXPORT,
    )

    def require(self, scope: AgentScope) -> None:
        if scope not in self.scopes:
            raise PermissionError(f"Agent principal requires {scope.value}")


class ExecutionStage(StrEnum):
    PREPARE = "prepare"
    EVALUATE = "evaluate"


class AgentExecutionAuthorization(Contract):
    schema_version: str = Field(
        default="geo-agent-execution-authorization/v1",
        pattern=r"^geo-agent-execution-authorization/v1$",
    )
    authorization_id: str = Field(default_factory=identifier)
    owner: OwnerIdentity
    principal_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=64)
    stage: ExecutionStage
    run_revision: int = Field(ge=1)
    input_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    policy_id: str = Field(min_length=1, max_length=100)
    policy_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_ceiling: int = Field(ge=1, le=100)
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    consumed_at: datetime | None = None
    consumed_by_job_id: str | None = Field(default=None, max_length=64)

    @classmethod
    def issue(
        cls,
        *,
        owner: OwnerIdentity,
        principal_id: str,
        run_id: str,
        stage: ExecutionStage,
        run_revision: int,
        input_hash: str | None,
        policy_id: str,
        policy_hash: str,
        operation_ceiling: int,
        lifetime_seconds: int,
    ) -> "AgentExecutionAuthorization":
        if not 60 <= lifetime_seconds <= 86400:
            raise ValueError("Agent execution authorization lifetime must be between 60 and 86400 seconds")
        return cls(
            owner=owner,
            principal_id=principal_id,
            run_id=run_id,
            stage=stage,
            run_revision=run_revision,
            input_hash=input_hash,
            policy_id=policy_id,
            policy_hash=policy_hash,
            operation_ceiling=operation_ceiling,
            expires_at=utc_now() + timedelta(seconds=lifetime_seconds),
        )


class AgentAuthorizationStatus(Contract):
    authorization_id: str
    stage: ExecutionStage
    run_revision: int
    input_hash: str | None = None
    expires_at: datetime
    consumed: bool

    @classmethod
    def from_record(cls, record: AgentExecutionAuthorization) -> "AgentAuthorizationStatus":
        return cls(
            authorization_id=record.authorization_id,
            stage=record.stage,
            run_revision=record.run_revision,
            input_hash=record.input_hash,
            expires_at=record.expires_at,
            consumed=record.consumed_at is not None,
        )
