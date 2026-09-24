"""Link project conversations to multiple measurement runs."""

from alembic import op
import sqlalchemy as sa


revision = "0006_project_conversation_runs"
down_revision = "0005_project_agent_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_conversation_runs",
        sa.Column(
            "conversation_id",
            sa.String(64),
            sa.ForeignKey("project_conversations.conversation_id"),
            primary_key=True,
        ),
        sa.Column(
            "run_id",
            sa.String(64),
            sa.ForeignKey("measurement_runs.run_id"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
        ),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("linked_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_project_conversation_runs_project",
        "project_conversation_runs",
        ["project_id", "owner_key", "linked_at"],
    )
    op.execute(
        """
        INSERT INTO project_conversation_runs
            (conversation_id, run_id, project_id, owner_key, linked_at)
        SELECT conversation_id, run_id, project_id, owner_key, updated_at
        FROM project_conversations
        WHERE run_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_conversation_runs_project",
        table_name="project_conversation_runs",
    )
    op.drop_table("project_conversation_runs")
