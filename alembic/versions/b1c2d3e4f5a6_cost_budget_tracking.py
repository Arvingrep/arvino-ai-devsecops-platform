"""cost_budget_tracking

Add cost_usd to steps and cost_usd_total to workflow_runs for Phase 6 budget tracking.

Revision ID: b1c2d3e4f5a6
Revises: a4387efe1c44
Create Date: 2026-10-15 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a4387efe1c44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cost tracking columns."""
    # Per-step cost (nullable; only set for agent steps that report a cost).
    op.add_column("steps", sa.Column("cost_usd", sa.Float(), nullable=True))
    # Cumulative cost for the whole run; 0.0 when no cost has been recorded.
    op.add_column(
        "workflow_runs",
        sa.Column("cost_usd_total", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Remove cost tracking columns."""
    op.drop_column("workflow_runs", "cost_usd_total")
    op.drop_column("steps", "cost_usd")
