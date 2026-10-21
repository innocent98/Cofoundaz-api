"""Sales slice C (coach + analytics): coach sessions, coach messages, call preps

Revision ID: 0064_sales_coach
Revises: 0063_sales_crm_core
Create Date: 2026-10-21

Three new tables, all startup-scoped. Additive only. Analytics reads existing tables.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0064_sales_coach"
down_revision = "0063_sales_crm_core"
branch_labels = None
depends_on = None


def _uuid(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _enum(name: str, length: int, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def upgrade() -> None:
    op.create_table(
        "sales_coach_sessions",
        _uuid("id"),
        _uuid("startup_id"),
        _uuid("created_by", nullable=True),
        sa.Column("mode", sa.String(12), nullable=False),
        _uuid("deal_id", nullable=True),
        sa.Column("scenario", sa.Text(), nullable=True),
        sa.Column("persona", sa.String(200), nullable=False),
        sa.Column(
            "difficulty",
            _enum("coachdifficulty", 10, "friendly", "neutral", "tough"),
            nullable=False,
        ),
        sa.Column("context_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            _enum("coachsessionstatus", 10, "active", "scoring", "ended"),
            nullable=False,
        ),
        sa.Column("scorecard_json", postgresql.JSONB(), nullable=True),
        sa.Column("scorecard_error", sa.String(200), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_coach_sessions_startup_id", "sales_coach_sessions", ["startup_id"])
    op.create_index("ix_sales_coach_sessions_created_by", "sales_coach_sessions", ["created_by"])
    op.create_index("ix_sales_coach_sessions_deal_id", "sales_coach_sessions", ["deal_id"])
    op.create_index(
        "ix_sales_coach_sessions_owner", "sales_coach_sessions", ["startup_id", "created_by"]
    )

    op.create_table(
        "sales_coach_messages",
        _uuid("id"),
        _uuid("session_id"),
        sa.Column("role", _enum("chatmessagerole", 12, "user", "assistant"), nullable=False),
        sa.Column(
            "status",
            _enum("chatmessagestatus", 12, "generating", "ready", "failed"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["session_id"], ["sales_coach_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_coach_messages_session_id", "sales_coach_messages", ["session_id"])

    op.create_table(
        "sales_call_preps",
        _uuid("id"),
        _uuid("startup_id"),
        _uuid("deal_id"),
        _uuid("requested_by", nullable=True),
        sa.Column(
            "status",
            _enum("callprepstatus", 12, "generating", "ready", "failed"),
            nullable=False,
        ),
        sa.Column("output_json", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.String(200), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_call_preps_startup_id", "sales_call_preps", ["startup_id"])
    op.create_index("ix_sales_call_preps_deal_id", "sales_call_preps", ["deal_id"])


def downgrade() -> None:
    op.drop_table("sales_call_preps")
    op.drop_table("sales_coach_messages")
    op.drop_table("sales_coach_sessions")
