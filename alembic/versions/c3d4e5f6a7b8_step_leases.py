"""step_leases

Add external-executor lease fields to steps and the repo_write_locks table.

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-10-21 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import arvino.db

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add opt-in lease columns and the repository write-lock table."""
    op.add_column("steps", sa.Column("executor_principal", sa.String(length=200), nullable=True))
    op.add_column(
        "steps",
        sa.Column(
            "executor_lease_expires_at",
            arvino.db.UTCDateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "steps", sa.Column("executor_lease_token_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "steps",
        sa.Column(
            "executor_claimed_at",
            arvino.db.UTCDateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column("steps", sa.Column("executor_attempt", sa.Integer(), nullable=True))

    op.create_table(
        "repo_write_locks",
        sa.Column("repo_key", sa.String(length=200), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("step_key", sa.String(length=64), nullable=False),
        sa.Column("executor_principal", sa.String(length=200), nullable=False),
        sa.Column("executor_lease_token_hash", sa.String(length=64), nullable=False),
        sa.Column("executor_attempt", sa.Integer(), nullable=False),
        sa.Column("expires_at", arvino.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("acquired_at", arvino.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("repo_key"),
    )
    op.create_index(
        op.f("ix_repo_write_locks_run_id"), "repo_write_locks", ["run_id"], unique=False
    )


def downgrade() -> None:
    """Remove the repository write-lock table and lease columns."""
    op.drop_index(op.f("ix_repo_write_locks_run_id"), table_name="repo_write_locks")
    op.drop_table("repo_write_locks")
    op.drop_column("steps", "executor_attempt")
    op.drop_column("steps", "executor_claimed_at")
    op.drop_column("steps", "executor_lease_token_hash")
    op.drop_column("steps", "executor_lease_expires_at")
    op.drop_column("steps", "executor_principal")
