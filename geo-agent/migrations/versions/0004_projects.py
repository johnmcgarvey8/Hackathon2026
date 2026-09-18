"""Add durable brand projects and project-run ownership."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_projects"
down_revision: str | None = "0003_brand_definitions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(64), primary_key=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_projects_owner_updated", "projects", ["owner_key", "updated_at"])
    op.create_table(
        "project_runs",
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.project_id"), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("measurement_runs.run_id"), primary_key=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_project_runs_run"),
    )
    op.create_index("ix_project_runs_project", "project_runs", ["project_id", "created_at"])
    op.create_table(
        "project_conversations",
        sa.Column("conversation_id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("measurement_runs.run_id"), nullable=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_project_conversations_project_updated",
        "project_conversations",
        ["project_id", "owner_key", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_conversations_project_updated", table_name="project_conversations")
    op.drop_table("project_conversations")
    op.drop_index("ix_project_runs_project", table_name="project_runs")
    op.drop_table("project_runs")
    op.drop_index("ix_projects_owner_updated", table_name="projects")
    op.drop_table("projects")
