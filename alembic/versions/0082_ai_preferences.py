"""AI preferences: proactive suggestions, daily briefing, tone (per member per workspace)

Revision ID: 0082_ai_preferences
Revises: 0081_notifications_gaps
Create Date: 2026-10-30

Downgrade drops the table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0082_ai_preferences"
down_revision = "0081_notifications_gaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proactive_suggestions", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("daily_briefing", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("tone", sa.String(20), server_default="encouraging", nullable=False),
        sa.UniqueConstraint("user_id", "startup_id", name="uq_ai_preferences_user_startup"),
    )
    op.create_index("ix_ai_preferences_startup_id", "ai_preferences", ["startup_id"])


def downgrade() -> None:
    op.drop_table("ai_preferences")
