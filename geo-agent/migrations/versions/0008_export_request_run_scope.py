"""Scope export reservations to their measurement run."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_export_request_run_scope"
down_revision: str | Sequence[str] | None = "0005_nullable_export_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifact_create_requests") as batch:
        batch.add_column(sa.Column(
            "run_id",
            sa.String(64),
            nullable=True,
        ))
        batch.create_foreign_key(
            "fk_artifact_create_requests_run_id",
            "measurement_runs",
            ["run_id"],
            ["run_id"],
        )
    op.execute(
        "UPDATE artifact_create_requests "
        "SET run_id = ("
        "SELECT artifacts.run_id FROM artifacts "
        "WHERE artifacts.artifact_id = artifact_create_requests.artifact_id"
        ") "
        "WHERE artifact_id IS NOT NULL"
    )
    op.create_index(
        "ix_artifact_create_requests_run",
        "artifact_create_requests",
        ["run_id", "owner_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_create_requests_run",
        table_name="artifact_create_requests",
    )
    with op.batch_alter_table("artifact_create_requests") as batch:
        batch.drop_column("run_id")
