"""Allow export idempotency keys to be reserved before artifact generation."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_nullable_export_reservations"
down_revision: str | None = "0004_mcp_execution_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifact_create_requests") as batch:
        batch.alter_column(
            "artifact_id",
            existing_type=sa.String(64),
            nullable=True,
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM artifact_create_requests WHERE artifact_id IS NULL"
    )
    with op.batch_alter_table("artifact_create_requests") as batch:
        batch.alter_column(
            "artifact_id",
            existing_type=sa.String(64),
            nullable=False,
        )
