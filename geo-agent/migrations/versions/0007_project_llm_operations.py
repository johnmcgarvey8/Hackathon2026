"""Persist idempotent project LLM operations."""

from alembic import op
import sqlalchemy as sa


revision = "0007_project_llm_operations"
down_revision = "0006_project_conversation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_llm_operations",
        sa.Column("operation_id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
        ),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("operation_type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("provider_response_id", sa.String(500)),
        sa.Column("fallback_used", sa.Integer(), nullable=False),
        sa.Column("usage_payload", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.String(40)),
        sa.UniqueConstraint(
            "owner_key",
            "operation_type",
            "idempotency_key",
            name="uq_project_llm_operations_owner_type_key",
        ),
    )
    op.create_index(
        "ix_project_llm_operations_project_created",
        "project_llm_operations",
        ["project_id", "owner_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_llm_operations_project_created",
        table_name="project_llm_operations",
    )
    op.drop_table("project_llm_operations")
