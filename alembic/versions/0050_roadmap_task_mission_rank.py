"""roadmap task mission rank

Revision ID: 0050_roadmap_task_mission_rank
Revises: 0049_kpi_snapshots
Create Date: 2026-10-18

Today's Mission §04.2 "Upcoming (editable order)": a founder can reorder the next week's
projected tasks. The chosen order is stored as a nullable `mission_rank` on `roadmap_tasks`,
which the mission generator sorts by first (NULLs keep the default milestone/due-date order).
A nullable column with no default: metadata-only on Postgres, no table rewrite.
"""

import sqlalchemy as sa

from alembic import op

revision = "0050_roadmap_task_mission_rank"
down_revision = "0049_kpi_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("roadmap_tasks", sa.Column("mission_rank", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("roadmap_tasks", "mission_rank")
