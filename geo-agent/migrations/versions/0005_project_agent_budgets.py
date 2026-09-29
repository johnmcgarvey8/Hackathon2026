"""Persist hosted project chat usage independently of legacy allowances."""

from alembic import op
import sqlalchemy as sa


revision = "0005_project_agent_budgets"
down_revision = "0004_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_agent_budgets",
        sa.Column("owner_key", sa.String(64), primary_key=True),
        sa.Column("request_limit", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("project_agent_budgets")
