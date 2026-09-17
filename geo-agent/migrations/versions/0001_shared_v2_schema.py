"""Create shared GEO v2 persistence tables."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_shared_v2_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "measurement_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("owner_key", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index("ix_measurement_runs_owner_updated", "measurement_runs", ["owner_key", "run_id"])
    op.create_table(
        "run_events",
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("measurement_runs.run_id"), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_table(
        "approvals",
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("measurement_runs.run_id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_tenant_id", sa.String(length=200), nullable=False),
        sa.Column("actor_object_id", sa.String(length=200), nullable=False),
        sa.Column("approved_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "workflow_jobs",
        sa.Column("job_id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("measurement_runs.run_id"), nullable=False),
        sa.Column("owner_key", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("lease_holder", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.String(length=40), nullable=True),
        sa.Column("completed_at", sa.String(length=40), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("owner_key", "idempotency_key", name="uq_workflow_jobs_owner_idempotency"),
    )
    op.create_index("ix_workflow_jobs_state_created", "workflow_jobs", ["state", "created_at"])
    op.create_table(
        "operation_claims",
        sa.Column("claim_id", sa.String(length=64), primary_key=True),
        sa.Column("job_id", sa.String(length=64), sa.ForeignKey("workflow_jobs.job_id"), nullable=False),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("measurement_runs.run_id"), nullable=False),
        sa.Column("operation_key", sa.String(length=200), nullable=False, unique=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("measurement_runs.run_id"), nullable=False),
        sa.Column("owner_key", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("run_id", "content_hash", name="uq_artifacts_run_hash"),
    )
    op.create_table(
        "agent_conversations",
        sa.Column("conversation_id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("measurement_runs.run_id"), nullable=False),
        sa.Column("owner_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "agent_capabilities",
        sa.Column("capability_hash", sa.String(length=64), primary_key=True),
        sa.Column("conversation_id", sa.String(length=64), sa.ForeignKey("agent_conversations.conversation_id"), nullable=False),
        sa.Column("owner_key", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_agent_capabilities_expiry", "agent_capabilities", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_capabilities_expiry", table_name="agent_capabilities")
    op.drop_table("agent_capabilities")
    op.drop_table("agent_conversations")
    op.drop_table("artifacts")
    op.drop_table("operation_claims")
    op.drop_index("ix_workflow_jobs_state_created", table_name="workflow_jobs")
    op.drop_table("workflow_jobs")
    op.drop_table("approvals")
    op.drop_table("run_events")
    op.drop_index("ix_measurement_runs_owner_updated", table_name="measurement_runs")
    op.drop_table("measurement_runs")