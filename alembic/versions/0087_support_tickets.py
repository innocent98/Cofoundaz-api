"""Admin Tickets: support tickets, founder-visible messages, staff-only notes, canned replies

Revision ID: 0087_support_tickets
Revises: 0086_admin_danger_zone
Create Date: 2026-11-02

Internal notes sit in their own table so no founder query can return one. Ticket numbers come from
the `support_ticket_no_seq` sequence. Downgrade drops all four tables (and the sequence).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0087_support_tickets"
down_revision = "0086_admin_danger_zone"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _user_fk(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        _id(),
        sa.Column(
            "ticket_no",
            sa.Integer(),
            sa.Sequence("support_ticket_no_seq", start=1001),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user_fk("created_by"),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("priority", sa.String(4), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _user_fk("assigned_to"),
        sa.Column("escalated", sa.Boolean(), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        _user_fk("resolved_by"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_author", sa.String(10), nullable=False),
        *_stamps(),
    )
    op.create_index("ix_support_tickets_startup", "support_tickets", ["startup_id"])
    op.create_index("ix_support_tickets_created_by", "support_tickets", ["created_by"])
    op.create_index(
        "ix_support_tickets_status_activity", "support_tickets", ["status", "last_message_at"]
    )
    op.create_index("ix_support_tickets_assigned", "support_tickets", ["assigned_to"])

    for table, extra in (
        ("support_ticket_messages", [sa.Column("author_kind", sa.String(10), nullable=False)]),
        ("support_ticket_notes", []),
    ):
        op.create_table(
            table,
            _id(),
            sa.Column(
                "ticket_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("support_tickets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            _user_fk("author_user_id"),
            *extra,
            sa.Column("body", sa.Text(), nullable=False),
            *_stamps(),
        )
        op.create_index(f"ix_{table}_ticket", table, ["ticket_id", "created_at"])

    op.create_table(
        "support_canned_responses",
        _id(),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        _user_fk("created_by"),
        *_stamps(),
    )


def downgrade() -> None:
    op.drop_table("support_canned_responses")
    op.drop_table("support_ticket_notes")
    op.drop_table("support_ticket_messages")
    op.drop_table("support_tickets")
    op.execute("DROP SEQUENCE IF EXISTS support_ticket_no_seq")
