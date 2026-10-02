import json
import sqlite3
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
    delete as sql_delete,
    event,
    func,
    inspect,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from pydantic import BaseModel

from geo_agent.agent_access import AgentExecutionAuthorization, ExecutionStage
from geo_agent.artifact_storage import ArtifactKind, MeasurementArtifact
from geo_agent.contracts import Brief, MeasurementInputs, digest, identifier, utc_now
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.evidence_assessment import BrandDefinition, BrandDefinitionRecord
from geo_agent.jobs import (
    JobState,
    JobType,
    LeaseLost,
    OperationClaim,
    OperationClaimState,
    RunProgress,
    WorkflowJob,
)
from geo_agent.measurement_budget import (
    MeasurementBudgetGrant,
    MeasurementCapacity,
    MeasurementOperationAllowances,
    MeasurementOperationCapacity,
)
from geo_agent.measurement_workflow import (
    MeasurementApproval,
    MeasurementEvent,
    MeasurementRepository,
    MeasurementRun,
    MeasurementRunSummary,
    MeasurementState,
    Mutation,
    OwnerIdentity,
)
from geo_agent.projects import Project, ProjectCreate, ProjectUpdate
from geo_agent.specialist_agents import (
    AgentBindingSnapshot,
    AgentStageRecord,
    AgentStageStatus,
    SpecialistAgentRole,
    SpecialistProviderMode,
    upsert_stage,
)
from geo_agent.specialist_workflow import SpecialistStageRequest
from geo_agent.workflow import Conflict, NotFound


metadata = MetaData()
LATEST_SCHEMA_REVISION = "0005_nullable_export_reservations"

projects = Table(
    "projects",
    metadata,
    Column("project_id", String(64), primary_key=True),
    Column("owner_key", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Index("ix_projects_owner_updated", "owner_key", "updated_at"),
)

measurement_runs = Table(
    "measurement_runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("owner_key", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("state", String(40), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("summary_payload", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Index("ix_measurement_runs_owner_updated", "owner_key", "updated_at", "run_id"),
)

project_runs = Table(
    "project_runs",
    metadata,
    Column("project_id", String(64), ForeignKey("projects.project_id"), primary_key=True),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), primary_key=True),
    Column("owner_key", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("run_id", name="uq_project_runs_run"),
    Index("ix_project_runs_project", "project_id", "created_at"),
)

project_conversations = Table(
    "project_conversations",
    metadata,
    Column("conversation_id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("projects.project_id"), nullable=False),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id")),
    Column("owner_key", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Index("ix_project_conversations_project_updated", "project_id", "owner_key", "updated_at"),
)

project_conversation_runs = Table(
    "project_conversation_runs",
    metadata,
    Column(
        "conversation_id",
        String(64),
        ForeignKey("project_conversations.conversation_id"),
        primary_key=True,
    ),
    Column(
        "run_id",
        String(64),
        ForeignKey("measurement_runs.run_id"),
        primary_key=True,
    ),
    Column("project_id", String(64), ForeignKey("projects.project_id"), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("linked_at", String(40), nullable=False),
    Index(
        "ix_project_conversation_runs_project",
        "project_id",
        "owner_key",
        "linked_at",
    ),
)

project_agent_budgets = Table(
    "project_agent_budgets",
    metadata,
    Column("owner_key", String(64), primary_key=True),
    Column("request_limit", Integer, nullable=False),
    Column("used", Integer, nullable=False),
)

project_llm_operations = Table(
    "project_llm_operations",
    metadata,
    Column("operation_id", String(64), primary_key=True),
    Column("project_id", String(64), ForeignKey("projects.project_id"), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("operation_type", String(64), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("summary", Text),
    Column("provider_response_id", String(500)),
    Column("fallback_used", Integer, nullable=False),
    Column("usage_payload", Text, nullable=False),
    Column("error_code", String(100)),
    Column("created_at", String(40), nullable=False),
    Column("completed_at", String(40)),
    UniqueConstraint(
        "owner_key",
        "operation_type",
        "idempotency_key",
        name="uq_project_llm_operations_owner_type_key",
    ),
    Index(
        "ix_project_llm_operations_project_created",
        "project_id",
        "owner_key",
        "created_at",
    ),
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
    Column("lease_token", String(64)),
    Column("policy_id", String(100)),
    Column("policy_hash", String(64)),
    Column("execution_principal_id", String(200)),
    Column("execution_authorization_id", String(64)),
    Column("operation_ceiling", Integer),
    Column("lease_expires_at", String(40)),
    Column("completed_at", String(40)),
    Column("error_code", String(100)),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("owner_key", "idempotency_key", name="uq_workflow_jobs_owner_idempotency"),
    Index("ix_workflow_jobs_state_created", "state", "created_at"),
    Index(
        "ix_workflow_jobs_dispatch",
        "state",
        "policy_id",
        "policy_hash",
        "owner_key",
        "created_at",
    ),
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
    Column("checkpoint_payload", Text),
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
    Column("kind", String(40), nullable=False, default=ArtifactKind.MEASUREMENT.value),
    Column("content_hash", String(64), nullable=False),
    Column("media_type", String(120), nullable=False),
    Column("size", Integer, nullable=False),
    Column("storage_key", String(500), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint(
        "run_id",
        "kind",
        "content_hash",
        name="uq_artifacts_run_kind_hash",
    ),
    Index("ix_artifacts_run_kind", "run_id", "owner_key", "kind", "created_at"),
)

workflow_admission_lock = Table(
    "workflow_admission_lock",
    metadata,
    Column("lock_id", Integer, primary_key=True),
)

run_create_requests = Table(
    "run_create_requests",
    metadata,
    Column("owner_key", String(64), primary_key=True),
    Column("idempotency_key", String(200), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), nullable=False),
    Column("created_at", String(40), nullable=False),
)

artifact_create_requests = Table(
    "artifact_create_requests",
    metadata,
    Column("owner_key", String(64), primary_key=True),
    Column("idempotency_key", String(200), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("artifact_id", String(64), ForeignKey("artifacts.artifact_id")),
    Column("created_at", String(40), nullable=False),
)

agent_execution_authorizations = Table(
    "agent_execution_authorizations",
    metadata,
    Column("authorization_id", String(64), primary_key=True),
    Column("run_id", String(64), ForeignKey("measurement_runs.run_id"), nullable=False),
    Column("owner_key", String(64), nullable=False),
    Column("principal_id", String(200), nullable=False),
    Column("stage", String(40), nullable=False),
    Column("run_revision", Integer, nullable=False),
    Column("input_hash", String(64)),
    Column("policy_id", String(100), nullable=False),
    Column("policy_hash", String(64), nullable=False),
    Column("operation_ceiling", Integer, nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("consumed_at", String(40)),
    Column("consumed_by_job_id", String(64)),
    Column("payload", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Index(
        "ix_agent_authorizations_principal_run",
        "owner_key",
        "principal_id",
        "run_id",
        "stage",
        "expires_at",
    ),
)

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_id", String(200), primary_key=True),
    Column("current_job_id", String(64)),
    Column("last_seen_at", String(40), nullable=False),
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
    _ACTIVE_DELETION_STATES = frozenset({
        MeasurementState.PREPARING,
        MeasurementState.QUEUED,
        MeasurementState.EVALUATING,
        MeasurementState.RECOMMENDING,
    })

    def __init__(self, database_url: str, *, initialize_schema: bool = False):
        self.engine = create_engine(database_url)
        self._measurement_budget_binding: tuple[str, str, str] | None = None
        self._measurement_budget_enforced = True
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        if initialize_schema:
            metadata.create_all(self.engine)
            with self.engine.begin() as connection:
                if connection.execute(
                    select(workflow_admission_lock.c.lock_id).where(
                        workflow_admission_lock.c.lock_id == 1
                    )
                ).scalar_one_or_none() is None:
                    connection.execute(insert(workflow_admission_lock).values(lock_id=1))

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @staticmethod
    def _event(event_type: str, sequence: int = 1) -> MeasurementEvent:
        return MeasurementEvent(sequence=sequence, event_type=event_type)

    @staticmethod
    def _read_project(
        connection: Connection,
        project_id: str,
        owner: OwnerIdentity,
    ) -> Project:
        payload = connection.execute(
            select(projects.c.payload).where(
                projects.c.project_id == project_id,
                projects.c.owner_key == owner.key,
            )
        ).scalar_one_or_none()
        if payload is None:
            raise NotFound("Project not found")
        return Project.model_validate_json(payload)

    def create_project(self, owner: OwnerIdentity, request: ProjectCreate) -> Project:
        project = Project(owner=owner, **request.model_dump())
        try:
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(projects.c.project_id).where(projects.c.owner_key == owner.key)
                ).scalars()
                for project_id in existing:
                    current = self._read_project(connection, project_id, owner)
                    if set(project.domains) & set(current.domains):
                        raise Conflict("A project already uses one of these domains")
                connection.execute(insert(projects).values(
                    project_id=project.project_id,
                    owner_key=owner.key,
                    revision=project.revision,
                    payload=project.model_dump_json(),
                    created_at=project.created_at.isoformat(),
                    updated_at=project.updated_at.isoformat(),
                ))
            return project
        except IntegrityError:
            raise Conflict("Project could not be created") from None

    def get_project(self, project_id: str, owner: OwnerIdentity) -> Project:
        with self.engine.connect() as connection:
            return self._read_project(connection, project_id, owner)

    def list_projects(self, owner: OwnerIdentity) -> tuple[Project, ...]:
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(projects.c.payload).where(projects.c.owner_key == owner.key)
            ).scalars()
            records = sorted(
                (Project.model_validate_json(payload) for payload in payloads),
                key=lambda project: (
                    project.archived,
                    project.name.casefold(),
                    project.project_id,
                ),
            )
            return tuple(records)

    def update_project(
        self,
        project_id: str,
        owner: OwnerIdentity,
        request: ProjectUpdate,
    ) -> Project:
        with self.engine.begin() as connection:
            current = self._read_project(connection, project_id, owner)
            if current.revision != request.expected_revision:
                raise Conflict("Stale project revision; reload the project")
            changes = request.model_dump(exclude={"expected_revision"}, exclude_unset=True)
            candidate_values = {
                **current.model_dump(),
                **changes,
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            }
            candidate_request = ProjectCreate.model_validate({
                key: candidate_values[key]
                for key in (
                    "name",
                    "primary_domain",
                    "additional_domains",
                    "competitor_domains",
                    "default_locale",
                    "active_goal",
                    "colour",
                    "foundry",
                    "specialist_agents",
                )
            })
            candidate = Project.model_validate({
                **candidate_values,
                **candidate_request.model_dump(),
            })
            other_ids = connection.execute(
                select(projects.c.project_id).where(
                    projects.c.owner_key == owner.key,
                    projects.c.project_id != project_id,
                )
            ).scalars()
            for other_id in other_ids:
                other = self._read_project(connection, other_id, owner)
                if set(candidate.domains) & set(other.domains):
                    raise Conflict("A project already uses one of these domains")
            result = connection.execute(
                update(projects)
                .where(
                    projects.c.project_id == project_id,
                    projects.c.owner_key == owner.key,
                    projects.c.revision == current.revision,
                )
                .values(
                    revision=candidate.revision,
                    payload=candidate.model_dump_json(),
                    updated_at=candidate.updated_at.isoformat(),
                )
            )
            if result.rowcount != 1:
                raise Conflict("Project changed concurrently; reload the project")
            return candidate

    def archive_project(
        self,
        project_id: str,
        owner: OwnerIdentity,
        expected_revision: int,
    ) -> Project:
        current = self.get_project(project_id, owner)
        if current.archived:
            return current
        with self.engine.begin() as connection:
            current = self._read_project(connection, project_id, owner)
            if current.revision != expected_revision:
                raise Conflict("Stale project revision; reload the project")
            archived = current.model_copy(update={
                "archived": True,
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            })
            changed = connection.execute(
                update(projects)
                .where(
                    projects.c.project_id == project_id,
                    projects.c.owner_key == owner.key,
                    projects.c.revision == current.revision,
                )
                .values(
                    revision=archived.revision,
                    payload=archived.model_dump_json(),
                    updated_at=archived.updated_at.isoformat(),
                )
            )
            if changed.rowcount != 1:
                raise Conflict("Project changed concurrently; reload the project")
            return archived

    def bind_run_to_project(
        self,
        project_id: str,
        run_id: str,
        owner: OwnerIdentity,
    ) -> None:
        try:
            with self.engine.begin() as connection:
                self._read_project(connection, project_id, owner)
                self._read(connection, run_id, owner)
                existing = connection.execute(
                    select(project_runs.c.project_id).where(project_runs.c.run_id == run_id)
                ).scalar_one_or_none()
                if existing is not None:
                    if existing != project_id:
                        raise Conflict("Measurement run already belongs to another project")
                    return
                connection.execute(insert(project_runs).values(
                    project_id=project_id,
                    run_id=run_id,
                    owner_key=owner.key,
                    created_at=utc_now().isoformat(),
                ))
        except IntegrityError:
            raise Conflict("Measurement run could not be assigned to the project") from None

    def list_project_runs(
        self,
        project_id: str,
        owner: OwnerIdentity,
        limit: int | None = 50,
    ) -> tuple[MeasurementRun, ...]:
        if limit is not None and not 1 <= limit <= 100:
            raise Conflict("Run list limit must be between 1 and 100")
        with self.engine.connect() as connection:
            self._read_project(connection, project_id, owner)
            payloads = connection.execute(
                select(measurement_runs.c.payload)
                .select_from(project_runs.join(
                    measurement_runs,
                    project_runs.c.run_id == measurement_runs.c.run_id,
                ))
                .where(
                    project_runs.c.project_id == project_id,
                    project_runs.c.owner_key == owner.key,
                    measurement_runs.c.owner_key == owner.key,
                )
            ).scalars()
            runs = sorted(
                (MeasurementRun.model_validate_json(payload) for payload in payloads),
                key=lambda run: (run.updated_at, run.run_id),
                reverse=True,
            )
            return tuple(runs if limit is None else runs[:limit])

    def get_project_run(
        self,
        project_id: str,
        run_id: str,
        owner: OwnerIdentity,
    ) -> MeasurementRun:
        with self.engine.connect() as connection:
            self._read_project(connection, project_id, owner)
            mapped = connection.execute(
                select(project_runs.c.run_id).where(
                    project_runs.c.project_id == project_id,
                    project_runs.c.run_id == run_id,
                    project_runs.c.owner_key == owner.key,
                )
            ).scalar_one_or_none()
            if mapped is None:
                raise NotFound("Measurement run not found in project")
            return self._read(connection, run_id, owner)

    def get_run_project_id(
        self,
        run_id: str,
        owner: OwnerIdentity,
    ) -> str | None:
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            return connection.execute(
                select(project_runs.c.project_id).where(
                    project_runs.c.run_id == run_id,
                    project_runs.c.owner_key == owner.key,
                )
            ).scalar_one_or_none()

    @staticmethod
    def _reconcile_conversations_for_deleted_run(
        connection: Connection,
        project_id: str,
        run_id: str,
        owner: OwnerIdentity,
    ) -> None:
        from geo_agent.project_chat import ProjectConversation

        rows = connection.execute(select(project_conversations).where(
            project_conversations.c.project_id == project_id,
            project_conversations.c.owner_key == owner.key,
        )).all()
        for row in rows:
            current = ProjectConversation.model_validate_json(row.payload)
            if (
                current.run_id != run_id
                and run_id not in current.linked_run_ids
                and run_id not in current.comparison_run_ids
                and run_id not in current.bound_run_contexts
                and all(
                    workflow.run_id != run_id
                    for workflow in current.measurement_workflows
                )
            ):
                continue
            active_run_id = None if current.run_id == run_id else current.run_id
            contexts = {
                linked_run_id: context
                for linked_run_id, context in current.bound_run_contexts.items()
                if linked_run_id != run_id
            }
            workflows = tuple(
                workflow
                for workflow in current.measurement_workflows
                if workflow.run_id != run_id
            )
            updated = current.model_copy(update={
                "run_id": active_run_id,
                "linked_run_ids": tuple(
                    linked_run_id
                    for linked_run_id in current.linked_run_ids
                    if linked_run_id != run_id
                ),
                "comparison_run_ids": tuple(
                    comparison_run_id
                    for comparison_run_id in current.comparison_run_ids
                    if comparison_run_id != run_id
                ),
                "measurement_workflow": (
                    None
                    if (
                        current.measurement_workflow is not None
                        and current.measurement_workflow.run_id == run_id
                    )
                    else current.measurement_workflow
                ),
                "measurement_workflows": workflows,
                "bound_project_context": (
                    contexts.get(active_run_id)
                    if active_run_id is not None
                    else None
                ),
                "bound_run_contexts": contexts,
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            })
            changed = connection.execute(
                update(project_conversations)
                .where(
                    project_conversations.c.conversation_id == current.conversation_id,
                    project_conversations.c.project_id == project_id,
                    project_conversations.c.owner_key == owner.key,
                    project_conversations.c.revision == current.revision,
                )
                .values(
                    run_id=active_run_id,
                    revision=updated.revision,
                    payload=updated.model_dump_json(),
                    updated_at=updated.updated_at.isoformat(),
                )
            )
            if changed.rowcount != 1:
                raise Conflict("Conversation changed concurrently; reload it")
        connection.execute(sql_delete(project_conversation_runs).where(
            project_conversation_runs.c.project_id == project_id,
            project_conversation_runs.c.run_id == run_id,
            project_conversation_runs.c.owner_key == owner.key,
        ))

    def _delete_run_rows(
        self,
        connection: Connection,
        project_id: str,
        run_id: str,
        owner: OwnerIdentity,
        *,
        reconcile_conversations: bool,
    ) -> tuple[str, ...]:
        run = self._read(connection, run_id, owner)
        if run.state in self._ACTIVE_DELETION_STATES:
            raise Conflict("Active measurement runs cannot be deleted")
        self._require_no_active_specialist_job(connection, run_id, owner)
        artifact_rows = connection.execute(select(
            artifacts.c.artifact_id,
            artifacts.c.storage_key,
        ).where(
            artifacts.c.run_id == run_id,
            artifacts.c.owner_key == owner.key,
        )).all()
        artifact_ids = tuple(row.artifact_id for row in artifact_rows)
        storage_keys = tuple(row.storage_key for row in artifact_rows)
        if reconcile_conversations:
            self._reconcile_conversations_for_deleted_run(
                connection,
                project_id,
                run_id,
                owner,
            )
        connection.execute(sql_delete(measurement_budget_consumptions).where(
            measurement_budget_consumptions.c.claim_id.in_(
                select(operation_claims.c.claim_id).where(
                    operation_claims.c.run_id == run_id,
                )
            )
        ))
        connection.execute(sql_delete(operation_claims).where(
            operation_claims.c.run_id == run_id,
        ))
        connection.execute(sql_delete(workflow_jobs).where(
            workflow_jobs.c.run_id == run_id,
            workflow_jobs.c.owner_key == owner.key,
        ))
        connection.execute(sql_delete(agent_capabilities).where(
            agent_capabilities.c.conversation_id.in_(
                select(agent_conversations.c.conversation_id).where(
                    agent_conversations.c.run_id == run_id,
                    agent_conversations.c.owner_key == owner.key,
                )
            )
        ))
        connection.execute(sql_delete(agent_conversations).where(
            agent_conversations.c.run_id == run_id,
            agent_conversations.c.owner_key == owner.key,
        ))
        connection.execute(sql_delete(agent_execution_authorizations).where(
            agent_execution_authorizations.c.run_id == run_id,
            agent_execution_authorizations.c.owner_key == owner.key,
        ))
        connection.execute(sql_delete(run_create_requests).where(
            run_create_requests.c.run_id == run_id,
            run_create_requests.c.owner_key == owner.key,
        ))
        if artifact_ids:
            connection.execute(sql_delete(artifact_create_requests).where(
                artifact_create_requests.c.artifact_id.in_(artifact_ids),
                artifact_create_requests.c.owner_key == owner.key,
            ))
        for table in (artifacts, approvals, run_events, run_brand_definitions):
            connection.execute(sql_delete(table).where(table.c.run_id == run_id))
        connection.execute(sql_delete(project_runs).where(
            project_runs.c.project_id == project_id,
            project_runs.c.run_id == run_id,
            project_runs.c.owner_key == owner.key,
        ))
        connection.execute(sql_delete(measurement_runs).where(
            measurement_runs.c.run_id == run_id,
            measurement_runs.c.owner_key == owner.key,
        ))
        return storage_keys

    def delete_project_run(
        self,
        project_id: str,
        run_id: str,
        owner: OwnerIdentity,
    ) -> tuple[str, ...]:
        with self.engine.begin() as connection:
            self._read_project(connection, project_id, owner)
            mapped = connection.execute(select(project_runs.c.run_id).where(
                project_runs.c.project_id == project_id,
                project_runs.c.run_id == run_id,
                project_runs.c.owner_key == owner.key,
            )).scalar_one_or_none()
            if mapped is None:
                raise NotFound("Measurement run not found in project")
            return self._delete_run_rows(
                connection,
                project_id,
                run_id,
                owner,
                reconcile_conversations=True,
            )

    def delete_project(
        self,
        project_id: str,
        owner: OwnerIdentity,
    ) -> tuple[str, ...]:
        with self.engine.begin() as connection:
            self._read_project(connection, project_id, owner)
            run_ids = tuple(connection.execute(select(project_runs.c.run_id).where(
                project_runs.c.project_id == project_id,
                project_runs.c.owner_key == owner.key,
            )).scalars())
            runs = tuple(self._read(connection, run_id, owner) for run_id in run_ids)
            if any(run.state in self._ACTIVE_DELETION_STATES for run in runs):
                raise Conflict("Projects with active measurement runs cannot be deleted")
            connection.execute(sql_delete(project_conversation_runs).where(
                project_conversation_runs.c.project_id == project_id,
                project_conversation_runs.c.owner_key == owner.key,
            ))
            connection.execute(sql_delete(project_conversations).where(
                project_conversations.c.project_id == project_id,
                project_conversations.c.owner_key == owner.key,
            ))
            connection.execute(sql_delete(project_llm_operations).where(
                project_llm_operations.c.project_id == project_id,
                project_llm_operations.c.owner_key == owner.key,
            ))
            storage_keys: list[str] = []
            for run_id in run_ids:
                storage_keys.extend(self._delete_run_rows(
                    connection,
                    project_id,
                    run_id,
                    owner,
                    reconcile_conversations=False,
                ))
            deleted = connection.execute(sql_delete(projects).where(
                projects.c.project_id == project_id,
                projects.c.owner_key == owner.key,
            ))
            if deleted.rowcount != 1:
                raise Conflict("Project changed concurrently; reload it")
            return tuple(storage_keys)

    @staticmethod
    def _run_values(run: MeasurementRun) -> dict[str, object]:
        summary = MeasurementRunSummary.from_run(run)
        return {
            "revision": run.revision,
            "state": run.state.value,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "summary_payload": summary.model_dump_json(),
            "payload": run.model_dump_json(),
        }

    def create(
        self,
        owner: OwnerIdentity,
        inputs: MeasurementInputs | None = None,
        brief: Brief | None = None,
        brand_definition: BrandDefinition | None = None,
        project_id: str | None = None,
        competitor_domains: tuple[str, ...] = (),
    ) -> MeasurementRun:
        created_event = self._event("awaiting-query-approval" if inputs else "draft")
        run = MeasurementRun(
            owner=owner,
            competitor_domains=competitor_domains,
            brief=inputs.brief if inputs is not None else brief,
            inputs=inputs,
            state=MeasurementState.AWAITING_APPROVAL if inputs else MeasurementState.DRAFT,
            events=(created_event,),
        )
        with self.engine.begin() as connection:
            if project_id is not None:
                self._read_project(connection, project_id, owner)
                if inputs is not None:
                    self._require_live_project_run(run)
            connection.execute(insert(measurement_runs).values(
                run_id=run.run_id,
                owner_key=owner.key,
                **self._run_values(run),
            ))
            self._insert_events(connection, run.run_id, (created_event,))
            if brand_definition is not None:
                self._insert_brand_definition(connection, owner, BrandDefinitionRecord(
                    run_id=run.run_id, definition_version=1, definition=brand_definition))
            if project_id is not None:
                connection.execute(insert(project_runs).values(
                    project_id=project_id,
                    run_id=run.run_id,
                    owner_key=owner.key,
                    created_at=run.created_at.isoformat(),
                ))
        return run

    def create_idempotent(
        self,
        owner: OwnerIdentity,
        brief: Brief,
        brand_definition: BrandDefinition | None,
        idempotency_key: str,
    ) -> MeasurementRun:
        if not idempotency_key.strip():
            raise Conflict("A bounded idempotency key is required")
        request_hash = digest({
            "brief": brief.model_dump(mode="json"),
            "brand_definition": (
                brand_definition.model_dump(mode="json")
                if brand_definition is not None
                else None
            ),
        })
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(run_create_requests).where(
                    run_create_requests.c.owner_key == owner.key,
                    run_create_requests.c.idempotency_key == idempotency_key,
                )
            ).first()
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise Conflict("Idempotency key is bound to a different run request")
                return self._read(connection, existing.run_id, owner)
            run = MeasurementRun(
                owner=owner,
                brief=brief,
                state=MeasurementState.DRAFT,
                events=(self._event("draft"),),
            )
            connection.execute(insert(measurement_runs).values(
                run_id=run.run_id,
                owner_key=owner.key,
                **self._run_values(run),
            ))
            self._insert_events(connection, run.run_id, run.events)
            if brand_definition is not None:
                self._insert_brand_definition(
                    connection,
                    owner,
                    BrandDefinitionRecord(
                        run_id=run.run_id,
                        definition_version=1,
                        definition=brand_definition,
                    ),
                )
            try:
                connection.execute(insert(run_create_requests).values(
                    owner_key=owner.key,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    run_id=run.run_id,
                    created_at=run.created_at.isoformat(),
                ))
            except IntegrityError as error:
                raise Conflict("Idempotency key changed concurrently; retry the request") from error
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
    def _authorization_from_row(row: Any) -> AgentExecutionAuthorization:
        return AgentExecutionAuthorization.model_validate_json(row.payload)

    def issue_execution_authorization(
        self,
        *,
        run_id: str,
        owner: OwnerIdentity,
        principal_id: str,
        stage: ExecutionStage,
        expected_revision: int,
        input_hash: str | None,
        policy: MeasurementExecutionPolicy,
        operation_ceiling: int,
        lifetime_seconds: int,
    ) -> AgentExecutionAuthorization:
        with self.engine.begin() as connection:
            run = self._read(connection, run_id, owner)
            if run.revision != expected_revision:
                raise Conflict("Stale measurement revision; reload the run")
            if stage == ExecutionStage.PREPARE:
                if run.state != MeasurementState.DRAFT or run.brief is None:
                    raise Conflict("Preparation authorization requires a draft run")
                if input_hash is not None:
                    raise Conflict("Preparation authorization does not accept an input hash")
            else:
                if (
                    run.state != MeasurementState.AWAITING_APPROVAL
                    or run.inputs is None
                    or run.approval is None
                ):
                    raise Conflict("Evaluation authorization requires current human query approval")
                if input_hash != run.inputs.approval_hash:
                    raise Conflict("Evaluation authorization does not match the current input hash")
            record = AgentExecutionAuthorization.issue(
                owner=owner,
                principal_id=principal_id,
                run_id=run_id,
                stage=stage,
                run_revision=expected_revision,
                input_hash=input_hash,
                policy_id=policy.policy_id,
                policy_hash=policy.policy_hash,
                operation_ceiling=operation_ceiling,
                lifetime_seconds=lifetime_seconds,
            )
            connection.execute(insert(agent_execution_authorizations).values(
                authorization_id=record.authorization_id,
                run_id=record.run_id,
                owner_key=owner.key,
                principal_id=record.principal_id,
                stage=record.stage.value,
                run_revision=record.run_revision,
                input_hash=record.input_hash,
                policy_id=record.policy_id,
                policy_hash=record.policy_hash,
                operation_ceiling=record.operation_ceiling,
                expires_at=record.expires_at.isoformat(),
                consumed_at=None,
                consumed_by_job_id=None,
                payload=record.model_dump_json(),
                created_at=record.created_at.isoformat(),
            ))
            return record

    def list_execution_authorizations(
        self,
        run_id: str,
        owner: OwnerIdentity,
        principal_id: str,
    ) -> tuple[AgentExecutionAuthorization, ...]:
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            rows = connection.execute(
                select(agent_execution_authorizations.c.payload)
                .where(
                    agent_execution_authorizations.c.run_id == run_id,
                    agent_execution_authorizations.c.owner_key == owner.key,
                    agent_execution_authorizations.c.principal_id == principal_id,
                    agent_execution_authorizations.c.consumed_at.is_(None),
                    agent_execution_authorizations.c.expires_at > utc_now().isoformat(),
                )
                .order_by(
                    agent_execution_authorizations.c.created_at.desc(),
                    agent_execution_authorizations.c.authorization_id.desc(),
                )
                .limit(20)
            ).scalars()
            return tuple(
                AgentExecutionAuthorization.model_validate_json(payload)
                for payload in rows
            )

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
    def _require_no_active_specialist_job(
        connection: Connection,
        run_id: str,
        owner: OwnerIdentity,
    ) -> None:
        active = connection.execute(
            select(workflow_jobs.c.job_id).where(
                workflow_jobs.c.run_id == run_id,
                workflow_jobs.c.owner_key == owner.key,
                workflow_jobs.c.job_type == JobType.AGENT_STAGE.value,
                workflow_jobs.c.state.in_((
                    JobState.QUEUED.value,
                    JobState.LEASED.value,
                )),
            ).limit(1)
        ).scalar_one_or_none()
        if active is not None:
            raise Conflict(
                "A specialist stage is active; wait for it to finish or cancel it"
            )

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
                select(measurement_runs.c.payload)
                .where(measurement_runs.c.owner_key == owner.key)
                .order_by(
                    measurement_runs.c.updated_at.desc(),
                    measurement_runs.c.run_id.desc(),
                )
                .limit(limit)
            ).scalars()
            return tuple(MeasurementRun.model_validate_json(payload) for payload in payloads)

    def list_run_summaries(
        self,
        owner: OwnerIdentity,
        limit: int,
        *,
        state: MeasurementState | None = None,
        before_updated_at: datetime | None = None,
        before_run_id: str | None = None,
    ) -> tuple[MeasurementRunSummary, ...]:
        if not 1 <= limit <= 51:
            raise Conflict("Run summary limit must be between 1 and 51")
        if (before_updated_at is None) != (before_run_id is None):
            raise Conflict("Run summary cursor is incomplete")
        statement = select(measurement_runs.c.summary_payload).where(
            measurement_runs.c.owner_key == owner.key
        )
        if state is not None:
            statement = statement.where(measurement_runs.c.state == state.value)
        if before_updated_at is not None and before_run_id is not None:
            stamp = before_updated_at.isoformat()
            statement = statement.where(or_(
                measurement_runs.c.updated_at < stamp,
                (
                    (measurement_runs.c.updated_at == stamp)
                    & (measurement_runs.c.run_id < before_run_id)
                ),
            ))
        with self.engine.connect() as connection:
            payloads = connection.execute(
                statement.order_by(
                    measurement_runs.c.updated_at.desc(),
                    measurement_runs.c.run_id.desc(),
                ).limit(limit)
            ).scalars()
            return tuple(
                MeasurementRunSummary.model_validate_json(payload)
                for payload in payloads
            )

    @staticmethod
    def _artifact_from_row(row: Any) -> MeasurementArtifact:
        return MeasurementArtifact(
            artifact_id=row.artifact_id,
            run_id=row.run_id,
            kind=row.kind,
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
                artifacts.c.kind == ArtifactKind.MEASUREMENT.value,
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
                self._require_no_active_specialist_job(
                    connection,
                    artifact.run_id,
                    owner,
                )
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
                    kind=artifact.kind.value,
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

    def persist_companion(
        self,
        artifact: MeasurementArtifact,
        owner: OwnerIdentity,
        revision: int,
    ) -> MeasurementArtifact:
        if artifact.kind == ArtifactKind.MEASUREMENT:
            raise Conflict("Companion persistence requires a companion artifact kind")
        try:
            with self.engine.begin() as connection:
                run = self._read(connection, artifact.run_id, owner)
                if run.revision != revision:
                    raise Conflict("Stale measurement revision; reload the run")
                existing = connection.execute(
                    select(artifacts).where(
                        artifacts.c.run_id == artifact.run_id,
                        artifacts.c.owner_key == owner.key,
                        artifacts.c.kind == artifact.kind.value,
                        artifacts.c.content_hash == artifact.content_hash,
                    )
                ).first()
                if existing is not None:
                    return self._artifact_from_row(existing)
                connection.execute(insert(artifacts).values(
                    artifact_id=artifact.artifact_id,
                    run_id=artifact.run_id,
                    owner_key=owner.key,
                    kind=artifact.kind.value,
                    content_hash=artifact.content_hash,
                    media_type=artifact.media_type,
                    size=artifact.size,
                    storage_key=artifact.storage_key,
                    created_at=artifact.created_at.isoformat(),
                ))
                return artifact
        except IntegrityError:
            with self.engine.connect() as connection:
                existing = connection.execute(
                    select(artifacts).where(
                        artifacts.c.run_id == artifact.run_id,
                        artifacts.c.owner_key == owner.key,
                        artifacts.c.content_hash == artifact.content_hash,
                    )
                ).first()
                if existing is not None:
                    return self._artifact_from_row(existing)
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

    def list_artifacts(
        self,
        run_id: str,
        owner: OwnerIdentity,
        limit: int = 50,
        *,
        before_created_at: datetime | None = None,
        before_artifact_id: str | None = None,
    ) -> tuple[MeasurementArtifact, ...]:
        if not 1 <= limit <= 100:
            raise Conflict("Artifact list limit must be between 1 and 100")
        if (before_created_at is None) != (before_artifact_id is None):
            raise Conflict("Artifact cursor is incomplete")
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            statement = select(artifacts).where(
                artifacts.c.run_id == run_id,
                artifacts.c.owner_key == owner.key,
            )
            if before_created_at is not None and before_artifact_id is not None:
                stamp = before_created_at.isoformat()
                statement = statement.where(or_(
                    artifacts.c.created_at < stamp,
                    (
                        (artifacts.c.created_at == stamp)
                        & (artifacts.c.artifact_id < before_artifact_id)
                    ),
                ))
            rows = connection.execute(
                statement
                .order_by(artifacts.c.created_at.desc(), artifacts.c.artifact_id.desc())
                .limit(limit)
            ).all()
            return tuple(self._artifact_from_row(row) for row in rows)

    def reserve_export_request(
        self,
        owner: OwnerIdentity,
        idempotency_key: str,
        request_hash: str,
    ) -> MeasurementArtifact | None:
        try:
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(artifact_create_requests).where(
                        artifact_create_requests.c.owner_key == owner.key,
                        artifact_create_requests.c.idempotency_key == idempotency_key,
                    )
                ).first()
                if existing is None:
                    connection.execute(insert(artifact_create_requests).values(
                        owner_key=owner.key,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        artifact_id=None,
                        created_at=utc_now().isoformat(),
                    ))
                    return None
                if existing.request_hash != request_hash:
                    raise Conflict("Idempotency key is bound to a different export request")
                if existing.artifact_id is None:
                    return None
                row = connection.execute(
                    select(artifacts).where(
                        artifacts.c.artifact_id == existing.artifact_id,
                        artifacts.c.owner_key == owner.key,
                    )
                ).one()
                return self._artifact_from_row(row)
        except IntegrityError:
            with self.engine.connect() as connection:
                existing = connection.execute(
                    select(artifact_create_requests).where(
                        artifact_create_requests.c.owner_key == owner.key,
                        artifact_create_requests.c.idempotency_key == idempotency_key,
                    )
                ).first()
                if existing is None:
                    raise
                if existing.request_hash != request_hash:
                    raise Conflict("Idempotency key is bound to a different export request")
                if existing.artifact_id is None:
                    return None
                row = connection.execute(
                    select(artifacts).where(
                        artifacts.c.artifact_id == existing.artifact_id,
                        artifacts.c.owner_key == owner.key,
                    )
                ).one()
                return self._artifact_from_row(row)

    def bind_export_request(
        self,
        owner: OwnerIdentity,
        idempotency_key: str,
        request_hash: str,
        artifact: MeasurementArtifact,
    ) -> MeasurementArtifact:
        try:
            with self.engine.begin() as connection:
                existing = connection.execute(
                    select(artifact_create_requests).where(
                        artifact_create_requests.c.owner_key == owner.key,
                        artifact_create_requests.c.idempotency_key == idempotency_key,
                    )
                ).first()
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise Conflict("Idempotency key is bound to a different export request")
                    if existing.artifact_id is None:
                        changed = connection.execute(
                            update(artifact_create_requests)
                            .where(
                                artifact_create_requests.c.owner_key == owner.key,
                                artifact_create_requests.c.idempotency_key == idempotency_key,
                                artifact_create_requests.c.request_hash == request_hash,
                                artifact_create_requests.c.artifact_id.is_(None),
                            )
                            .values(artifact_id=artifact.artifact_id)
                        )
                        if changed.rowcount == 1:
                            return artifact
                        existing = connection.execute(
                            select(artifact_create_requests).where(
                                artifact_create_requests.c.owner_key == owner.key,
                                artifact_create_requests.c.idempotency_key == idempotency_key,
                            )
                        ).one()
                    row = connection.execute(
                        select(artifacts).where(
                            artifacts.c.artifact_id == existing.artifact_id,
                            artifacts.c.owner_key == owner.key,
                        )
                    ).one()
                    return self._artifact_from_row(row)
                raise Conflict("Export request was not reserved")
        except IntegrityError:
            replay = self.reserve_export_request(owner, idempotency_key, request_hash)
            if replay is not None:
                return replay
            raise

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
        if inspect(connection).has_table(project_runs.name) and connection.execute(
            select(project_runs.c.run_id).where(project_runs.c.run_id == current.run_id)
        ).scalar_one_or_none() is not None:
            self._require_live_project_run(candidate)
        updated = MeasurementRun.model_validate({
            **candidate.model_dump(),
            "revision": current.revision + 1,
            "updated_at": utc_now(),
        })
        values = self._run_values(updated)
        changed = connection.execute(
            update(measurement_runs)
            .where(
                measurement_runs.c.run_id == current.run_id,
                measurement_runs.c.owner_key == current.owner.key,
                measurement_runs.c.revision == revision,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise Conflict("Stale measurement revision; reload the run")
        self._insert_events(connection, current.run_id, updated.events[len(current.events):])
        if updated.approval is not None and updated.approval != current.approval:
            self._insert_approval(connection, current.run_id, updated.approval)
        return updated

    def mutate(self, run_id: str, owner: OwnerIdentity, revision: int, operation: Mutation) -> MeasurementRun:
        with self.engine.begin() as connection:
            self._require_no_active_specialist_job(connection, run_id, owner)
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
                lease_token=job.lease_token,
                policy_id=job.policy_id,
                policy_hash=job.policy_hash,
                execution_principal_id=job.execution_principal_id,
                execution_authorization_id=job.execution_authorization_id,
                operation_ceiling=job.operation_ceiling,
                lease_expires_at=job.lease_expires_at.isoformat() if job.lease_expires_at else None,
                completed_at=job.completed_at.isoformat() if job.completed_at else None,
                error_code=job.error_code,
                payload=job.model_dump_json(),
                updated_at=job.updated_at.isoformat(),
            )
        )

    @staticmethod
    def _lock_admission(connection: Connection) -> None:
        changed = connection.execute(
            update(workflow_admission_lock)
            .where(workflow_admission_lock.c.lock_id == 1)
            .values(lock_id=1)
        )
        if changed.rowcount != 1:
            raise Conflict("Workflow admission control is not initialized")

    @staticmethod
    def _validate_queue_capacity(connection: Connection, owner: OwnerIdentity) -> None:
        queued_total = connection.execute(
            select(func.count()).select_from(workflow_jobs).where(
                workflow_jobs.c.state == JobState.QUEUED.value
            )
        ).scalar_one()
        if queued_total >= 20:
            raise Conflict("Workflow queue is full; at most 20 jobs may be queued")
        owner_queued = connection.execute(
            select(func.count()).select_from(workflow_jobs).where(
                workflow_jobs.c.state == JobState.QUEUED.value,
                workflow_jobs.c.owner_key == owner.key,
            )
        ).scalar_one()
        if owner_queued >= 4:
            raise Conflict("Owner workflow queue is full; at most 4 jobs may be queued")

    def _consume_execution_authorization(
        self,
        connection: Connection,
        *,
        authorization_id: str,
        principal_id: str,
        job_type: JobType,
        run: MeasurementRun,
        policy_id: str,
        policy_hash: str,
        operation_ceiling: int,
        job_id: str,
    ) -> None:
        row = connection.execute(
            select(agent_execution_authorizations).where(
                agent_execution_authorizations.c.authorization_id == authorization_id,
                agent_execution_authorizations.c.owner_key == run.owner.key,
                agent_execution_authorizations.c.principal_id == principal_id,
                agent_execution_authorizations.c.run_id == run.run_id,
            ).with_for_update()
        ).first()
        if row is None:
            raise Conflict("Agent execution authorization was not found")
        record = self._authorization_from_row(row)
        expected_stage = (
            ExecutionStage.PREPARE
            if job_type == JobType.PREPARE
            else ExecutionStage.EVALUATE
        )
        expected_hash = run.inputs.approval_hash if run.inputs is not None else None
        if (
            record.stage != expected_stage
            or record.run_revision != run.revision
            or record.input_hash != (expected_hash if expected_stage == ExecutionStage.EVALUATE else None)
            or record.policy_id != policy_id
            or record.policy_hash != policy_hash
            or record.operation_ceiling != operation_ceiling
            or record.expires_at <= utc_now()
            or record.consumed_at is not None
        ):
            raise Conflict("Agent execution authorization is stale, consumed, expired, or mismatched")
        consumed_at = utc_now()
        consumed = record.model_copy(update={
            "consumed_at": consumed_at,
            "consumed_by_job_id": job_id,
        })
        changed = connection.execute(
            update(agent_execution_authorizations)
            .where(
                agent_execution_authorizations.c.authorization_id == authorization_id,
                agent_execution_authorizations.c.consumed_at.is_(None),
            )
            .values(
                consumed_at=consumed_at.isoformat(),
                consumed_by_job_id=job_id,
                payload=consumed.model_dump_json(),
            )
        )
        if changed.rowcount != 1:
            raise Conflict("Agent execution authorization was consumed concurrently")

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
            expected = WorkflowJob.create(
                current,
                job_type,
                idempotency_key,
                request,
                execution_principal_id=execution_principal_id,
                execution_authorization_id=execution_authorization_id,
                policy_id=policy_id,
                policy_hash=policy_hash,
                operation_ceiling=operation_ceiling,
            )
            if existing_row is not None:
                existing = self._job_from_row(existing_row)
                if (
                    existing.run_id != run_id
                    or existing.job_type != job_type
                    or existing.request != expected.request
                    or existing.execution_principal_id != execution_principal_id
                    or existing.execution_authorization_id != execution_authorization_id
                ):
                    raise Conflict("Idempotency key is bound to a different job request")
                return existing, current
            if job_type != JobType.AGENT_STAGE:
                self._require_no_active_specialist_job(connection, run_id, owner)
            if current.revision != revision:
                raise Conflict("Stale measurement revision; reload the run")
            if job_type == JobType.PREPARE:
                recoverable_failure = (
                    current.state == MeasurementState.NEEDS_REVIEW
                    and current.inputs is None
                )
                if current.state != MeasurementState.DRAFT and not recoverable_failure:
                    raise Conflict(
                        "Preparation can only be queued for a draft or recoverable failed run"
                    )
                next_state = MeasurementState.PREPARING
                event_type = "preparation-queued"
            elif job_type == JobType.EVALUATE:
                if current.state != MeasurementState.AWAITING_APPROVAL or current.approval is None:
                    raise Conflict("Evaluation requires the current automatic query binding")
                next_state = MeasurementState.QUEUED
                event_type = "evaluation-queued"
            elif job_type == JobType.RECOVER_EVALUATORS:
                failed_evaluators = (
                    current.measurement is not None
                    and any(result.status == "error" for result in current.measurement.results)
                )
                if (
                    current.state not in {MeasurementState.FAILED, MeasurementState.PARTIAL}
                    or not failed_evaluators
                ):
                    raise Conflict("Evaluator recovery requires failed evaluator results")
                next_state = MeasurementState.QUEUED
                event_type = "evaluator-recovery-queued"
            elif job_type == JobType.AGENT_STAGE:
                stage_request = SpecialistStageRequest.model_validate(expected.request)
                if (
                    current.measurement is None
                    or current.state not in {MeasurementState.READY, MeasurementState.PARTIAL}
                ):
                    raise Conflict("Specialist stages require saved measurement results")
                next_state = current.state
                event_type = f"{stage_request.role.value}-queued"
            else:
                raise Conflict("Unsupported workflow job type")
            self._lock_admission(connection)
            self._validate_queue_capacity(connection, owner)
            job_id = identifier()
            if execution_principal_id is not None:
                if (
                    execution_authorization_id is None
                    or policy_id is None
                    or policy_hash is None
                    or operation_ceiling is None
                ):
                    raise Conflict("Agent jobs require a complete execution authorization context")
                self._consume_execution_authorization(
                    connection,
                    authorization_id=execution_authorization_id,
                    principal_id=execution_principal_id,
                    job_type=job_type,
                    run=current,
                    policy_id=policy_id,
                    policy_hash=policy_hash,
                    operation_ceiling=operation_ceiling,
                    job_id=job_id,
                )
            def queue_mutation(run: MeasurementRun) -> MeasurementRun:
                updates: dict[str, Any] = {
                    "state": next_state,
                    "events": (*run.events, MeasurementEvent(
                        sequence=len(run.events) + 1,
                        event_type=event_type,
                    )),
                }
                if job_type == JobType.AGENT_STAGE:
                    binding = (
                        AgentBindingSnapshot.model_validate(stage_request.binding)
                        if stage_request.binding is not None
                        else None
                    )
                    updates["agent_stages"] = upsert_stage(
                        run.agent_stages,
                        AgentStageRecord(
                            role=stage_request.role,
                            status=AgentStageStatus.QUEUED,
                            provider_mode=stage_request.provider_mode,
                            job_id=job_id,
                            binding=binding,
                            input_hash=digest(run.measurement.model_dump(mode="json")),
                        ),
                    )
                return run.model_copy(update=updates)

            updated_run = self._mutate_run(
                connection,
                current,
                revision,
                queue_mutation,
            )
            job = WorkflowJob.create(
                updated_run,
                job_type,
                idempotency_key,
                request,
                execution_principal_id=execution_principal_id,
                execution_authorization_id=execution_authorization_id,
                policy_id=policy_id,
                policy_hash=policy_hash,
                operation_ceiling=operation_ceiling,
            ).model_copy(update={"job_id": job_id})
            connection.execute(insert(workflow_jobs).values(
                job_id=job.job_id,
                run_id=job.run_id,
                owner_key=owner.key,
                job_type=job.job_type.value,
                idempotency_key=job.idempotency_key,
                state=job.state.value,
                lease_holder=None,
                lease_token=None,
                policy_id=job.policy_id,
                policy_hash=job.policy_hash,
                execution_principal_id=job.execution_principal_id,
                execution_authorization_id=job.execution_authorization_id,
                operation_ceiling=job.operation_ceiling,
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

    def list_run_jobs(
        self,
        run_id: str,
        owner: OwnerIdentity,
        limit: int = 50,
    ) -> tuple[WorkflowJob, ...]:
        if not 1 <= limit <= 100:
            raise Conflict("Job list limit must be between 1 and 100")
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            rows = connection.execute(
                select(workflow_jobs).where(
                    workflow_jobs.c.run_id == run_id,
                    workflow_jobs.c.owner_key == owner.key,
                )
                .order_by(workflow_jobs.c.created_at.desc(), workflow_jobs.c.job_id.desc())
                .limit(limit)
            ).all()
            return tuple(self._job_from_row(row) for row in rows)

    def list_unreconciled_specialist_jobs(
        self,
        limit: int = 100,
        *,
        policy_id: str | None = None,
        policy_hash: str | None = None,
        owner_key: str | None = None,
    ) -> tuple[WorkflowJob, ...]:
        if not 1 <= limit <= 100:
            raise Conflict("Reconciliation job limit must be between 1 and 100")
        child = workflow_jobs.alias("specialist_child")
        prior_stage = workflow_jobs.alias("prior_specialist_stage")
        conditions = [
            workflow_jobs.c.state == JobState.COMPLETED.value,
            workflow_jobs.c.job_type.in_((
                JobType.EVALUATE.value,
                JobType.RECOVER_EVALUATORS.value,
            )),
            or_(
                (
                    (workflow_jobs.c.job_type == JobType.EVALUATE.value)
                    & (
                        func.json_extract(
                            workflow_jobs.c.payload,
                            "$.request.include_recommendations",
                        )
                        == 1
                    )
                ),
                (
                    (workflow_jobs.c.job_type == JobType.RECOVER_EVALUATORS.value)
                    & select(prior_stage.c.job_id).where(
                        prior_stage.c.run_id == workflow_jobs.c.run_id,
                        prior_stage.c.owner_key == workflow_jobs.c.owner_key,
                        prior_stage.c.job_type == JobType.AGENT_STAGE.value,
                    ).exists()
                ),
            ),
            ~select(child.c.job_id).where(
                child.c.run_id == workflow_jobs.c.run_id,
                child.c.owner_key == workflow_jobs.c.owner_key,
                child.c.job_type == JobType.AGENT_STAGE.value,
                func.json_extract(
                    child.c.payload,
                    "$.request.source_job_id",
                )
                == workflow_jobs.c.job_id,
            ).exists(),
        ]
        if policy_id is not None:
            conditions.append(workflow_jobs.c.policy_id == policy_id)
        if policy_hash is not None:
            conditions.append(workflow_jobs.c.policy_hash == policy_hash)
        if owner_key is not None:
            conditions.append(workflow_jobs.c.owner_key == owner_key)
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(workflow_jobs).where(*conditions)
                .order_by(
                    workflow_jobs.c.completed_at.desc(),
                    workflow_jobs.c.job_id.desc(),
                )
                .limit(limit)
            ).all()
            return tuple(self._job_from_row(row) for row in rows)

    def get_run_progress(
        self,
        run_id: str,
        owner: OwnerIdentity,
        job_id: str | None = None,
    ) -> RunProgress:
        with self.engine.begin() as connection:
            summary_payload = connection.execute(
                select(measurement_runs.c.summary_payload).where(
                    measurement_runs.c.run_id == run_id,
                    measurement_runs.c.owner_key == owner.key,
                )
            ).scalar_one_or_none()
            if summary_payload is None:
                raise NotFound("Measurement run not found")
            run: MeasurementRun | MeasurementRunSummary = (
                MeasurementRunSummary.model_validate_json(summary_payload)
            )
            job_statement = select(workflow_jobs).where(
                    workflow_jobs.c.run_id == run_id,
                    workflow_jobs.c.owner_key == owner.key,
                )
            if job_id is not None:
                job_statement = job_statement.where(workflow_jobs.c.job_id == job_id)
            row = connection.execute(
                job_statement
                .order_by(workflow_jobs.c.created_at.desc(), workflow_jobs.c.job_id.desc())
                .limit(1)
            ).first()
            if job_id is not None and row is None:
                raise NotFound("Workflow job not found")
            job = self._job_from_row(row) if row else None
            if job is not None and (
                job.job_type == JobType.RECOVER_EVALUATORS
                or (
                    job.job_type == JobType.EVALUATE
                    and job.operation_ceiling is None
                )
            ):
                run = self._read(connection, run_id, owner)
            claims = ()
            if job is not None:
                payloads = connection.execute(
                    select(operation_claims.c.payload)
                    .where(
                        operation_claims.c.run_id == run_id,
                        operation_claims.c.job_id == job.job_id,
                    )
                    .order_by(
                        operation_claims.c.created_at,
                        operation_claims.c.claim_id,
                    )
                ).scalars()
                claims = tuple(
                    OperationClaim.model_validate_json(payload)
                    for payload in payloads
                )
            return RunProgress.from_records(run, job, claims)

    @staticmethod
    def _require_active_lease(
        job: WorkflowJob,
        worker_id: str,
        now: datetime,
        lease_token: str | None = None,
    ) -> None:
        if (
            job.state != JobState.LEASED
            or job.lease_holder != worker_id
            or (lease_token is not None and job.lease_token != lease_token)
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            raise LeaseLost("Worker does not hold an active lease for this job")

    @staticmethod
    def _write_worker_heartbeat(
        connection: Connection,
        worker_id: str,
        current_job_id: str | None,
        observed_at: datetime,
    ) -> None:
        changed = connection.execute(
            update(worker_heartbeats)
            .where(worker_heartbeats.c.worker_id == worker_id)
            .values(
                current_job_id=current_job_id,
                last_seen_at=observed_at.isoformat(),
            )
        )
        if changed.rowcount == 0:
            try:
                connection.execute(insert(worker_heartbeats).values(
                    worker_id=worker_id,
                    current_job_id=current_job_id,
                    last_seen_at=observed_at.isoformat(),
                ))
            except IntegrityError:
                connection.execute(
                    update(worker_heartbeats)
                    .where(worker_heartbeats.c.worker_id == worker_id)
                    .values(
                        current_job_id=current_job_id,
                        last_seen_at=observed_at.isoformat(),
                    )
                )

    def lease_one_job(
        self,
        worker_id: str,
        lease_seconds: int = 300,
        policy_id: str | None = None,
        policy_hash: str | None = None,
        owner_key: str | None = None,
        now: datetime | None = None,
    ) -> WorkflowJob | None:
        if not worker_id.strip() or not 1 <= lease_seconds <= 3600:
            raise Conflict("A worker ID and bounded lease duration are required")
        current_time = now or utc_now()
        with self.engine.begin() as connection:
            self._lock_admission(connection)
            active = connection.execute(
                select(func.count()).select_from(workflow_jobs).where(
                    workflow_jobs.c.state == JobState.LEASED.value
                )
            ).scalar_one()
            if active >= 1:
                self._write_worker_heartbeat(connection, worker_id, None, current_time)
                return None
            statement = select(workflow_jobs).where(
                workflow_jobs.c.state == JobState.QUEUED.value
            )
            if policy_id is not None:
                statement = statement.where(workflow_jobs.c.policy_id == policy_id)
            if policy_hash is not None:
                statement = statement.where(workflow_jobs.c.policy_hash == policy_hash)
            if owner_key is not None:
                statement = statement.where(workflow_jobs.c.owner_key == owner_key)
            row = connection.execute(
                statement
                .order_by(workflow_jobs.c.created_at, workflow_jobs.c.job_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if row is None:
                self._write_worker_heartbeat(connection, worker_id, None, current_time)
                return None
            job = self._job_from_row(row)
            run = self._read(connection, job.run_id, job.owner)
            if run.revision != job.run_revision:
                raise Conflict("Queued job is bound to a stale measurement revision")
            if job.job_type in {JobType.EVALUATE, JobType.RECOVER_EVALUATORS}:
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
            elif job.job_type == JobType.AGENT_STAGE:
                request = SpecialistStageRequest.model_validate(job.request)

                def start_stage(item: MeasurementRun) -> MeasurementRun:
                    existing = next(
                        (stage for stage in item.agent_stages if stage.role == request.role),
                        None,
                    )
                    if existing is None or existing.job_id != job.job_id:
                        raise Conflict("Queued specialist stage does not match the run")
                    return item.model_copy(update={
                        "agent_stages": upsert_stage(
                            item.agent_stages,
                            existing.model_copy(update={
                                "status": AgentStageStatus.RUNNING,
                                "updated_at": current_time,
                            }),
                        ),
                        "events": (*item.events, MeasurementEvent(
                            sequence=len(item.events) + 1,
                            event_type=f"{request.role.value}-running",
                        )),
                    })

                run = self._mutate_run(
                    connection,
                    run,
                    run.revision,
                    start_stage,
                )
            leased = job.model_copy(update={
                "run_revision": run.revision,
                "state": JobState.LEASED,
                "lease_holder": worker_id,
                "lease_token": identifier(),
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
            self._write_worker_heartbeat(connection, worker_id, leased.job_id, current_time)
            return leased

    def renew_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> WorkflowJob:
        if not lease_token or not 1 <= lease_seconds <= 3600:
            raise Conflict("A lease token and bounded lease duration are required")
        current_time = now or utc_now()
        with self.engine.begin() as connection:
            row = connection.execute(
                select(workflow_jobs).where(workflow_jobs.c.job_id == job_id)
            ).first()
            if row is None:
                raise NotFound("Workflow job not found")
            job = self._job_from_row(row)
            self._require_active_lease(job, worker_id, current_time, lease_token)
            renewed = job.model_copy(update={
                "lease_expires_at": current_time + timedelta(seconds=lease_seconds),
                "updated_at": current_time,
            })
            changed = connection.execute(
                update(workflow_jobs)
                .where(
                    workflow_jobs.c.job_id == job_id,
                    workflow_jobs.c.state == JobState.LEASED.value,
                    workflow_jobs.c.lease_holder == worker_id,
                    workflow_jobs.c.lease_token == lease_token,
                )
                .values(
                    lease_expires_at=renewed.lease_expires_at.isoformat(),
                    payload=renewed.model_dump_json(),
                    updated_at=renewed.updated_at.isoformat(),
                )
            )
            if changed.rowcount != 1:
                raise LeaseLost("Worker lost the job lease")
            self._write_worker_heartbeat(connection, worker_id, job_id, current_time)
            return renewed

    def claim_operation(
        self,
        job_id: str,
        worker_id: str,
        operation_key: str,
        operation_type: str,
        lease_token: str | None = None,
        now: datetime | None = None,
    ) -> OperationClaim:
        current_time = now or utc_now()
        with self.engine.begin() as connection:
            job_row = connection.execute(select(workflow_jobs).where(workflow_jobs.c.job_id == job_id)).first()
            if job_row is None:
                raise NotFound("Workflow job not found")
            job = self._job_from_row(job_row)
            self._require_active_lease(job, worker_id, current_time, lease_token)
            existing_claim = connection.execute(
                select(operation_claims.c.claim_id).where(
                    operation_claims.c.operation_key == operation_key
                )
            ).first()
            if existing_claim is not None:
                raise Conflict("Operation was already claimed and will not be retried")
            if job.operation_ceiling is not None:
                attempted = connection.execute(
                    select(func.count()).select_from(operation_claims).where(
                        operation_claims.c.job_id == job.job_id
                    )
                ).scalar_one()
                if attempted >= job.operation_ceiling:
                    raise Conflict("Agent execution authorization operation ceiling is exhausted")
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
                    payload=claim.model_dump_json(exclude={"output"}),
                    checkpoint_payload=None,
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
        binding = (grant.policy_id, grant.policy_hash, grant.owner.key)
        if self._measurement_budget_binding not in {None, binding}:
            raise Conflict("Repository is already bound to a different measurement policy or owner")
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

    def bind_measurement_budgets(
        self,
        grants: tuple[MeasurementBudgetGrant, ...],
        policy: MeasurementExecutionPolicy,
    ) -> None:
        if not grants:
            raise Conflict("At least one measurement budget grant is required")
        for grant in grants:
            self.bind_measurement_budget(grant, policy)

    def set_measurement_budget_enforcement(self, enforced: bool) -> None:
        self._measurement_budget_enforced = enforced

    def measurement_capacity(
        self,
        owner: OwnerIdentity,
        policy: MeasurementExecutionPolicy,
    ) -> MeasurementCapacity:
        totals = {
            operation_type: {"allowance": 0, "consumed": 0}
            for operation_type, _ in MeasurementOperationAllowances().items()
        }
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    measurement_budget_usage.c.operation_type,
                    measurement_budget_usage.c.allowance,
                    measurement_budget_usage.c.consumed,
                )
                .select_from(
                    measurement_budget_usage.join(
                        measurement_budget_grants,
                        measurement_budget_usage.c.grant_id
                        == measurement_budget_grants.c.grant_id,
                    )
                )
                .where(
                    measurement_budget_grants.c.policy_id == policy.policy_id,
                    measurement_budget_grants.c.policy_hash == policy.policy_hash,
                    measurement_budget_grants.c.owner_key == owner.key,
                )
            ).all()
        for operation_type, allowance, consumed in rows:
            if operation_type in totals:
                totals[operation_type]["allowance"] += allowance
                totals[operation_type]["consumed"] += consumed
        operations = {
            operation_type: MeasurementOperationCapacity(
                allowance=values["allowance"],
                consumed=values["consumed"],
                remaining=max(0, values["allowance"] - values["consumed"]),
            )
            for operation_type, values in totals.items()
        }
        runs = operations["webiq-browse"]
        return MeasurementCapacity(
            authorized_runs=runs.allowance,
            consumed_runs=runs.consumed,
            remaining_runs=runs.remaining,
            exhausted=runs.remaining == 0,
            operations=operations,
        )

    def _consume_measurement_budget(
        self,
        connection: Connection,
        job: WorkflowJob,
        operation_type: str,
    ) -> str | None:
        if self._measurement_budget_binding is None or not self._measurement_budget_enforced:
            return None
        policy_id, policy_hash, owner_key = self._measurement_budget_binding
        if owner_key != job.owner.key:
            raise Conflict("Measurement budget does not authorise this job")
        candidates = connection.execute(
            select(measurement_budget_usage.c.grant_id)
            .select_from(
                measurement_budget_usage.join(
                    measurement_budget_grants,
                    measurement_budget_usage.c.grant_id == measurement_budget_grants.c.grant_id,
                )
            )
            .where(
                measurement_budget_grants.c.policy_id == policy_id,
                measurement_budget_grants.c.policy_hash == policy_hash,
                measurement_budget_grants.c.owner_key == owner_key,
                measurement_budget_usage.c.operation_type == operation_type,
                measurement_budget_usage.c.consumed < measurement_budget_usage.c.allowance,
            )
            .order_by(
                measurement_budget_grants.c.approved_at,
                measurement_budget_grants.c.grant_id,
            )
        ).scalars().all()
        for grant_id in candidates:
            consumed = connection.execute(
                update(measurement_budget_usage)
                .where(
                    measurement_budget_usage.c.grant_id == grant_id,
                    measurement_budget_usage.c.operation_type == operation_type,
                    measurement_budget_usage.c.consumed < measurement_budget_usage.c.allowance,
                )
                .values(consumed=measurement_budget_usage.c.consumed + 1)
            )
            if consumed.rowcount == 1:
                return grant_id
        raise Conflict("Measurement operation allowance is exhausted or not authorised")

    @staticmethod
    def _require_live_project_run(run: MeasurementRun) -> None:
        try:
            if run.inputs is not None:
                run.inputs.validate_live()
            if run.measurement is not None:
                run.measurement.validate_live()
            if run.recommendations is not None:
                if run.measurement is None:
                    raise ValueError("Project recommendations require a saved live measurement")
                run.measurement.validate_live()
        except ValueError as error:
            raise Conflict(str(error)) from None

    @staticmethod
    def _claim_from_row(row: Any, *, include_output: bool = False) -> OperationClaim:
        claim = OperationClaim.model_validate_json(row.payload)
        if include_output and row.checkpoint_payload is not None:
            return claim.model_copy(update={"output": json.loads(row.checkpoint_payload)})
        return claim

    @staticmethod
    def _checkpoint_value(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {
                str(key): SQLAlchemyMeasurementRepository._checkpoint_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                SQLAlchemyMeasurementRepository._checkpoint_value(item)
                for item in value
            ]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise Conflict("Operation output cannot be checkpointed safely")

    def record_operation(
        self,
        claim_id: str,
        worker_id: str,
        state: OperationClaimState,
        provider_metadata: dict[str, str | int | bool | None] | None = None,
        error_code: str | None = None,
        output: Any = None,
        lease_token: str | None = None,
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
            self._require_active_lease(
                self._job_from_row(job_row),
                worker_id,
                current_time,
                lease_token,
            )
            if claim.state != OperationClaimState.CLAIMED:
                raise Conflict("Operation claim already has an outcome")
            updated = claim.model_copy(update={
                "state": state,
                "provider_metadata": provider_metadata or {},
                "error_code": error_code,
                "output": self._checkpoint_value(output) if state == OperationClaimState.COMPLETED else None,
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
                    payload=updated.model_dump_json(exclude={"output"}),
                    checkpoint_payload=(
                        json.dumps(updated.output, sort_keys=True, separators=(",", ":"))
                        if updated.output is not None
                        else None
                    ),
                    updated_at=updated.updated_at.isoformat(),
                )
            )
            return updated

    def list_operation_checkpoints(
        self,
        run_id: str,
        owner: OwnerIdentity,
        job_id: str | None = None,
    ) -> tuple[OperationClaim, ...]:
        with self.engine.connect() as connection:
            self._read(connection, run_id, owner)
            statement = select(operation_claims).where(
                operation_claims.c.run_id == run_id,
                operation_claims.c.state == OperationClaimState.COMPLETED.value,
            )
            if job_id is not None:
                statement = statement.where(operation_claims.c.job_id == job_id)
            rows = connection.execute(
                statement.order_by(
                    operation_claims.c.created_at,
                    operation_claims.c.claim_id,
                )
            ).all()
            return tuple(self._claim_from_row(row, include_output=True) for row in rows)

    def _leased_job(
        self,
        connection: Connection,
        job_id: str,
        worker_id: str,
        lease_token: str | None = None,
    ) -> WorkflowJob:
        row = connection.execute(select(workflow_jobs).where(workflow_jobs.c.job_id == job_id)).first()
        if row is None:
            raise NotFound("Workflow job not found")
        job = self._job_from_row(row)
        self._require_active_lease(job, worker_id, utc_now(), lease_token)
        return job

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        operation: Mutation,
        lease_token: str | None = None,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        with self.engine.begin() as connection:
            job = self._leased_job(connection, job_id, worker_id, lease_token)
            run = self._read(connection, job.run_id, job.owner)
            updated_run = self._mutate_run(connection, run, job.run_revision, operation)
            allowed_states = {
                JobType.PREPARE: {MeasurementState.AWAITING_APPROVAL},
                JobType.EVALUATE: {
                    MeasurementState.READY,
                    MeasurementState.PARTIAL,
                    MeasurementState.FAILED,
                },
                JobType.RECOVER_EVALUATORS: {
                    MeasurementState.READY,
                    MeasurementState.PARTIAL,
                    MeasurementState.FAILED,
                },
                JobType.AGENT_STAGE: {
                    MeasurementState.READY,
                    MeasurementState.PARTIAL,
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
                        definition=BrandDefinition.model_validate(
                            analysis.brand.definition.model_dump(mode="json")
                        ),
                        source="page-analysis",
                    ))
            now = utc_now()
            completed = job.model_copy(update={
                "run_revision": updated_run.revision,
                "state": JobState.COMPLETED,
                "lease_holder": None,
                "lease_token": None,
                "lease_expires_at": None,
                "completed_at": now,
                "updated_at": now,
            })
            self._write_job(connection, completed)
            self._write_worker_heartbeat(connection, worker_id, None, now)
            return completed, updated_run

    def fail_job(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        lease_token: str | None = None,
    ) -> tuple[WorkflowJob, MeasurementRun]:
        with self.engine.begin() as connection:
            job = self._leased_job(connection, job_id, worker_id, lease_token)
            run = self._read(connection, job.run_id, job.owner)
            now = utc_now()
            failed = job.model_copy(update={
                "state": JobState.FAILED,
                "lease_holder": None,
                "lease_token": None,
                "lease_expires_at": None,
                "error_code": error_code,
                "completed_at": now,
                "updated_at": now,
            })
            def failure_mutation(item: MeasurementRun) -> MeasurementRun:
                if job.job_type != JobType.AGENT_STAGE:
                    return item.model_copy(update={
                        "state": MeasurementState.NEEDS_REVIEW,
                        "events": (*item.events, MeasurementEvent(
                            sequence=len(item.events) + 1,
                            event_type="job-failed",
                        )),
                    })
                request = SpecialistStageRequest.model_validate(job.request)
                existing = next(
                    (stage for stage in item.agent_stages if stage.role == request.role),
                    None,
                )
                stage = AgentStageRecord(
                    role=request.role,
                    status=AgentStageStatus.FAILED,
                    provider_mode=request.provider_mode,
                    job_id=job.job_id,
                    binding=existing.binding if existing is not None else None,
                    input_hash=(
                        existing.input_hash if existing is not None
                        else digest(item.measurement.model_dump(mode="json"))
                    ),
                    error_code=error_code,
                    created_at=existing.created_at if existing is not None else now,
                    updated_at=now,
                )
                return item.model_copy(update={
                    "agent_stages": upsert_stage(item.agent_stages, stage),
                    "events": (*item.events, MeasurementEvent(
                        sequence=len(item.events) + 1,
                        event_type=f"{request.role.value}-failed",
                    )),
                })

            updated_run = self._mutate_run(
                connection,
                run,
                run.revision,
                failure_mutation,
            )
            self._write_job(connection, failed)
            self._write_worker_heartbeat(connection, worker_id, None, now)
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
                "lease_token": None,
                "lease_expires_at": None,
                "completed_at": now,
                "updated_at": now,
            })
            def cancellation_mutation(item: MeasurementRun) -> MeasurementRun:
                if job.job_type != JobType.AGENT_STAGE:
                    return item.model_copy(update={
                        "state": MeasurementState.CANCELLED,
                        "events": (*item.events, MeasurementEvent(
                            sequence=len(item.events) + 1,
                            event_type="cancelled",
                        )),
                    })
                request = SpecialistStageRequest.model_validate(job.request)
                existing = next(
                    (stage for stage in item.agent_stages if stage.role == request.role),
                    None,
                )
                if existing is None:
                    raise Conflict("Cancelled specialist stage does not match the run")
                return item.model_copy(update={
                    "agent_stages": upsert_stage(
                        item.agent_stages,
                        existing.model_copy(update={
                            "status": AgentStageStatus.FAILED,
                            "error_code": "cancelled",
                            "updated_at": now,
                        }),
                    ),
                    "events": (*item.events, MeasurementEvent(
                        sequence=len(item.events) + 1,
                        event_type=f"{request.role.value}-cancelled",
                    )),
                })

            updated_run = self._mutate_run(
                connection,
                run,
                run.revision,
                cancellation_mutation,
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
                    "lease_token": None,
                    "lease_expires_at": None,
                    "error_code": "lease-expired",
                    "completed_at": current_time,
                    "updated_at": current_time,
                })
                changed = connection.execute(
                    update(workflow_jobs)
                    .where(
                        workflow_jobs.c.job_id == job.job_id,
                        workflow_jobs.c.state == JobState.LEASED.value,
                        workflow_jobs.c.lease_token == job.lease_token,
                        workflow_jobs.c.lease_expires_at == row.lease_expires_at,
                        workflow_jobs.c.lease_expires_at <= current_time.isoformat(),
                    )
                    .values(
                        state=failed.state.value,
                        lease_holder=None,
                        lease_token=None,
                        lease_expires_at=None,
                        completed_at=current_time.isoformat(),
                        error_code=failed.error_code,
                        payload=failed.model_dump_json(),
                        updated_at=current_time.isoformat(),
                    )
                )
                if changed.rowcount != 1:
                    continue
                def interruption_mutation(item: MeasurementRun) -> MeasurementRun:
                    if job.job_type != JobType.AGENT_STAGE:
                        return item.model_copy(update={
                            "state": MeasurementState.NEEDS_REVIEW,
                            "events": (*item.events, MeasurementEvent(
                                sequence=len(item.events) + 1,
                                event_type="job-interrupted",
                            )),
                        })
                    request = SpecialistStageRequest.model_validate(job.request)
                    existing = next(
                        (stage for stage in item.agent_stages if stage.role == request.role),
                        None,
                    )
                    if existing is None:
                        raise Conflict("Interrupted specialist stage does not match the run")
                    return item.model_copy(update={
                        "agent_stages": upsert_stage(
                            item.agent_stages,
                            existing.model_copy(update={
                                "status": AgentStageStatus.FAILED,
                                "error_code": "lease-expired",
                                "updated_at": current_time,
                            }),
                        ),
                        "events": (*item.events, MeasurementEvent(
                            sequence=len(item.events) + 1,
                            event_type=f"{request.role.value}-interrupted",
                        )),
                    })

                self._mutate_run(
                    connection,
                    run,
                    run.revision,
                    interruption_mutation,
                )
                if job.lease_holder is not None:
                    self._write_worker_heartbeat(
                        connection,
                        job.lease_holder,
                        None,
                        current_time,
                    )
                recovered.append(failed)
        return tuple(recovered)

    def worker_health(self) -> dict[str, str | None]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(worker_heartbeats)
                .order_by(worker_heartbeats.c.last_seen_at.desc())
                .limit(1)
            ).first()
            if row is None:
                return {
                    "status": "unknown",
                    "worker_id": None,
                    "current_job_id": None,
                    "last_seen_at": None,
                }
            return {
                "status": "observed",
                "worker_id": row.worker_id,
                "current_job_id": row.current_job_id,
                "last_seen_at": row.last_seen_at,
            }

    def queue_status(self, owner: OwnerIdentity) -> dict[str, int]:
        with self.engine.connect() as connection:
            queued_global = connection.execute(
                select(func.count()).select_from(workflow_jobs).where(
                    workflow_jobs.c.state == JobState.QUEUED.value
                )
            ).scalar_one()
            queued_owner = connection.execute(
                select(func.count()).select_from(workflow_jobs).where(
                    workflow_jobs.c.state == JobState.QUEUED.value,
                    workflow_jobs.c.owner_key == owner.key,
                )
            ).scalar_one()
            active_global = connection.execute(
                select(func.count()).select_from(workflow_jobs).where(
                    workflow_jobs.c.state == JobState.LEASED.value
                )
            ).scalar_one()
            return {
                "queued_global": queued_global,
                "queued_for_owner": queued_owner,
                "active_global": active_global,
            }

    def close(self) -> None:
        self.engine.dispose()


class SQLiteMeasurementRepository(SQLAlchemyMeasurementRepository):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._upgrade_legacy_schema(path)
        super().__init__(f"sqlite:///{path.resolve().as_posix()}", initialize_schema=True)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one_or_none()
            if version is None:
                connection.exec_driver_sql(
                    "INSERT INTO alembic_version (version_num) VALUES (?)",
                    (LATEST_SCHEMA_REVISION,),
                )

    @staticmethod
    def _upgrade_legacy_schema(path: Path) -> None:
        if not path.exists():
            return
        with sqlite3.connect(path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "measurement_runs" not in tables:
                return
            column_rows = {
                table: list(connection.execute(f'PRAGMA table_info("{table}")'))
                for table in tables
            }
            columns = {
                table: {row[1] for row in rows}
                for table, rows in column_rows.items()
            }
            indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
            required_columns = {
                "measurement_runs": {
                    "state",
                    "created_at",
                    "updated_at",
                    "summary_payload",
                },
                "workflow_jobs": {
                    "lease_token",
                    "policy_id",
                    "policy_hash",
                    "execution_principal_id",
                    "execution_authorization_id",
                    "operation_ceiling",
                },
                "operation_claims": {"checkpoint_payload"},
                "artifacts": {"kind"},
            }
            required_tables = {
                "workflow_admission_lock",
                "run_create_requests",
                "artifact_create_requests",
                "agent_execution_authorizations",
                "worker_heartbeats",
            }
            required_indexes = {
                "ix_measurement_runs_owner_updated",
                "ix_workflow_jobs_dispatch",
                "ix_artifacts_run_kind",
                "ix_agent_authorizations_principal_run",
            }
            mcp_only_indexes = required_indexes - {
                "ix_measurement_runs_owner_updated",
            }
            v4_complete = (
                required_tables <= tables
                and required_indexes <= indexes
                and all(
                    required <= columns.get(table, set())
                    for table, required in required_columns.items()
                )
            )
            artifact_reservation_nullable = any(
                row[1] == "artifact_id" and row[3] == 0
                for row in column_rows.get("artifact_create_requests", [])
            )
            complete = v4_complete and artifact_reservation_nullable
            markers_present = (
                bool(required_tables.intersection(tables))
                or bool(mcp_only_indexes.intersection(indexes))
                or any(
                    bool(required.intersection(columns.get(table, set())))
                    for table, required in required_columns.items()
                )
            )
            old_run_columns = {"run_id", "owner_key", "revision", "payload"}
            version = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] if "alembic_version" in tables else None
            stamp_required = version is None
            if version == LATEST_SCHEMA_REVISION:
                if not complete:
                    raise Conflict(
                        "The MCP schema is incomplete despite its latest migration marker"
                    )
                return
            if complete and version is None:
                return
            if version == "0004_mcp_execution_foundation":
                if not v4_complete:
                    raise Conflict(
                        "The MCP schema is partially migrated; restore a backup or complete a controlled repair"
                    )
            elif version is None and v4_complete:
                version = "0004_mcp_execution_foundation"
            if markers_present:
                if version != "0004_mcp_execution_foundation":
                    raise Conflict(
                        "The MCP schema is partially migrated; restore a backup or complete a controlled repair"
                    )
            elif columns.get("measurement_runs") != old_run_columns:
                raise Conflict(
                    "Existing measurement schema is not a recognized migration source"
                )
            if version is None:
                version = (
                    "0003_brand_definitions"
                    if "run_brand_definitions" in tables
                    else "0002_measurement_budget"
                    if "measurement_budget_grants" in tables
                    else "0001_shared_v2_schema"
                )
        from alembic import command
        from alembic.config import Config

        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{path.resolve().as_posix()}")
        if stamp_required:
            command.stamp(config, version)
        command.upgrade(config, LATEST_SCHEMA_REVISION)