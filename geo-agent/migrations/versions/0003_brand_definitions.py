from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_brand_definitions"
down_revision: str | None = "0002_measurement_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_brand_definitions",
        sa.Column("run_id", sa.String(64), sa.ForeignKey("measurement_runs.run_id"), primary_key=True),
        sa.Column("definition_version", sa.Integer(), primary_key=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("run_brand_definitions")