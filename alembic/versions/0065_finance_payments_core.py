"""Finance payments core: provider connections, webhook ledger, invoice pay links

Revision ID: 0065_finance_payments_core
Revises: 0062_marketing_prd_b
Create Date: 2026-10-21

Adds finance_connections and provider_events, transactions.connection_id with a partial
unique index for idempotent import, and pay-link columns on invoices. The transactions
source column is a non-native enum stored as VARCHAR(12) with no CHECK constraint, so the
new paystack/flutterwave values need no constraint change. Existing 'stripe' rows (none are
ever written by the app) are left as-is; the column is plain text.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0065_finance_payments_core"
down_revision = "0064_sales_coach"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("startup_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=43), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("key_ciphertext", sa.Text(), nullable=True),
        sa.Column("secret_hash_ciphertext", sa.Text(), nullable=True),
        sa.Column("key_last4", sa.String(length=4), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("account_name", sa.String(length=200), nullable=True),
        sa.Column("sync_cursor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("connected_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_finance_connections_public_id"),
        sa.UniqueConstraint(
            "startup_id", "provider", name="uq_finance_connections_startup_provider"
        ),
    )
    op.create_index("ix_finance_connections_startup_id", "finance_connections", ["startup_id"])
    op.create_index(
        "uq_finance_connections_one_live",
        "finance_connections",
        ["startup_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'disconnected'"),
    )

    op.create_table(
        "provider_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["finance_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "event_id", name="uq_provider_events_connection_event"
        ),
    )
    op.create_index("ix_provider_events_connection_id", "provider_events", ["connection_id"])

    op.add_column(
        "transactions", sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_transactions_connection_id_finance_connections",
        "transactions",
        "finance_connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_transactions_connection_ref",
        "transactions",
        ["connection_id", "external_ref"],
        unique=True,
        postgresql_where=sa.text("connection_id IS NOT NULL"),
    )

    op.add_column("invoices", sa.Column("pay_url", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column("pay_reference", sa.String(length=120), nullable=True))
    op.add_column("invoices", sa.Column("pay_provider", sa.String(length=16), nullable=True))
    op.add_column("invoices", sa.Column("paid_via", sa.String(length=16), nullable=True))
    op.create_index("ix_invoices_pay_reference", "invoices", ["pay_reference"])


def downgrade() -> None:
    op.drop_index("ix_invoices_pay_reference", table_name="invoices")
    op.drop_column("invoices", "paid_via")
    op.drop_column("invoices", "pay_provider")
    op.drop_column("invoices", "pay_reference")
    op.drop_column("invoices", "pay_url")

    op.drop_index("uq_transactions_connection_ref", table_name="transactions")
    op.drop_constraint(
        "fk_transactions_connection_id_finance_connections", "transactions", type_="foreignkey"
    )
    op.drop_column("transactions", "connection_id")

    op.drop_index("ix_provider_events_connection_id", table_name="provider_events")
    op.drop_table("provider_events")

    op.drop_index("uq_finance_connections_one_live", table_name="finance_connections")
    op.drop_index("ix_finance_connections_startup_id", table_name="finance_connections")
    op.drop_table("finance_connections")
