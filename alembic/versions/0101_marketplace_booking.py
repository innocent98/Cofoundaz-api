"""Marketplace slice C: bookings, orders, ledger, webhook events, consents

Revision ID: 0101_marketplace_booking
Revises: 0102_funding_term_sheets
Create Date: 2026-11-06

Marketplace slice C (booking, checkout and ledger). Adds `marketplace_bookings`,
`marketplace_orders`, `marketplace_ledger_entries` (append-only: a trigger refuses UPDATE and
DELETE, like `audit_log`), `marketplace_webhook_events`, `marketplace_consents` and the
`marketplace_receipt_seq` sequence. Amounts are kobo; CHECK constraints keep an order's
commission + net equal to its amount and its refunds within it. A partial unique index is the
backstop that two live bookings never start at the same instant for one expert. Downgrade drops
all of it.

CHAIN NOTE: chained on develop's head (0100, AI slice 3). 0099 (Funding) is still pending; if it
merges first the lead re-chains 0101 onto it.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0101_marketplace_booking"
down_revision = "0102_funding_term_sheets"
branch_labels = None
depends_on = None

FUNCTION_SQL = """
CREATE FUNCTION marketplace_ledger_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'marketplace_ledger_entries is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql
"""

TRIGGER_SQL = """
CREATE TRIGGER marketplace_ledger_append_only
BEFORE UPDATE OR DELETE ON marketplace_ledger_entries
FOR EACH ROW EXECUTE FUNCTION marketplace_ledger_reject_change()
"""


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


def _fk(table: str, ondelete: str, *, nullable: bool = False) -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete=ondelete)


def _uuid_fk(name: str, table: str, ondelete: str, **kw: object) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), _fk(table, ondelete), **kw)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "marketplace_bookings",
        _id(),
        _uuid_fk("startup_id", "startups", "RESTRICT", nullable=False),
        _uuid_fk("listing_id", "marketplace_listings", "RESTRICT", nullable=False),
        _uuid_fk("provider_id", "marketplace_providers", "RESTRICT", nullable=False),
        _uuid_fk("booked_by", "users", "RESTRICT", nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("title_snap", sa.String(160), nullable=False),
        sa.Column("category_snap", sa.String(20), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("commission_bps_snap", sa.Integer(), nullable=False),
        sa.Column("deliverables_snap", postgresql.JSONB(), nullable=False),
        sa.Column("duration_min_snap", sa.Integer(), nullable=True),
        sa.Column("delivery_days_snap", sa.Integer(), nullable=True),
        sa.Column("revisions_included_snap", sa.Integer(), nullable=False),
        sa.Column("meeting_url_snap", sa.String(500), nullable=True),
        sa.Column("brief", sa.Text(), nullable=False),
        _ts("starts_at"),
        _ts("ends_at"),
        _ts("slot_hold_expires_at"),
        _ts("accept_by"),
        _ts("accepted_at"),
        _ts("due_at"),
        _ts("delivered_at"),
        _ts("auto_release_at"),
        sa.Column("revisions_used", sa.Integer(), nullable=False),
        sa.Column("calendar_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("closed_reason", sa.String(60), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        *_stamps(),
        sa.UniqueConstraint("booked_by", "idempotency_key", name="uq_marketplace_booking_idem"),
    )
    op.create_index("ix_marketplace_bookings_provider_id", "marketplace_bookings", ["provider_id"])
    op.create_index(
        "ix_marketplace_bookings_startup_status", "marketplace_bookings", ["startup_id", "status"]
    )
    op.create_index(
        "ix_marketplace_bookings_hold", "marketplace_bookings", ["status", "slot_hold_expires_at"]
    )
    op.create_index(
        "ix_marketplace_bookings_accept", "marketplace_bookings", ["status", "accept_by"]
    )
    op.create_index(
        "uq_marketplace_booking_slot",
        "marketplace_bookings",
        ["provider_id", "starts_at"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('awaiting_provider','confirmed','in_progress','delivered','disputed')"
            " AND starts_at IS NOT NULL"
        ),
    )

    op.create_table(
        "marketplace_orders",
        _id(),
        _uuid_fk("booking_id", "marketplace_bookings", "RESTRICT", nullable=False, unique=True),
        _uuid_fk("startup_id", "startups", "RESTRICT", nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("commission_minor", sa.BigInteger(), nullable=False),
        sa.Column("provider_net_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("settlement_mode", sa.String(12), nullable=False),
        sa.Column("paystack_reference", sa.String(40), nullable=False, unique=True),
        sa.Column("checkout_url", sa.String(300), nullable=True),
        _ts("paid_at"),
        sa.Column("receipt_number", sa.String(24), nullable=True, unique=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("refunded_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("refund_due_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("refund_reason", sa.String(60), nullable=True),
        sa.Column("refund_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("refund_next_attempt_at"),
        _ts("refund_inflight_since"),
        sa.Column("attention_reason", sa.String(40), nullable=True),
        _ts("attention_at"),
        sa.Column("attention_amount_minor", sa.BigInteger(), nullable=True),
        _ts("refund_failed_at"),
        *_stamps(),
        sa.CheckConstraint("amount_minor > 0", name="ck_marketplace_order_amount_positive"),
        sa.CheckConstraint(
            "commission_minor >= 0 AND provider_net_minor >= 0 "
            "AND commission_minor + provider_net_minor = amount_minor",
            name="ck_marketplace_order_split",
        ),
        sa.CheckConstraint(
            "refunded_minor >= 0 AND refund_due_minor >= 0 "
            "AND refunded_minor + refund_due_minor <= amount_minor",
            name="ck_marketplace_order_refund_bounds",
        ),
    )
    op.create_index("ix_marketplace_orders_startup_id", "marketplace_orders", ["startup_id"])
    op.create_index("ix_marketplace_orders_attention", "marketplace_orders", ["attention_at"])
    op.create_index(
        "ix_marketplace_orders_refund_due",
        "marketplace_orders",
        ["refund_due_minor", "refund_next_attempt_at"],
    )

    op.create_table(
        "marketplace_ledger_entries",
        _id(),
        _uuid_fk("order_id", "marketplace_orders", "RESTRICT", nullable=False),
        _uuid_fk("provider_id", "marketplace_providers", "RESTRICT", nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("external_ref", sa.String(80), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("order_id", "kind", "external_ref", name="uq_marketplace_ledger_once"),
        sa.CheckConstraint("amount_minor <> 0", name="ck_marketplace_ledger_nonzero"),
    )
    op.create_index(
        "ix_marketplace_ledger_entries_order_id", "marketplace_ledger_entries", ["order_id"]
    )
    op.create_index(
        "ix_marketplace_ledger_entries_provider_id", "marketplace_ledger_entries", ["provider_id"]
    )
    op.execute(FUNCTION_SQL)
    op.execute(TRIGGER_SQL)

    op.create_table(
        "marketplace_webhook_events",
        _id(),
        sa.Column("event_key", sa.String(160), nullable=False, unique=True),
        sa.Column("type", sa.String(60), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("outcome", sa.String(60), nullable=True),
    )
    op.create_table(
        "marketplace_consents",
        _id(),
        _uuid_fk("user_id", "users", "CASCADE", nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "kind", "version", name="uq_marketplace_consent"),
    )
    op.execute("CREATE SEQUENCE marketplace_receipt_seq START 1001")


def downgrade() -> None:
    op.execute("DROP SEQUENCE marketplace_receipt_seq")
    op.drop_table("marketplace_consents")
    op.drop_table("marketplace_webhook_events")
    op.execute("DROP TRIGGER marketplace_ledger_append_only ON marketplace_ledger_entries")
    op.drop_table("marketplace_ledger_entries")
    op.execute("DROP FUNCTION marketplace_ledger_reject_change()")
    op.drop_table("marketplace_orders")
    op.drop_table("marketplace_bookings")
