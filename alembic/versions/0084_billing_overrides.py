"""Billing overrides (Admin slice C): billing_overrides, comped flag, invoice refunded_minor

Revision ID: 0084_billing_overrides
Revises: 0083_platform_staff
Create Date: 2026-10-31

Adds the `billing_overrides` ledger (refund, credit, trial extension, comp; `request_id` unique so
a replay cannot apply twice), `billing_subscriptions.comped` and `billing_invoices.refunded_minor`.
A DB check keeps refunded plus pending refunds within the invoice amount.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0084_billing_overrides"
down_revision = "0083_platform_staff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "billing_subscriptions",
        sa.Column("comped", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "billing_invoices",
        sa.Column("refunded_minor", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "billing_invoices",
        sa.Column("refund_pending_minor", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_billing_invoices_refund_bounds",
        "billing_invoices",
        "refunded_minor >= 0 AND refund_pending_minor >= 0 "
        "AND refunded_minor + refund_pending_minor <= amount_minor",
    )
    op.create_table(
        "billing_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column(
            "startup_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("startups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("billing_invoices.id"),
            nullable=True,
        ),
        sa.Column("amount_minor", sa.Integer(), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=True),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("plan_key", sa.String(20), nullable=True),
        sa.Column("provider_refund", sa.String(10), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("details", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_billing_overrides_startup_id", "billing_overrides", ["startup_id"])


def downgrade() -> None:
    op.drop_index("ix_billing_overrides_startup_id", table_name="billing_overrides")
    op.drop_table("billing_overrides")
    op.drop_constraint("ck_billing_invoices_refund_bounds", "billing_invoices", type_="check")
    op.drop_column("billing_invoices", "refund_pending_minor")
    op.drop_column("billing_invoices", "refunded_minor")
    op.drop_column("billing_subscriptions", "comped")
