"""finance model

Revision ID: 0045_finance_model
Revises: 0044_finance_budgets
Create Date: 2026-09-30

Module 12 Slice 5 (Financial Model): the financial_models table — one new table, additive, no lock
on existing tables. Each row is one AI-generated projection run for a startup: status
(generating/complete/failed, varchar-backed via native_enum=False, length 12), horizon_months and
currency, the assumptions plus the three statements (pnl, cash_flow, balance_sheet) as nullable
JSONB filled in when generation completes, a short error string for failed runs, and generated_at.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0045_finance_model"
down_revision = "0044_finance_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_models",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "generating",
                "complete",
                "failed",
                name="financialmodelstatus",
                native_enum=False,
                length=12,
            ),
            server_default="generating",
            nullable=False,
        ),
        sa.Column("horizon_months", sa.Integer(), server_default="12", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="NGN", nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pnl", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cash_flow", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("balance_sheet", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_financial_models_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_financial_models_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_models")),
    )
    op.create_index(
        op.f("ix_financial_models_startup_id"), "financial_models", ["startup_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_financial_models_startup_id"), table_name="financial_models")
    op.drop_table("financial_models")
