"""AI Co-Founder slice 2: orchestrator routes and agent output on messages

Revision ID: 0098_ai_orchestrator
Revises: 0097_ai_conversation_archive
Create Date: 2026-11-05

Adds `ai_agent_routes` (one routing decision per assistant message) and, on `ai_messages`,
`agent_key`, `reasoning_summary`, `disclaimer`, `sources`. The new message status `cancelled`
needs no DDL (the column is a plain varchar). Downgrade drops the table and the columns.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0098_ai_orchestrator"
down_revision = "0097_ai_conversation_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_messages", sa.Column("agent_key", sa.String(length=24), nullable=True))
    op.add_column("ai_messages", sa.Column("reasoning_summary", sa.Text(), nullable=True))
    op.add_column("ai_messages", sa.Column("disclaimer", sa.Text(), nullable=True))
    op.add_column("ai_messages", sa.Column("sources", postgresql.JSONB(), nullable=True))
    op.create_table(
        "ai_agent_routes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("agent_key", sa.String(length=24), nullable=False),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["message_id"], ["ai_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index("ix_ai_agent_routes_conversation_id", "ai_agent_routes", ["conversation_id"])
    op.create_index("ix_ai_agent_routes_startup_id", "ai_agent_routes", ["startup_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_agent_routes_startup_id", table_name="ai_agent_routes")
    op.drop_index("ix_ai_agent_routes_conversation_id", table_name="ai_agent_routes")
    op.drop_table("ai_agent_routes")
    op.drop_column("ai_messages", "sources")
    op.drop_column("ai_messages", "disclaimer")
    op.drop_column("ai_messages", "reasoning_summary")
    op.drop_column("ai_messages", "agent_key")
