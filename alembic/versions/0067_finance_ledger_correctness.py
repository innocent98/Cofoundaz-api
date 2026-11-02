"""Finance ledger correctness: private receipts flag, one generating model per workspace

Revision ID: 0067_finance_ledger_correctness
Revises: 0066_finance_invoice_automation
Create Date: 2026-10-22

1. expenses.receipt_private (NOT NULL, default false): marks receipts whose asset already sits
   behind authentication. Every existing row is false, i.e. a legacy receipt that was uploaded
   publicly; the API moves it behind authentication the first time it is opened and stops
   returning its raw URL immediately.
2. Partial unique index on financial_models(startup_id) WHERE status = 'generating', so two
   concurrent POST /finance/model/generate calls cannot both start. Any duplicate `generating`
   rows that already exist are failed first (all but the newest per workspace), otherwise the
   index could not be built.
"""

import sqlalchemy as sa

from alembic import op

revision = "0067_finance_ledger_correctness"
down_revision = "0066_finance_invoice_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expenses",
        sa.Column("receipt_private", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        """
        UPDATE financial_models
        SET status = 'failed', error = 'Superseded by a newer generation'
        WHERE status = 'generating' AND id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY startup_id ORDER BY created_at DESC, id DESC
                ) AS rn
                FROM financial_models WHERE status = 'generating'
            ) ranked WHERE rn > 1
        )
        """
    )
    op.create_index(
        "uq_financial_models_one_generating",
        "financial_models",
        ["startup_id"],
        unique=True,
        postgresql_where=sa.text("status = 'generating'"),
    )


def downgrade() -> None:
    op.drop_index("uq_financial_models_one_generating", table_name="financial_models")
    op.drop_column("expenses", "receipt_private")
