"""finance transactions

Revision ID: 0039_finance_transactions
Revises: 0038_marketing_metrics
Create Date: 2026-09-29

Module 12 Slice 1 (Cash Flow): the transactions ledger — one new table, additive, no lock on
existing tables. Money is stored as amount_minor (integer minor units) + currency; direction
carries the sign.
"""

from alembic import op
import sqlalchemy as sa

revision = "0039_finance_transactions"
down_revision = "0038_marketing_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="NGN", nullable=False),
        sa.Column(
            "direction",
            sa.Enum("in", "out", name="transactiondirection", native_enum=False, length=8),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum("manual", "bank", "accounting", "stripe", name="transactionsource",
                    native_enum=False, length=12),
            server_default="manual", nullable=False,
        ),
        sa.Column("external_ref", sa.String(length=200), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"], name=op.f("fk_transactions_startup_id_startups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
    )
    op.create_index(op.f("ix_transactions_startup_id"), "transactions", ["startup_id"], unique=False)
    op.create_index("ix_transactions_startup_date", "transactions", ["startup_id", "date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transactions_startup_date", table_name="transactions")
    op.drop_index(op.f("ix_transactions_startup_id"), table_name="transactions")
    op.drop_table("transactions")
