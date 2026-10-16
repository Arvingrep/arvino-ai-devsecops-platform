"""remove_plane_project_id

Remove the plane_project_id column and unique constraint from the projects table.
Plane integration has been decommissioned (see CHANGELOG_FRAMEWORK.md).

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-10-17 03:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the plane_project_id column and its unique constraint."""
    # SQLite requires batch mode when the table has foreign-key references.
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("plane_project_id")


def downgrade() -> None:
    """Re-add the plane_project_id column (UNIQUE, nullable)."""
    import sqlalchemy as sa

    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column("plane_project_id", sa.String(length=64), nullable=True),
        )
        batch_op.create_unique_constraint(
            "uq_projects_plane_project_id", ["plane_project_id"]
        )