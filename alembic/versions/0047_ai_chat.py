"""ai chat

Revision ID: 0047_ai_chat
Revises: 0046_calendar
Create Date: 2026-10-17

AI Co-Founder chat v1 (Module 03): two brand-new tables, no change to any existing
table (so only the brief locks to validate the new foreign keys).
  - ai_conversations — one row per chat thread in a workspace.
  - ai_messages — user/assistant turns. Assistant rows carry a generating → ready/failed
    status: Pattern B, where POST /ai/conversations/{id}/messages enqueues the
    ai.cofounder.reply job and the FE polls the conversation until the assistant turn is
    ready. `role`/`status` are stored as their enum VALUES (native_enum=False), matching
    the house style used by daily_briefings and calendar_events.
Every foreign key is indexed, per the house rule.

MIGRATION-HEAD COORDINATION: 0047 was reserved for Victoria's Module 13. Whichever of the
two lands on `develop` second must rebase its `down_revision` onto the first so the chain
stays single-headed (the migration-head test enforces one head).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0047_ai_chat"
down_revision = "0046_calendar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_ai_conversations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_ai_conversations_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_conversations")),
    )
    op.create_index(
        op.f("ix_ai_conversations_created_by"), "ai_conversations", ["created_by"], unique=False
    )
    op.create_index(
        op.f("ix_ai_conversations_startup_id"), "ai_conversations", ["startup_id"], unique=False
    )
    op.create_table(
        "ai_messages",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("user", "assistant", name="chatmessagerole", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "generating",
                "ready",
                "failed",
                name="chatmessagestatus",
                native_enum=False,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            name=op.f("fk_ai_messages_conversation_id_ai_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_messages")),
    )
    op.create_index(
        op.f("ix_ai_messages_conversation_id"), "ai_messages", ["conversation_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_messages_conversation_id"), table_name="ai_messages")
    op.drop_table("ai_messages")
    op.drop_index(op.f("ix_ai_conversations_startup_id"), table_name="ai_conversations")
    op.drop_index(op.f("ix_ai_conversations_created_by"), table_name="ai_conversations")
    op.drop_table("ai_conversations")
