"""Finance slice (e): invoice client link, notes, payment details, resend bookkeeping

Revision ID: 0071_finance_slice_e
Revises: 0070_finance_model_mono
Create Date: 2026-10-24

invoices gains:
  * `account_id`      optional link to a Sales account (the client picker); SET NULL when the
                      account is deleted so the invoice (a legal record) survives it
  * `notes`           free text shown to the client on the PDF and email
  * `payment_details` bank transfer details {bank_name, account_name, account_number, instructions}
  * `last_sent_at`    when the invoice email last went out (first send or resend); drives the
                      resend cooldown

Existing invoices keep NULL in all four. `last_sent_at` is not back-filled (a date is not an
instant), so the first resend of an old invoice is never blocked by the cooldown.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0071_finance_slice_e"
down_revision = "0070_finance_model_mono"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("account_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_invoices_account_id_sales_accounts",
        "invoices",
        "sales_accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_invoices_account_id", "invoices", ["account_id"])
    op.add_column("invoices", sa.Column("notes", sa.Text()))
    op.add_column(
        "invoices", sa.Column("payment_details", postgresql.JSONB(astext_type=sa.Text()))
    )
    op.add_column("invoices", sa.Column("last_sent_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("invoices", "last_sent_at")
    op.drop_column("invoices", "payment_details")
    op.drop_column("invoices", "notes")
    op.drop_index("ix_invoices_account_id", table_name="invoices")
    op.drop_constraint("fk_invoices_account_id_sales_accounts", "invoices", type_="foreignkey")
    op.drop_column("invoices", "account_id")
