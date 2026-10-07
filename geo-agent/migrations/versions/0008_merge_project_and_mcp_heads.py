"""Merge the project and MCP migration branches."""

from collections.abc import Sequence


revision: str = "0008_merge_project_and_mcp_heads"
down_revision: tuple[str, str] = (
    "0005_nullable_export_reservations",
    "0007_project_llm_operations",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
