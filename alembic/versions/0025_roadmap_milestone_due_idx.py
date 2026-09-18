"""index roadmap_milestones.due_on for the overdue scheduler detector

Revision ID: 0025_roadmap_milestone_due_idx
Revises: 0024_scheduled_runs
Create Date: 2026-09-18 23:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0025_roadmap_milestone_due_idx'
down_revision = '0024_scheduled_runs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        op.f('ix_roadmap_milestones_due_on'), 'roadmap_milestones', ['due_on'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_roadmap_milestones_due_on'), table_name='roadmap_milestones')
