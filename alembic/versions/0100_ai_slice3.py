"""AI Co-Founder slice 3: actions, feedback, regenerate, suggestions, pinned agent

Revision ID: 0100_ai_slice3
Revises: 0098_ai_orchestrator
Create Date: 2026-11-06

Adds `ai_actions` (confirm-first proposals and their undo data), `ai_suggestions` (per-member
suggestion cards), message columns `feedback`, `feedback_note`, `generation`, and
`ai_conversations.pinned_agent_key`. Downgrade drops all of it.

CHAIN NOTE: 0099 is reserved for Funding #229; the lead re-chains whichever lands second.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0100_ai_slice3"
down_revision = "0098_ai_orchestrator"
branch_labels = None
depends_on = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.add_column("ai_conversations", sa.Column("pinned_agent_key", sa.String(24), nullable=True))
    op.add_column("ai_messages", sa.Column("feedback", sa.String(8), nullable=True))
    op.add_column("ai_messages", sa.Column("feedback_note", sa.Text(), nullable=True))
    op.add_column(
        "ai_messages",
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "ai_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["message_id"], ["ai_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_actions_message_id", "ai_actions", ["message_id"])
    op.create_table(
        "ai_suggestions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(300), nullable=True),
        sa.Column("href", sa.String(120), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "startup_id", "source", "source_ref", name="uq_ai_suggestion"),
    )
    op.create_index("ix_ai_suggestions_startup_id", "ai_suggestions", ["startup_id"])
    op.create_index("ix_ai_suggestions_user_id", "ai_suggestions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_suggestions_user_id", table_name="ai_suggestions")
    op.drop_index("ix_ai_suggestions_startup_id", table_name="ai_suggestions")
    op.drop_table("ai_suggestions")
    op.drop_index("ix_ai_actions_message_id", table_name="ai_actions")
    op.drop_table("ai_actions")
    op.drop_column("ai_messages", "generation")
    op.drop_column("ai_messages", "feedback_note")
    op.drop_column("ai_messages", "feedback")
    op.drop_column("ai_conversations", "pinned_agent_key")
