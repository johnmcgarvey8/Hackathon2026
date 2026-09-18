from datetime import datetime
from enum import StrEnum
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, Field

from geo_agent.contracts import Contract, digest, identifier, utc_now
from geo_agent.measurement_workflow import MeasurementRun, MeasurementRunSummary, OwnerIdentity
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict


class ExecutionControlError(Conflict):
    pass


class LeaseLost(ExecutionControlError):
    pass


class JobType(StrEnum):
    PREPARE = "prepare"
    EVALUATE = "evaluate"


class JobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationClaimState(StrEnum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowJob(Contract):
    schema_version: str = Field(
        default="geo-workflow-job/v2",
        pattern=r"^geo-workflow-job/v[12]$",
    )
    job_id: str = Field(default_factory=identifier)
    run_id: str
    owner: OwnerIdentity
    run_revision: int = Field(ge=1)
    job_type: JobType
    idempotency_key: str = Field(min_length=1, max_length=200)
    request: dict[str, Any]
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_principal_id: str | None = Field(default=None, max_length=200)
    execution_authorization_id: str | None = Field(default=None, max_length=64)
    policy_id: str | None = Field(default=None, max_length=100)
    policy_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    operation_ceiling: int | None = Field(default=None, ge=1, le=100)
    state: JobState = JobState.QUEUED
    lease_holder: str | None = Field(default=None, max_length=200)
    lease_token: str | None = Field(default=None, max_length=64)
    lease_expires_at: datetime | None = None
    error_code: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None

    @classmethod
    def create(
        cls,
        run: MeasurementRun,
        job_type: JobType,
        idempotency_key: str,
        request: object,
        *,
        execution_principal_id: str | None = None,
        execution_authorization_id: str | None = None,
        policy_id: str | None = None,
        policy_hash: str | None = None,
        operation_ceiling: int | None = None,
    ) -> "WorkflowJob":
        request_payload = request.model_dump(mode="json") if isinstance(request, BaseModel) else request
        if not isinstance(request_payload, dict):
            raise ValueError("A workflow job request must be an object")
        request_hash = digest({
            "run_id": run.run_id,
            "job_type": job_type.value,
            "request": request_payload,
        })
        return cls(
            run_id=run.run_id,
            owner=run.owner,
            run_revision=run.revision,
            job_type=job_type,
            idempotency_key=idempotency_key,
            request=request_payload,
            request_hash=request_hash,
            execution_principal_id=execution_principal_id,
            execution_authorization_id=execution_authorization_id,
            policy_id=policy_id,
            policy_hash=policy_hash,
            operation_ceiling=operation_ceiling,
        )


class OperationClaim(Contract):
    schema_version: str = Field(default="geo-operation-claim/v1", pattern=r"^geo-operation-claim/v1$")
    claim_id: str = Field(default_factory=identifier)
    job_id: str
    run_id: str
    operation_key: str = Field(min_length=1, max_length=200)
    operation_type: str = Field(pattern=r"^[a-z0-9-]+$")
    state: OperationClaimState = OperationClaimState.CLAIMED
    provider_metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)
    output: Any = None
    error_code: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OperationProgress(Contract):
    operation_type: str
    planned: int = Field(ge=0)
    completed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    in_flight: int = Field(default=0, ge=0)
    unresolved: int = Field(default=0, ge=0)
    not_attempted: int = Field(default=0, ge=0)
    optional: bool = False


class RunProgress(Contract):
    run_id: str
    run_revision: int
    run_state: str
    job_id: str | None = None
    job_type: JobType | None = None
    job_state: JobState | None = None
    error_code: str | None = None
    queued_at: datetime | None = None
    last_activity_at: datetime
    observed_at: datetime = Field(default_factory=utc_now)
    current_operation: str | None = None
    operations: tuple[OperationProgress, ...] = ()

    @classmethod
    def from_records(
        cls,
        run: MeasurementRun | MeasurementRunSummary,
        job: WorkflowJob | None,
        claims: tuple[OperationClaim, ...],
    ) -> "RunProgress":
        if job is None:
            return cls(run_id=run.run_id, run_revision=run.revision, run_state=run.state,
                       last_activity_at=run.updated_at)
        if job.job_type == JobType.PREPARE:
            planned = {"webiq-browse": 1, "page-analysis-model": 1, "paired-query-plan": 1}
        else:
            if isinstance(run, MeasurementRun):
                query_count = len(run.inputs.query_plan.queries) if run.inputs else 0
                profile_count = len(run.inputs.profiles) if run.inputs else 0
            elif job.operation_ceiling is not None:
                query_count = 5
                recommendation_count = 1 if job.request.get("include_recommendations") else 0
                profile_count = max(
                    0,
                    (job.operation_ceiling - query_count - recommendation_count)
                    // query_count,
                )
            else:
                query_count = 0
                profile_count = 0
            planned = {"webiq-search": query_count, "profile-evaluator": query_count * profile_count}
            if job.request.get("include_recommendations"):
                planned["recommendation-model"] = 1
        active = job.state in {JobState.QUEUED, JobState.LEASED}
        operations = []
        for operation_type, total in planned.items():
            matching = [claim for claim in claims if claim.operation_type == operation_type]
            claimed = sum(claim.state == OperationClaimState.CLAIMED for claim in matching)
            operations.append(OperationProgress(
                operation_type=operation_type, planned=total,
                completed=sum(claim.state == OperationClaimState.COMPLETED for claim in matching),
                failed=sum(claim.state == OperationClaimState.FAILED for claim in matching),
                in_flight=claimed if active else 0, unresolved=0 if active else claimed,
                not_attempted=max(0, total - len(matching)),
                optional=operation_type == "recommendation-model",
            ))
        in_flight = [claim for claim in claims if claim.state == OperationClaimState.CLAIMED
                     and claim.operation_type in planned]
        return cls(
            run_id=run.run_id, run_revision=run.revision, run_state=run.state,
            job_id=job.job_id, job_type=job.job_type, job_state=job.state,
            error_code=("lease-expired" if job.error_code == "lease-expired" else "job-failed") if job.error_code else None,
            queued_at=job.created_at,
            last_activity_at=max([run.updated_at, job.updated_at, *(claim.updated_at for claim in claims)]),
            current_operation=in_flight[-1].operation_type if active and in_flight else None,
            operations=tuple(operations),
        )


class JobRepository(Protocol):
    def enqueue_job(
        self,
        run_id: str,
        owner: OwnerIdentity,
        revision: int,
        job_type: JobType,
        idempotency_key: str,
        request: object,
        *,
        execution_principal_id: str | None = None,
        execution_authorization_id: str | None = None,
        policy_id: str | None = None,
        policy_hash: str | None = None,
        operation_ceiling: int | None = None,
    ) -> tuple[WorkflowJob, MeasurementRun]: ...

    def get_job(self, job_id: str, owner: OwnerIdentity) -> WorkflowJob: ...

    def list_run_jobs(
        self,
        run_id: str,
        owner: OwnerIdentity,
        limit: int = 50,
    ) -> tuple[WorkflowJob, ...]: ...

    def get_run_progress(
        self,
        run_id: str,
        owner: OwnerIdentity,
        job_id: str | None = None,
    ) -> RunProgress: ...

    def lease_one_job(
        self,
        worker_id: str,
        lease_seconds: int,
        policy_id: str | None = None,
        policy_hash: str | None = None,
        owner_key: str | None = None,
    ) -> WorkflowJob | None: ...

    def renew_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> WorkflowJob: ...

    def claim_operation(
        self,
        job_id: str,
        worker_id: str,
        operation_key: str,
        operation_type: str,
        lease_token: str | None = None,
    ) -> OperationClaim: ...

    def record_operation(
        self,
        claim_id: str,
        worker_id: str,
        state: OperationClaimState,
        provider_metadata: dict[str, str | int | bool | None] | None = None,
        error_code: str | None = None,
        output: Any = None,
        lease_token: str | None = None,
    ) -> OperationClaim: ...

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        operation: Callable[[MeasurementRun], MeasurementRun],
        lease_token: str | None = None,
    ) -> tuple[WorkflowJob, MeasurementRun]: ...

    def fail_job(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        lease_token: str | None = None,
    ) -> tuple[WorkflowJob, MeasurementRun]: ...

    def cancel_job(self, job_id: str, owner: OwnerIdentity) -> tuple[WorkflowJob, MeasurementRun]: ...

    def recover_interrupted(self, now: datetime | None = None) -> tuple[WorkflowJob, ...]: ...


class JobService:
    def __init__(self, repository: JobRepository):
        self.repository = repository

    def enqueue(
        self,
        run_id: str,
        owner: OwnerIdentity,
        revision: int,
        job_type: JobType,
        idempotency_key: str,
        request: object,
        *,
        execution_principal_id: str | None = None,
        execution_authorization_id: str | None = None,
        policy_id: str | None = None,
        policy_hash: str | None = None,
        operation_ceiling: int | None = None,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        return self.repository.enqueue_job(
            run_id,
            owner,
            revision,
            job_type,
            idempotency_key,
            request,
            execution_principal_id=execution_principal_id,
            execution_authorization_id=execution_authorization_id,
            policy_id=policy_id,
            policy_hash=policy_hash,
            operation_ceiling=operation_ceiling,
        )

    def cancel(self, job_id: str, owner: OwnerIdentity) -> tuple[WorkflowJob, MeasurementRun]:
        return self.repository.cancel_job(job_id, owner)


Result = TypeVar("Result")


class ClaimedOperationRunner:
    def __init__(self, repository: JobRepository, job: WorkflowJob, worker_id: str):
        self.repository = repository
        self.job = job
        self.worker_id = worker_id

    def call(
        self,
        operation_key: str,
        operation_type: str,
        operation: Callable[[], tuple[Result, dict[str, str | int | bool | None]]],
    ) -> Result:
        claim = self.repository.claim_operation(
            self.job.job_id,
            self.worker_id,
            operation_key,
            operation_type,
            self.job.lease_token,
        )
        try:
            result, provider_metadata = operation()
        except Exception as error:
            self.repository.record_operation(
                claim.claim_id,
                self.worker_id,
                OperationClaimState.FAILED,
                error_code=error.code.value if isinstance(error, ProviderError) else type(error).__name__,
                lease_token=self.job.lease_token,
            )
            raise
        self.repository.record_operation(
            claim.claim_id,
            self.worker_id,
            OperationClaimState.COMPLETED,
            provider_metadata=provider_metadata,
            output=result,
            lease_token=self.job.lease_token,
        )
        return result