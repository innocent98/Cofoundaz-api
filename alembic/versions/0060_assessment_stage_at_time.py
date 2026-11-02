"""Assessment stage snapshot: assessments.stage_at_time

Revision ID: 0060_assessment_stage_at_time
Revises: 0058_mvp_feedback
Create Date: 2026-10-20

Assessment PRD (Module 07), D-ASM-3: freeze the startup's stage when an assessment is completed.
Nullable, no back-fill: older rows read as "not recorded".

NOTE FOR WHOEVER MERGES SECOND: 0059 is reserved for the Legal work, which is not merged yet, so
this revision chains from 0058 on purpose. When 0059 lands first, re-point `down_revision` below
to the 0059 revision id (single head is enforced by CI: `alembic heads` must show exactly one).
"""

import sqlalchemy as sa

from alembic import op

revision = "0060_assessment_stage_at_time"
down_revision = "0058_mvp_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessments", sa.Column("stage_at_time", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("assessments", "stage_at_time")
