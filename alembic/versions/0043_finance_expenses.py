"""finance expenses

Revision ID: 0043_finance_expenses
Revises: 0042_finance_invoices
Create Date: 2026-09-30

Module 12 Slice 4a (Expenses): the expenses table — one new table, additive, no lock on existing
tables. Money is integer minor units + currency. receipt_url/receipt_key hold an optional uploaded
receipt (public URL + storage key). transaction_id links an expense to the ledger row it produced
(SET NULL if that transaction is deleted; unique so one transaction backs at most one expense).
The composite (startup_id, expense_date) index serves the date-range / month queries.
The TransactionSource enum gains "expense" but that column is varchar-backed (native_enum=False,
length 12), so no DDL change is needed for it.
"""

from alembic import op
import sqlalchemy as sa

revision = "0043_finance_expenses"
down_revision = "0042_finance_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expenses",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("vendor", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="NGN", nullable=False),
        sa.Column("recurring", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("receipt_url", sa.String(length=600), nullable=True),
        sa.Column("receipt_key", sa.String(length=400), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("transaction_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_expenses_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_expenses_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_expenses_transaction_id_transactions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expenses")),
        sa.UniqueConstraint("transaction_id", name=op.f("uq_expenses_transaction_id")),
    )
    op.create_index(op.f("ix_expenses_startup_id"), "expenses", ["startup_id"], unique=False)
    op.create_index(
        "ix_expenses_startup_date", "expenses", ["startup_id", "expense_date"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_expenses_startup_date", table_name="expenses")
    op.drop_index(op.f("ix_expenses_startup_id"), table_name="expenses")
    op.drop_table("expenses")
