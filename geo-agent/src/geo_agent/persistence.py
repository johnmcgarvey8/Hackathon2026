from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from geo_agent.artifact_storage import MeasurementArtifact
from geo_agent.contracts import Brief, MeasurementInputs, utc_now
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.evidence_assessment import BrandDefinition, BrandDefinitionRecord
from geo_agent.jobs import JobState, JobType, OperationClaim, OperationClaimState, RunProgress, WorkflowJob
from geo_agent.measurement_budget import MeasurementBudgetGrant
from geo_agent.measurement_workflow import (
    MeasurementApproval,
    MeasurementEvent,
    MeasurementRepository,
    MeasurementRun,
    MeasurementState,
    Mutation,
    OwnerIdentity,
)
from geo_agent.workflow import Conflict, NotFound


metadata = MetaData()

measurement_runs = Table(
    "measurement_runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("owner_key", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    Index("ix_measurement_runs_owner_updated", "owner_key", "run_id"),
)

run_brand_definitions = Table(
    "run_brand_definitions", metadata,
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), primary_key=True),
    Column("definition_version", Integer, primary_key=True),
    Column("owner_key", String(64), nullable=False),
    Column("definition_hash", String(64), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

run_events = Table(
    "run_events",
    metadata,
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("payload", Text, nullable=False),
)

approvals = Table(
    "approvals",
    metadata,
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("input_hash", String(64), nullable=False),
    Column("actor_tenant_id", String(200), nullable=False),
    Column("actor_object_id", String(200), nullable=False),
    Column("approved_at", String(40), nullable=False),
)

workflow_jobs = Table(
    "workflow_jobs",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("job_type", String(40), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("state", String(40), nullable=False),
    Column("lease_holder", String(200)),
    Column("lease_expires_at", String(40)),
    Column("completed_at", String(40)),
    Column("error_code", String(100)),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("owner_key", "idempotency_key", name="uq_workflow_jobs_owner_idempotency"),
    Index("ix_workflow_jobs_state_created", "state", "created_at"),
)

operation_claims = Table(
    "operation_claims",
    metadata,
    Column("claim_id", String(64), primary_key=True),
    Column("job_id", String(64), ForeignKey("workflow_jobs.job_id"), nullable=False),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), nullable=False),
    Column("operation_key", String(200), nullable=False, unique=True),
    Column("state", String(40), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)

measurement_budget_grants = Table(
    "measurement_budget_grants",
    metadata,
    Column("grant_id", String(100), primary_key=True),
    Column("policy_id", String(100), nullable=False),
    Column("policy_hash", String(64), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("grant_hash", String(64), nullable=False),
    Column("payload", Text, nullable=False),
    Column("approved_at", String(40), nullable=False),
    Index("ix_measurement_budget_grants_policy", "policy_id", "policy_hash"),
)

measurement_budget_usage = Table(
    "measurement_budget_usage",
    metadata,
    Column("grant_id", String(100), ForeignKey("measurement_budget_grants.grant_id"), primary_key=True),
    Column("operation_type", String(40), primary_key=True),
    Column("allowance", Integer, nullable=False),
    Column("consumed", Integer, nullable=False),
)

measurement_budget_consumptions = Table(
    "measurement_budget_consumptions",
    metadata,
    Column("claim_id", String(64), ForeignKey("operation_claims.claim_id"), primary_key=True),
    Column("grant_id", String(100), ForeignKey("measurement_budget_grants.grant_id"), nullable=False),
    Column("operation_type", String(40), nullable=False),
    Column("created_at", String(40), nullable=False),
    Index("ix_measurement_budget_consumptions_grant", "grant_id", "operation_type"),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("artifact_id", String(64), primary_key=True),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("media_type", String(120), nullable=False),
    Column("size", Integer, nullable=False),
    Column("storage_key", String(500), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("run_id", "content_hash", name="uq_artifacts_run_hash"),
)

agent_conversations = Table(
    "agent_conversations",
    metadata,
    Column("conversation_id", String(64), primary_key=True),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)

agent_capabilities = Table(
    "agent_capabilities",
    metadata,
    Column("capability_hash", String(64), primary_key=True),
    Column("conversation_id", String(64), ForeignKey("agent_conversations.conversation_id"), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("created_at", String(40), nullable=False),
    Index("ix_agent_capabilities_expiry", "expires_at"),
)


class SQLAlchemyMeasurementRepository(MeasurementRepository):
    def __init__(self, database_url: str, *, initialize_schema: bool = False):
        self.engine = create_engine(database_url)
        self._measurement_budget_binding: tuple[str, str, str] | None = None
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        if initialize_schema:
            metadata.create_all(self.engine)

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @staticmethod
    def _event(event_type: str, sequence: int = 1) -> MeasurementEvent:
        return MeasurementEvent(sequence=sequence, event_type=event_type)

    def create(
        self,
        owner: OwnerIdentity,
        inputs: MeasurementInputs | None = None,
        brief: Brief | None = None,
        brand_definition: BrandDefinition | None = None,
    ) -> MeasurementRun:
        created_event = self._event("awaiting-query-approval" if inputs else "draft")
        run = MeasurementRun(
            owner=owner,
            brief=inputs.brief if inputs is not None else brief,
            inputs=inputs,
            state=MeasurementState.AWAITING_APPROVAL if inputs else MeasurementState.DRAFT,
            events=(created_event,),
        )
        with self.engine.begin() as connection:
            connection.execute(insert(measurement_runs).values(
                run_id=run.run_id,
                owner_key=owner.key,
                revision=run.revision,
                payload=run.model_dump_json(),
            ))
            self._insert_events(connection, run.run_id, (created_event,))
            if brand_definition is not None:
                self._insert_brand_definition(connection, owner, BrandDefinitionRecord(
                    run_id=run.run_id, definition_version=1, definition=brand_definition))
        return run

    @staticmethod
    def _insert_brand_definition(connection: Connection, owner: OwnerIdentity,
                                 record: BrandDefinitionRecord) -> None:
        connection.execute(insert(run_brand_definitions).values(
            run_id=record.run_id, definition_version=record.definition_version, owner_key=owner.key,
            definition_hash=record.definition_hash, payload=record.model_dump_json(),
            created_at=record.created_at.isoformat()))

    @staticmethod
    def _read_brand_definition(connection: Connection, run_id: str, owner: OwnerIdentity,
                              version: int | None = None) -> BrandDefinitionRecord | None:
        statement = select(run_brand_definitions.c.payload).where(
            run_brand_definitions.c.run_id == run_id, run_brand_definitions.c.owner_key == owner.key)
        if version is not None:
            statement = statement.where(run_brand_definitions.c.definition_version == version)
        payload = connection.execute(statement.order_by(
            run_brand_definitions.c.definition_version.desc()).limit(1)).scalar_one_or_none()
        if payload is None and version is not None:
            raise NotFound("Brand definition version not found")
        return BrandDefinitionRecord.model_validate_json(payload) if payload is not None else None

    def get_brand_definition(self, run_id: str, owner: OwnerIdentity,
                             version: int | None = None) -> BrandDefinitionRecord | None:
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            return self._read_brand_definition(connection, run_id, owner, version)

    def save_brand_definition(self, run_id: str, owner: OwnerIdentity, definition: BrandDefinition,
                              expected_version: int) -> BrandDefinitionRecord:
        if expected_version < 0:
            raise Conflict("Invalid brand definition version")
        try:
            with self.engine.begin() as connection:
                self._read(connection, run_id, owner)
                connection.execute(select(measurement_runs.c.run_id).where(
                    measurement_runs.c.run_id == run_id).with_for_update())
                current = self._read_brand_definition(connection, run_id, owner)
                if current and current.definition_hash == definition.definition_hash:
                    return current
                if expected_version != (current.definition_version if current else 0):
                    raise Conflict("Stale brand definition version; reload the assessment")
                record = BrandDefinitionRecord(run_id=run_id, definition_version=expected_version + 1,
                                               definition=definition)
                self._insert_brand_definition(connection, owner, record)
                return record
        except IntegrityError:
            current = self.get_brand_definition(run_id, owner)
            if current and current.definition_hash == definition.definition_hash:
                return current
            raise Conflict("Brand definition changed concurrently; reload the assessment") from None

    @staticmethod
    def _read(connection: Connection, run_id: str, owner: OwnerIdentity) -> MeasurementRun:
        payload = connection.execute(
            select(measurement_runs.c.payload).where(
                measurement_runs.c.run_id == run_id,
                measurement_runs.c.owner_key == owner.key,
            )
        ).scalar_one_or_none()
        if payload is None:
            raise NotFound("Measurement run not found")
        return MeasurementRun.model_validate_json(payload)

    @staticmethod
    def _insert_events(connection: Connection, run_id: str, events: tuple[MeasurementEvent, ...]) -> None:
        if events:
            connection.execute(insert(run_events), [
                {"run_id": run_id, "sequence": item.sequence, "payload": item.model_dump_json()}
                for item in events
            ])

    @staticmethod
    def _insert_approval(connection: Connection, run_id: str, approval: MeasurementApproval) -> None:
        connection.execute(insert(approvals).values(
            run_id=run_id,
            revision=approval.revision,
            input_hash=approval.input_hash,
            actor_tenant_id=approval.actor.tenant_id,
            actor_object_id=approval.actor.object_id,
            approved_at=approval.approved_at.isoformat(),
        ))

    def get(self, run_id: str, owner: OwnerIdentity) -> MeasurementRun:
        with self.engine.connect() as connection:
            return self._read(connection, run_id, owner)

    def list_runs(self, owner: OwnerIdentity, limit: int = 50) -> tuple[MeasurementRun, ...]:
        if not 1 <= limit <= 100:
            raise Conflict("Run list limit must be between 1 and 100")
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(measurement_runs.c.payload).where(measurement_runs.c.owner_key == owner.key)
            ).scalars()
            runs = sorted(
                (MeasurementRun.model_validate_json(payload) for payload in payloads),
                key=lambda run: (run.updated_at, run.run_id),
                reverse=True,
            )
            return tuple(runs[:limit])

    @staticmethod
    def _artifact_from_row(row: Any) -> MeasurementArtifact:
        return MeasurementArtifact(
            artifact_id=row.artifact_id,
            run_id=row.run_id,
            content_hash=row.content_hash,
            media_type=row.media_type,
            size=row.size,
            storage_key=row.storage_key,
            created_at=row.created_at,
        )

    @staticmethod
    def _read_run_artifact(
        connection: Connection,
        run_id: str,
        owner: OwnerIdentity,
    ) -> MeasurementArtifact | None:
        row = connection.execute(
            select(artifacts).where(
                artifacts.c.run_id == run_id,
                artifacts.c.owner_key == owner.key,
            ).order_by(artifacts.c.created_at, artifacts.c.artifact_id)
        ).first()
        return SQLAlchemyMeasurementRepository._artifact_from_row(row) if row is not None else None

    def persist_export(
        self,
        artifact: MeasurementArtifact,
        owner: OwnerIdentity,
        revision: int,
    ) -> tuple[MeasurementArtifact, MeasurementRun]:
        try:
            with self.engine.begin() as connection:
                current = self._read(connection, artifact.run_id, owner)
                existing = self._read_run_artifact(connection, artifact.run_id, owner)
                if current.state == MeasurementState.EXPORTED:
                    if existing is None:
                        raise Conflict("Exported measurement is missing artifact metadata")
                    return existing, current
                if current.revision != revision:
                    raise Conflict("Stale measurement revision; reload the run")
                if (
                    current.state not in {MeasurementState.READY, MeasurementState.PARTIAL, MeasurementState.FAILED}
                    or current.measurement is None
                ):
                    raise Conflict("Only a completed measurement can be exported")
                if artifact.run_id != current.run_id:
                    raise Conflict("Artifact does not belong to the measurement run")
                connection.execute(insert(artifacts).values(
                    artifact_id=artifact.artifact_id,
                    run_id=artifact.run_id,
                    owner_key=owner.key,
                    content_hash=artifact.content_hash,
                    media_type=artifact.media_type,
                    size=artifact.size,
                    storage_key=artifact.storage_key,
                    created_at=artifact.created_at.isoformat(),
                ))
                updated = self._mutate_run(
                    connection,
                    current,
                    revision,
                    lambda run: run.model_copy(update={
                        "state": MeasurementState.EXPORTED,
                        "events": (*run.events, MeasurementEvent(
                            sequence=len(run.events) + 1,
                            event_type="exported",
                        )),
                    }),
                )
                return artifact, updated
        except IntegrityError:
            with self.engine.connect() as connection:
                current = self._read(connection, artifact.run_id, owner)
                existing = self._read_run_artifact(connection, artifact.run_id, owner)
                if (
                    current.state == MeasurementState.EXPORTED
                    and existing is not None
                    and existing.content_hash == artifact.content_hash
                ):
                    return existing, current
            raise

    def get_artifact(
        self,
        run_id: str,
        artifact_id: str,
        owner: OwnerIdentity,
    ) -> MeasurementArtifact:
        with self.engine.connect() as connection:
            row = connection.execute(select(artifacts).where(
                artifacts.c.artifact_id == artifact_id,
                artifacts.c.run_id == run_id,
                artifacts.c.owner_key == owner.key,
            )).first()
            if row is None:
                raise NotFound("Measurement artifact not found")
            return self._artifact_from_row(row)

    def get_run_artifact(self, run_id: str, owner: OwnerIdentity) -> MeasurementArtifact:
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            artifact = self._read_run_artifact(connection, run_id, owner)
            if artifact is None:
                raise NotFound("Measurement artifact not found")
            return artifact

    def _mutate_run(
        self,
        connection: Connection,
        current: MeasurementRun,
        revision: int,
        operation: Mutation,
    ) -> MeasurementRun:
        if current.revision != revision:
            raise Conflict("Stale measurement revision; reload the run")
        candidate = operation(current)
        if candidate.run_id != current.run_id or candidate.owner != current.owner:
            raise Conflict("A measurement mutation cannot change identity")
        if candidate.revision != current.revision:
            raise Conflict("The repository owns measurement revisions")
        if candidate.events[:len(current.events)] != current.events:
            raise Conflict("Measurement events are append-only")
        updated = MeasurementRun.model_validate({
            **candidate.model_dump(),
            "revision": current.revision + 1,
            "updated_at": utc_now(),
        })
        changed = connection.execute(
            update(measurement_runs)
            .where(
                measurement_runs.c.run_id == current.run_id,
                measurement_runs.c.owner_key == current.owner.key,
                measurement_runs.c.revision == revision,
            )
            .values(revision=updated.revision, payload=updated.model_dump_json())
        )
        if changed.rowcount != 1:
            raise Conflict("Stale measurement revision; reload the run")
        self._insert_events(connection, current.run_id, updated.events[len(current.events):])
        if updated.approval is not None and updated.approval != current.approval:
            self._insert_approval(connection, current.run_id, updated.approval)
        return updated

    def mutate(self, run_id: str, owner: OwnerIdentity, revision: int, operation: Mutation) -> MeasurementRun:
        with self.engine.begin() as connection:
            return self._mutate_run(connection, self._read(connection, run_id, owner), revision, operation)

    @staticmethod
    def _job_from_row(row: Any) -> WorkflowJob:
        return WorkflowJob.model_validate_json(row.payload)

    @staticmethod
    def _write_job(connection: Connection, job: WorkflowJob) -> None:
        connection.execute(
            update(workflow_jobs)
            .where(workflow_jobs.c.job_id == job.job_id)
            .values(
                state=job.state.value,
                lease_holder=job.lease_holder,
                lease_expires_at=job.lease_expires_at.isoformat() if job.lease_expires_at else None,
                completed_at=job.completed_at.isoformat() if job.completed_at else None,
                error_code=job.error_code,
                payload=job.model_dump_json(),
                updated_at=job.updated_at.isoformat(),
            )
        )

    def enqueue_job(
        self,
        run_id: str,
        owner: OwnerIdentity,
        revision: int,
        job_type: JobType,
        idempotency_key: str,
        request: object,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        if not idempotency_key.strip():
            raise Conflict("A bounded idempotency key is required")
        with self.engine.begin() as connection:
            existing_row = connection.execute(
                select(workflow_jobs).where(
                    workflow_jobs.c.owner_key == owner.key,
                    workflow_jobs.c.idempotency_key == idempotency_key,
                )
            ).first()
            current = self._read(connection, run_id, owner)
            expected = WorkflowJob.create(current, job_type, idempotency_key, request)
            if existing_row is not None:
                existing = self._job_from_row(existing_row)
                if (
                    existing.run_id != run_id
                    or existing.job_type != job_type
                    or existing.request_hash != expected.request_hash
                ):
                    raise Conflict("Idempotency key is bound to a different job request")
                return existing, current
            if current.revision != revision:
                raise Conflict("Stale measurement revision; reload the run")
            if job_type == JobType.PREPARE:
                if current.state != MeasurementState.DRAFT:
                    raise Conflict("Preparation can only be queued for a draft run")
                next_state = MeasurementState.PREPARING
                event_type = "preparation-queued"
            else:
                if current.state != MeasurementState.AWAITING_APPROVAL or current.approval is None:
                    raise Conflict("Evaluation requires current human query approval")
                next_state = MeasurementState.QUEUED
                event_type = "evaluation-queued"
            updated_run = self._mutate_run(
                connection,
                current,
                revision,
                lambda run: run.model_copy(update={
                    "state": next_state,
                    "events": (*run.events, MeasurementEvent(
                        sequence=len(run.events) + 1,
                        event_type=event_type,
                    )),
                }),
            )
            job = WorkflowJob.create(updated_run, job_type, idempotency_key, request)
            connection.execute(insert(workflow_jobs).values(
                job_id=job.job_id,
                run_id=job.run_id,
                owner_key=owner.key,
                job_type=job.job_type.value,
                idempotency_key=job.idempotency_key,
                state=job.state.value,
                lease_holder=None,
                lease_expires_at=None,
                completed_at=None,
                error_code=None,
                payload=job.model_dump_json(),
                created_at=job.created_at.isoformat(),
                updated_at=job.updated_at.isoformat(),
            ))
            return job, updated_run

    def get_job(self, job_id: str, owner: OwnerIdentity) -> WorkflowJob:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(workflow_jobs).where(
                    workflow_jobs.c.job_id == job_id,
                    workflow_jobs.c.owner_key == owner.key,
                )
            ).first()
            if row is None:
                raise NotFound("Workflow job not found")
            return self._job_from_row(row)

    def list_run_jobs(self, run_id: str, owner: OwnerIdentity) -> tuple[WorkflowJob, ...]:
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            rows = connection.execute(
                select(workflow_jobs).where(
                    workflow_jobs.c.run_id == run_id,
                    workflow_jobs.c.owner_key == owner.key,
                ).order_by(workflow_jobs.c.created_at.desc(), workflow_jobs.c.job_id.desc())
            ).all()
            return tuple(self._job_from_row(row) for row in rows)

    def get_run_progress(self, run_id: str, owner: OwnerIdentity) -> RunProgress:
        with self.engine.begin() as connection:
            run = self._read(connection, run_id, owner)
            row = connection.execute(
                select(workflow_jobs).where(
                    workflow_jobs.c.run_id == run_id,
                    workflow_jobs.c.owner_key == owner.key,
                ).order_by(workflow_jobs.c.created_at.desc(), workflow_jobs.c.job_id.desc()).limit(1)
            ).first()
            job = self._job_from_row(row) if row else None
            claims = ()
            if job is not None:
                rows = connection.execute(select(operation_claims).where(
                    operation_claims.c.run_id == run_id,
                    operation_claims.c.job_id == job.job_id,
                ).order_by(operation_claims.c.created_at, operation_claims.c.claim_id)).all()
                claims = tuple(self._claim_from_row(item) for item in rows)
            return RunProgress.from_records(run, job, claims)

    @staticmethod
    def _require_active_lease(job: WorkflowJob, worker_id: str, now: datetime) -> None:
        if (
            job.state != JobState.LEASED
            or job.lease_holder != worker_id
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise Conflict("Worker does not hold an active lease for this job")

    def lease_one_job(
        self,
        worker_id: str,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> WorkflowJob | None:
        if not worker_id.strip() or not 1 <= lease_seconds <= 3600:
            raise Conflict("A worker ID and bounded lease duration are required")
        current_time = now or utc_now()
        with self.engine.begin() as connection:
            row = connection.execute(
                select(workflow_jobs)
                .where(workflow_jobs.c.state == JobState.QUEUED.value)
                .order_by(workflow_jobs.c.created_at, workflow_jobs.c.job_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if row is None:
                return None
            job = self._job_from_row(row)
            run = self._read(connection, job.run_id, job.owner)
            if run.revision != job.run_revision:
                raise Conflict("Queued job is bound to a stale measurement revision")
            if job.job_type == JobType.EVALUATE:
                run = self._mutate_run(
                    connection,
                    run,
                    run.revision,
                    lambda item: item.model_copy(update={
                        "state": MeasurementState.EVALUATING,
                        "events": (*item.events, MeasurementEvent(
                            sequence=len(item.events) + 1,
                            event_type="evaluating",
                        )),
                    }),
                )
            leased = job.model_copy(update={
                "run_revision": run.revision,
                "state": JobState.LEASED,
                "lease_holder": worker_id,
                "lease_expires_at": current_time + timedelta(seconds=lease_seconds),
                "updated_at": current_time,
            })
            changed = connection.execute(
                update(workflow_jobs)
                .where(
                    workflow_jobs.c.job_id == job.job_id,
                    workflow_jobs.c.state == JobState.QUEUED.value,
                )
                .values(state=leased.state.value)
            )
            if changed.rowcount != 1:
                return None
            self._write_job(connection, leased)
            return leased

    def claim_operation(
        self,
        job_id: str,
        worker_id: str,
        operation_key: str,
        operation_type: str,
        now: datetime | None = None,
    ) -> OperationClaim:
        current_time = now or utc_now()
        with self.engine.begin() as connection:
            job_row = connection.execute(select(workflow_jobs).where(workflow_jobs.c.job_id == job_id)).first()
            if job_row is None:
                raise NotFound("Workflow job not found")
            job = self._job_from_row(job_row)
            self._require_active_lease(job, worker_id, current_time)
            existing_claim = connection.execute(
                select(operation_claims.c.claim_id).where(
                    operation_claims.c.operation_key == operation_key
                )
            ).first()
            if existing_claim is not None:
                raise Conflict("Operation was already claimed and will not be retried")
            budget_grant_id = self._consume_measurement_budget(
                connection,
                job,
                operation_type,
            )
            claim = OperationClaim(
                job_id=job.job_id,
                run_id=job.run_id,
                operation_key=operation_key,
                operation_type=operation_type,
                created_at=current_time,
                updated_at=current_time,
            )
            try:
                connection.execute(insert(operation_claims).values(
                    claim_id=claim.claim_id,
                    job_id=claim.job_id,
                    run_id=claim.run_id,
                    operation_key=claim.operation_key,
                    state=claim.state.value,
                    payload=claim.model_dump_json(),
                    created_at=claim.created_at.isoformat(),
                    updated_at=claim.updated_at.isoformat(),
                ))
                if budget_grant_id is not None:
                    connection.execute(insert(measurement_budget_consumptions).values(
                        claim_id=claim.claim_id,
                        grant_id=budget_grant_id,
                        operation_type=operation_type,
                        created_at=current_time.isoformat(),
                    ))
            except IntegrityError as error:
                raise Conflict("Operation was already claimed and will not be retried") from error
            return claim

    def bind_measurement_budget(
        self,
        grant: MeasurementBudgetGrant,
        policy: MeasurementExecutionPolicy,
    ) -> None:
        grant.validate_for(policy)
        binding = (grant.grant_id, grant.policy_id, grant.policy_hash)
        if self._measurement_budget_binding not in {None, binding}:
            raise Conflict("Repository is already bound to a different measurement budget")
        with self.engine.begin() as connection:
            mutated_policy = connection.execute(
                select(measurement_budget_grants.c.grant_id).where(
                    measurement_budget_grants.c.policy_id == grant.policy_id,
                    measurement_budget_grants.c.policy_hash != grant.policy_hash,
                )
            ).first()
            if mutated_policy is not None:
                raise Conflict("Execution policy changed under an existing measurement budget")
            existing = connection.execute(
                select(measurement_budget_grants).where(
                    measurement_budget_grants.c.grant_id == grant.grant_id
                )
            ).first()
            if existing is not None:
                if existing.grant_hash != grant.grant_hash:
                    raise Conflict("Measurement budget grant already exists with different authorisation")
            else:
                connection.execute(insert(measurement_budget_grants).values(
                    grant_id=grant.grant_id,
                    policy_id=grant.policy_id,
                    policy_hash=grant.policy_hash,
                    owner_key=grant.owner.key,
                    grant_hash=grant.grant_hash,
                    payload=grant.model_dump_json(),
                    approved_at=grant.approved_at.isoformat(),
                ))
                connection.execute(insert(measurement_budget_usage), [
                    {
                        "grant_id": grant.grant_id,
                        "operation_type": operation_type,
                        "allowance": allowance,
                        "consumed": 0,
                    }
                    for operation_type, allowance in grant.allowances.items()
                ])
        self._measurement_budget_binding = binding

    def _consume_measurement_budget(
        self,
        connection: Connection,
        job: WorkflowJob,
        operation_type: str,
    ) -> str | None:
        if self._measurement_budget_binding is None:
            return None
        grant_id, policy_id, policy_hash = self._measurement_budget_binding
        grant_row = connection.execute(
            select(measurement_budget_grants).where(
                measurement_budget_grants.c.grant_id == grant_id,
                measurement_budget_grants.c.policy_id == policy_id,
                measurement_budget_grants.c.policy_hash == policy_hash,
                measurement_budget_grants.c.owner_key == job.owner.key,
            )
        ).first()
        if grant_row is None:
            raise Conflict("Measurement budget does not authorise this job")
        consumed = connection.execute(
            update(measurement_budget_usage)
            .where(
                measurement_budget_usage.c.grant_id == grant_id,
                measurement_budget_usage.c.operation_type == operation_type,
                measurement_budget_usage.c.consumed < measurement_budget_usage.c.allowance,
            )
            .values(consumed=measurement_budget_usage.c.consumed + 1)
        )
        if consumed.rowcount != 1:
            raise Conflict("Measurement operation allowance is exhausted or not authorised")
        return grant_id

    @staticmethod
    def _claim_from_row(row: Any) -> OperationClaim:
        return OperationClaim.model_validate_json(row.payload)

    def record_operation(
        self,
        claim_id: str,
        worker_id: str,
        state: OperationClaimState,
        provider_metadata: dict[str, str | int | bool | None] | None = None,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> OperationClaim:
        if state not in {OperationClaimState.COMPLETED, OperationClaimState.FAILED}:
            raise Conflict("An operation outcome must be completed or failed")
        current_time = now or utc_now()
        with self.engine.begin() as connection:
            claim_row = connection.execute(
                select(operation_claims).where(operation_claims.c.claim_id == claim_id)
            ).first()
            if claim_row is None:
                raise NotFound("Operation claim not found")
            claim = self._claim_from_row(claim_row)
            job_row = connection.execute(
                select(workflow_jobs).where(workflow_jobs.c.job_id == claim.job_id)
            ).first()
            if job_row is None:
                raise NotFound("Workflow job not found")
            self._require_active_lease(self._job_from_row(job_row), worker_id, current_time)
            if claim.state != OperationClaimState.CLAIMED:
                raise Conflict("Operation claim already has an outcome")
            updated = claim.model_copy(update={
                "state": state,
                "provider_metadata": provider_metadata or {},
                "error_code": error_code,
                "updated_at": current_time,
            })
            connection.execute(
                update(operation_claims)
                .where(
                    operation_claims.c.claim_id == claim_id,
                    operation_claims.c.state == OperationClaimState.CLAIMED.value,
                )
                .values(
                    state=updated.state.value,
                    payload=updated.model_dump_json(),
                    updated_at=updated.updated_at.isoformat(),
                )
            )
            return updated

    def _leased_job(self, connection: Connection, job_id: str, worker_id: str) -> WorkflowJob:
        row = connection.execute(select(workflow_jobs).where(workflow_jobs.c.job_id == job_id)).first()
        if row is None:
            raise NotFound("Workflow job not found")
        job = self._job_from_row(row)
        self._require_active_lease(job, worker_id, utc_now())
        return job

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        operation: Mutation,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        with self.engine.begin() as connection:
            job = self._leased_job(connection, job_id, worker_id)
            run = self._read(connection, job.run_id, job.owner)
            updated_run = self._mutate_run(connection, run, job.run_revision, operation)
            allowed_states = {
                JobType.PREPARE: {MeasurementState.AWAITING_APPROVAL},
                JobType.EVALUATE: {
                    MeasurementState.READY,
                    MeasurementState.PARTIAL,
                    MeasurementState.FAILED,
                },
            }
            if updated_run.state not in allowed_states[job.job_type]:
                raise Conflict("Job completion produced an invalid measurement state")
            if job.job_type == JobType.PREPARE:
                from geo_agent.foundry import PreparationAnalysis

                analysis = updated_run.page_analysis
                if (isinstance(analysis, PreparationAnalysis) and analysis.brand is not None
                        and self._read_brand_definition(connection, run.run_id, job.owner) is None):
                    self._insert_brand_definition(connection, job.owner, BrandDefinitionRecord(
                        run_id=run.run_id, definition_version=1,
                        definition=analysis.brand.definition, source="page-analysis"))
            now = utc_now()
            completed = job.model_copy(update={
                "run_revision": updated_run.revision,
                "state": JobState.COMPLETED,
                "lease_holder": None,
                "lease_expires_at": None,
                "completed_at": now,
                "updated_at": now,
            })
            self._write_job(connection, completed)
            return completed, updated_run

    def fail_job(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        with self.engine.begin() as connection:
            job = self._leased_job(connection, job_id, worker_id)
            run = self._read(connection, job.run_id, job.owner)
            now = utc_now()
            failed = job.model_copy(update={
                "state": JobState.FAILED,
                "lease_holder": None,
                "lease_expires_at": None,
                "error_code": error_code,
                "completed_at": now,
                "updated_at": now,
            })
            updated_run = self._mutate_run(
                connection,
                run,
                run.revision,
                lambda item: item.model_copy(update={
                    "state": MeasurementState.NEEDS_REVIEW,
                    "events": (*item.events, MeasurementEvent(
                        sequence=len(item.events) + 1,
                        event_type="job-failed",
                    )),
                }),
            )
            self._write_job(connection, failed)
            return failed, updated_run

    def cancel_job(self, job_id: str, owner: OwnerIdentity) -> tuple[WorkflowJob, MeasurementRun]:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(workflow_jobs).where(
                    workflow_jobs.c.job_id == job_id,
                    workflow_jobs.c.owner_key == owner.key,
                )
            ).first()
            if row is None:
                raise NotFound("Workflow job not found")
            job = self._job_from_row(row)
            run = self._read(connection, job.run_id, owner)
            if job.state == JobState.CANCELLED:
                return job, run
            if job.state in {JobState.COMPLETED, JobState.FAILED}:
                raise Conflict("Completed workflow jobs cannot be cancelled")
            now = utc_now()
            cancelled = job.model_copy(update={
                "state": JobState.CANCELLED,
                "lease_holder": None,
                "lease_expires_at": None,
                "completed_at": now,
                "updated_at": now,
            })
            updated_run = self._mutate_run(
                connection,
                run,
                run.revision,
                lambda item: item.model_copy(update={
                    "state": MeasurementState.CANCELLED,
                    "events": (*item.events, MeasurementEvent(
                        sequence=len(item.events) + 1,
                        event_type="cancelled",
                    )),
                }),
            )
            self._write_job(connection, cancelled)
            return cancelled, updated_run

    def recover_interrupted(self, now: datetime | None = None) -> tuple[WorkflowJob, ...]:
        current_time = now or utc_now()
        recovered: list[WorkflowJob] = []
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(workflow_jobs).where(
                    workflow_jobs.c.state == JobState.LEASED.value,
                    workflow_jobs.c.lease_expires_at <= current_time.isoformat(),
                )
            ).all()
            for row in rows:
                job = self._job_from_row(row)
                run = self._read(connection, job.run_id, job.owner)
                failed = job.model_copy(update={
                    "state": JobState.FAILED,
                    "lease_holder": None,
                    "lease_expires_at": None,
                    "error_code": "lease-expired",
                    "completed_at": current_time,
                    "updated_at": current_time,
                })
                self._mutate_run(
                    connection,
                    run,
                    run.revision,
                    lambda item: item.model_copy(update={
                        "state": MeasurementState.NEEDS_REVIEW,
                        "events": (*item.events, MeasurementEvent(
                            sequence=len(item.events) + 1,
                            event_type="job-interrupted",
                        )),
                    }),
                )
                self._write_job(connection, failed)
                recovered.append(failed)
        return tuple(recovered)

    def close(self) -> None:
        self.engine.dispose()


class SQLiteMeasurementRepository(SQLAlchemyMeasurementRepository):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(f"sqlite:///{path.resolve().as_posix()}", initialize_schema=True)