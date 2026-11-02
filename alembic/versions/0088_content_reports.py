"""Admin: reported issues (content reports) and the moderation-removed flag

Revision ID: 0088_content_reports
Revises: 0087_support_tickets
Create Date: 2026-11-02

Adds `content_reports` (a partial unique index keeps one live report per person per item) and a
nullable `removed_by_moderation_at` on `comments` and `ai_messages`. Downgrade drops them.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0088_content_reports"
down_revision = "0087_support_tickets"
branch_labels = None
depends_on = None


def _user_fk(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    op.create_table(
        "content_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user_fk("reporter_id"),
        sa.Column("item_type", sa.String(20), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        _user_fk("item_author_id"),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _user_fk("escalated_by"),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        _user_fk("decided_by"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_content_reports_status_created", "content_reports", ["status", "created_at"]
    )
    op.create_index("ix_content_reports_item", "content_reports", ["item_type", "item_id"])
    op.create_index("ix_content_reports_reporter", "content_reports", ["reporter_id"])
    op.create_index(
        "uq_content_reports_live",
        "content_reports",
        ["reporter_id", "item_type", "item_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'escalated')"),
    )
    for table in ("comments", "ai_messages"):
        op.add_column(
            table, sa.Column("removed_by_moderation_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    for table in ("ai_messages", "comments"):
        op.drop_column(table, "removed_by_moderation_at")
    op.drop_table("content_reports")
