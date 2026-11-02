"""Finance invoice automation: reminder bookkeeping columns on invoices

Revision ID: 0066_finance_invoice_automation
Revises: 0065_finance_payments_core
Create Date: 2026-10-21

Adds last_reminded_at, reminder_count (NOT NULL, server default 0) and overdue_notified_at to
invoices. They back the daily overdue scan: spacing and the reminder cap read last_reminded_at /
reminder_count, and overdue_notified_at makes the overdue event one-shot per invoice.
"""

import sqlalchemy as sa

from alembic import op

revision = "0066_finance_invoice_automation"
down_revision = "0065_finance_payments_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("last_reminded_at", sa.DateTime(timezone=True)))
    op.add_column(
        "invoices",
        sa.Column("reminder_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("invoices", sa.Column("overdue_notified_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("invoices", "overdue_notified_at")
    op.drop_column("invoices", "reminder_count")
    op.drop_column("invoices", "last_reminded_at")
