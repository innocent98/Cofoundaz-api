"""AI Co-Founder slice 1: conversation topic + archive

Revision ID: 0097_ai_conversation_archive
Revises: 0095_funding_data_room
Create Date: 2026-11-05

Adds `ai_conversations.topic` (nullable, indexed; filled when a reply is routed) and
`archived_at` (nullable; archived conversations leave the default list and are not searchable).
Downgrade drops both.

CHAIN NOTE: chained on develop's head, 0095; the lead re-chains this one when Marketplace (0094)
and Auth (0096) land first.
"""

import sqlalchemy as sa

from alembic import op

revision = "0097_ai_conversation_archive"
down_revision = "0095_funding_data_room"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_conversations", sa.Column("topic", sa.String(length=24), nullable=True))
    op.add_column(
        "ai_conversations", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_ai_conversations_topic", "ai_conversations", ["topic"])


def downgrade() -> None:
    op.drop_index("ix_ai_conversations_topic", table_name="ai_conversations")
    op.drop_column("ai_conversations", "archived_at")
    op.drop_column("ai_conversations", "topic")
