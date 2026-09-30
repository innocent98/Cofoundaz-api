"""finance budgets

Revision ID: 0044_finance_budgets
Revises: 0043_finance_expenses
Create Date: 2026-09-30

Module 12 Slice 4b (Budgets): the budgets table — one new table, additive, no lock on existing
tables. A budget is a per-startup, per-category monthly spending limit. period_month is a
"YYYY-MM" string; limit_minor is integer minor units + currency. The unique constraint
(startup_id, category, period_month) allows one budget per category per month; the composite
(startup_id, period_month) index serves the per-month listing.
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_finance_budgets"
down_revision = "0043_finance_expenses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("period_month", sa.String(length=7), nullable=False),
        sa.Column("limit_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="NGN", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
            name=op.f("fk_budgets_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["startup_id"],
            ["startups.id"],
            name=op.f("fk_budgets_startup_id_startups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        sa.UniqueConstraint(
            "startup_id", "category", "period_month", name="uq_budgets_startup_category_month"
        ),
    )
    op.create_index(op.f("ix_budgets_startup_id"), "budgets", ["startup_id"], unique=False)
    op.create_index(
        "ix_budgets_startup_period", "budgets", ["startup_id", "period_month"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_budgets_startup_period", table_name="budgets")
    op.drop_index(op.f("ix_budgets_startup_id"), table_name="budgets")
    op.drop_table("budgets")
