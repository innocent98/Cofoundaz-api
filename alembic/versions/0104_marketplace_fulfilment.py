"""Marketplace slice D: deliveries, threads, messages, booking documents

Revision ID: 0104_marketplace_fulfilment
Revises: 0101_marketplace_booking
Create Date: 2026-11-12

Marketplace slice D (fulfilment and messaging). Adds `marketplace_deliveries` (one row per hand-over
of work, with the founder's decision), `marketplace_threads` (one pre-booking conversation per
workspace and expert, one thread per booking) and `marketplace_messages` (plain text, a read stamp
per side), and `marketplace_booking_documents` (expiring, revocable "attach from Documents" links).
Adds `marketplace_files.thread_id` (a file sent in a pre-booking message),
`marketplace_bookings.overdue_notified_at` (one overdue notice per package) and
`marketplace_orders.attention_resolved_at/by` (a staff flag stops needing a person). Downgrade
drops all of it.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0104_marketplace_fulfilment"
down_revision = "0101_marketplace_booking"
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


def _fk(name: str, table: str, ondelete: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(f"{table}.id", ondelete=ondelete),
        nullable=nullable,
    )


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.add_column("marketplace_bookings", _ts("overdue_notified_at"))
    op.add_column("marketplace_orders", _ts("attention_resolved_at"))
    op.add_column(
        "marketplace_orders", _fk("attention_resolved_by", "users", "SET NULL", nullable=True)
    )

    op.create_table(
        "marketplace_deliveries",
        _id(),
        _fk("booking_id", "marketplace_bookings", "RESTRICT"),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("file_ids", postgresql.JSONB(), nullable=False),
        _ts("submitted_at", nullable=False),
        sa.Column("decision", sa.String(20), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        _ts("decided_at"),
        *_stamps(),
        sa.UniqueConstraint("booking_id", "round", name="uq_marketplace_delivery_round"),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('accepted','changes_requested','auto_released')",
            name="ck_marketplace_delivery_decision",
        ),
    )

    op.create_table(
        "marketplace_threads",
        _id(),
        _fk("startup_id", "startups", "RESTRICT"),
        _fk("provider_id", "marketplace_providers", "RESTRICT"),
        sa.Column(
            "booking_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("marketplace_bookings.id", ondelete="RESTRICT"),
            nullable=True,
            unique=True,
        ),
        _ts("last_message_at"),
        _ts("provider_emailed_at"),
        *_stamps(),
    )
    op.create_index("ix_marketplace_threads_startup_id", "marketplace_threads", ["startup_id"])
    op.create_index("ix_marketplace_threads_provider_id", "marketplace_threads", ["provider_id"])
    op.create_index(
        "uq_marketplace_thread_pair",
        "marketplace_threads",
        ["startup_id", "provider_id"],
        unique=True,
        postgresql_where=sa.text("booking_id IS NULL"),
    )

    op.create_table(
        "marketplace_messages",
        _id(),
        _fk("thread_id", "marketplace_threads", "CASCADE"),
        _fk("sender_user_id", "users", "RESTRICT"),
        sa.Column("sender_side", sa.String(10), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("file_ids", postgresql.JSONB(), nullable=False),
        _ts("read_at_founder_side"),
        _ts("read_at_provider_side"),
        *_stamps(),
        sa.CheckConstraint("sender_side IN ('founder','provider')", name="ck_marketplace_msg_side"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 4000", name="ck_marketplace_msg_len"),
    )
    op.create_index(
        "ix_marketplace_messages_thread_created",
        "marketplace_messages",
        ["thread_id", "created_at"],
    )

    op.add_column(
        "marketplace_files", _fk("thread_id", "marketplace_threads", "SET NULL", nullable=True)
    )

    op.create_table(
        "marketplace_booking_documents",
        _id(),
        _fk("booking_id", "marketplace_bookings", "RESTRICT"),
        _fk("startup_id", "startups", "RESTRICT"),
        _fk("document_id", "documents", "CASCADE"),
        _fk("granted_by", "users", "SET NULL", nullable=True),
        _ts("expires_at", nullable=False),
        _ts("revoked_at"),
        *_stamps(),
        sa.UniqueConstraint("booking_id", "document_id", name="uq_marketplace_booking_document"),
    )
    op.create_index(
        "ix_marketplace_booking_documents_booking_id",
        "marketplace_booking_documents",
        ["booking_id"],
    )


def downgrade() -> None:
    op.drop_table("marketplace_booking_documents")
    op.drop_column("marketplace_files", "thread_id")
    op.drop_table("marketplace_messages")
    op.drop_table("marketplace_threads")
    op.drop_table("marketplace_deliveries")
    op.drop_column("marketplace_orders", "attention_resolved_by")
    op.drop_column("marketplace_orders", "attention_resolved_at")
    op.drop_column("marketplace_bookings", "overdue_notified_at")
