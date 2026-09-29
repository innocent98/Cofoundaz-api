"""finance invoices

Revision ID: 0041_finance_invoices
Revises: 0040_finance_runway
Create Date: 2026-09-29

Module 12 Slice 3 (Invoices): the invoices table — one new table, additive, no lock on existing
tables. Money is integer minor units + currency; line_items is a JSONB snapshot. Invoice numbers
are unique per startup. transaction_id links a paid invoice to the ledger row it produced
(SET NULL if that transaction is deleted; unique so one transaction backs at most one invoice).
The TransactionSource enum gains "invoice" but that column is varchar-backed (native_enum=False,
length 12), so no DDL change is needed for it.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0041_finance_invoices"
down_revision = "0040_finance_runway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=False),
        sa.Column("client_email", sa.String(length=320), nullable=False),
        sa.Column(
            "line_items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("subtotal_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "tax_percent", sa.Numeric(precision=5, scale=2), server_default="0", nullable=False
        ),
        sa.Column("tax_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_minor", sa.Integer(), server_default="0", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="NGN", nullable=False),
        sa.Column(
            "terms",
            sa.Enum(
                "net_15",
                "net_30",
                "due_on_receipt",
                name="invoiceterms",
                native_enum=False,
                length=16,
            ),
            server_default="net_30",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("draft", "sent", "paid", name="invoicestatus", native_enum=False, length=8),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_remind", sa.Boolean(), server_default=sa.false(), nullable=False),
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
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_invoices_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_invoices_transaction_id_transactions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("startup_id", "number", name="uq_invoices_startup_number"),
        sa.UniqueConstraint("transaction_id", name=op.f("uq_invoices_transaction_id")),
    )
    op.create_index(op.f("ix_invoices_startup_id"), "invoices", ["startup_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_invoices_startup_id"), table_name="invoices")
    op.drop_table("invoices")
