"""Add immutable measurement budget grants and operation consumption."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_measurement_budget"
down_revision: str | None = "0001_shared_v2_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "measurement_budget_grants",
        sa.Column("grant_id", sa.String(length=100), primary_key=True),
        sa.Column("policy_id", sa.String(length=100), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_key", sa.String(length=64), nullable=False),
        sa.Column("grant_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.String(length=40), nullable=False),
    )
    op.create_index(
        "ix_measurement_budget_grants_policy",
        "measurement_budget_grants",
        ["policy_id", "policy_hash"],
    )
    op.create_table(
        "measurement_budget_usage",
        sa.Column(
            "grant_id",
            sa.String(length=100),
            sa.ForeignKey("measurement_budget_grants.grant_id"),
            primary_key=True,
        ),
        sa.Column("operation_type", sa.String(length=40), primary_key=True),
        sa.Column("allowance", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Integer(), nullable=False),
    )
    op.create_table(
        "measurement_budget_consumptions",
        sa.Column(
            "claim_id",
            sa.String(length=64),
            sa.ForeignKey("operation_claims.claim_id"),
            primary_key=True,
        ),
        sa.Column(
            "grant_id",
            sa.String(length=100),
            sa.ForeignKey("measurement_budget_grants.grant_id"),
            nullable=False,
        ),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index(
        "ix_measurement_budget_consumptions_grant",
        "measurement_budget_consumptions",
        ["grant_id", "operation_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_measurement_budget_consumptions_grant",
        table_name="measurement_budget_consumptions",
    )
    op.drop_table("measurement_budget_consumptions")
    op.drop_table("measurement_budget_usage")
    op.drop_index("ix_measurement_budget_grants_policy", table_name="measurement_budget_grants")
    op.drop_table("measurement_budget_grants")
