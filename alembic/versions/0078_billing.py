"""Billing: plans, subscriptions, payment methods, invoices, add-ons, webhook events

Revision ID: 0078_billing
Revises: 0077_settings_notif_schedule
Create Date: 2026-10-27

Cofoundaz's own subscriptions on Paystack. Seeds the three plans from
app/services/billing/catalog.py. Downgrade drops every billing table.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0078_billing"
down_revision = "0077_settings_notif_schedule"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)


_GROWTH = ["marketing", "sales", "finance", "validation", "analytics", "sales_outreach"]
# Inlined (not imported from app code) so this migration seeds the same rows forever.
_PLANS = [
    {"key": "starter", "name": "Starter", "tagline": "For validating an idea.", "rank": 1,
     "price_monthly_minor": 0, "price_annual_minor": 0, "ai_credits": 500, "seats": 1,
     "storage_mb": 5120, "modules": [],
     "features": ["Core AI Co-Founder", "Roadmap and Health Score", "Business Builder", "Documents",
                  "500 AI credits monthly", "1 seat"]},
    {"key": "growth", "name": "Growth", "tagline": "For getting to revenue.", "rank": 2,
     "price_monthly_minor": 2_400_000, "price_annual_minor": 24_000_000, "ai_credits": 5000,
     "seats": 5, "storage_mb": 20480, "modules": _GROWTH,
     "features": ["Everything in Starter", "Marketing, Sales, Finance, Validation Hubs",
                  "Analytics and Reports", "5,000 AI credits monthly", "5 seats"]},
    {"key": "scale", "name": "Scale", "tagline": "For raising and expanding.", "rank": 3,
     "price_monthly_minor": 6_800_000, "price_annual_minor": 68_000_000, "ai_credits": 20000,
     "seats": None, "storage_mb": 102400,
     "modules": [*_GROWTH, "funding", "investor_readiness", "legal", "priority_support"],
     "features": ["Everything in Growth", "Funding Hub and Investor Readiness",
                  "Legal and Compliance", "Priority support", "20,000 AI credits monthly",
                  "Unlimited seats"]},
]


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    ]


def _startup() -> sa.Column:
    return sa.Column(
        "startup_id", _UUID, sa.ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        *_base(),
        sa.Column("key", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("tagline", sa.String(120), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("price_monthly_minor", sa.Integer(), nullable=False),
        sa.Column("price_annual_minor", sa.Integer(), nullable=False),
        sa.Column("paystack_plan_code_monthly", sa.String(64)),
        sa.Column("paystack_plan_code_annual", sa.String(64)),
        sa.Column("ai_credits", sa.Integer(), nullable=False),
        sa.Column("seats", sa.Integer()),
        sa.Column("storage_mb", sa.Integer(), nullable=False),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("modules", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_table(
        "billing_subscriptions",
        *_base(),
        _startup(),
        sa.Column("plan_key", sa.String(20), server_default="starter", nullable=False),
        sa.Column("interval", sa.String(8), server_default="monthly", nullable=False),
        sa.Column("status", sa.String(12), server_default="free", nullable=False),
        sa.Column("current_period_start", _TS),
        sa.Column("current_period_end", _TS),
        sa.Column("trial_ends_at", _TS),
        sa.Column("trial_used_at", _TS),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("canceled_at", _TS),
        sa.Column("cancel_reason", sa.String(80)),
        sa.Column("pending_plan_key", sa.String(20)),
        sa.Column("pending_interval", sa.String(8)),
        sa.Column("payment_failed_at", _TS),
        sa.Column("grace_ends_at", _TS),
        sa.Column("paystack_customer_code", sa.String(64)),
        sa.Column("paystack_subscription_code", sa.String(64)),
        sa.Column("paystack_email_token", sa.String(64)),
        sa.Column("usage_alert_cycle", sa.String(10)),
        sa.UniqueConstraint("startup_id", name="uq_billing_subscriptions_startup_id"),
    )
    op.create_index(
        "ix_billing_subscriptions_customer", "billing_subscriptions", ["paystack_customer_code"]
    )
    op.create_index(
        "ix_billing_subscriptions_sub_code", "billing_subscriptions", ["paystack_subscription_code"]
    )
    op.create_table(
        "billing_payment_methods",
        *_base(),
        _startup(),
        sa.Column("authorization_code", sa.String(64), nullable=False),
        sa.Column("signature", sa.String(80), nullable=False),
        sa.Column("brand", sa.String(20), nullable=False),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("exp_month", sa.String(2), nullable=False),
        sa.Column("exp_year", sa.String(4), nullable=False),
        sa.Column("bank", sa.String(80)),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("removed_at", _TS),
        sa.UniqueConstraint("startup_id", "signature", name="uq_billing_pm_signature"),
    )
    op.create_index("ix_billing_payment_methods_startup_id", "billing_payment_methods", ["startup_id"])
    op.create_table(
        "billing_invoices",
        *_base(),
        _startup(),
        sa.Column("number", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("description", sa.String(160), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="NGN", nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False, unique=True),
        sa.Column("plan_key", sa.String(20)),
        sa.Column("paid_at", _TS),
    )
    op.create_index("ix_billing_invoices_startup_id", "billing_invoices", ["startup_id"])
    op.create_table(
        "billing_addon_purchases",
        *_base(),
        _startup(),
        sa.Column("addon_key", sa.String(30), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False, unique=True),
    )
    op.create_index("ix_billing_addon_purchases_startup_id", "billing_addon_purchases", ["startup_id"])
    op.create_table(
        "billing_details",
        *_base(),
        _startup(),
        sa.Column("company_name", sa.String(160)),
        sa.Column("tax_id", sa.String(40)),
        sa.Column("address", sa.Text()),
        sa.UniqueConstraint("startup_id", name="uq_billing_details_startup_id"),
    )
    op.create_table(
        "billing_webhook_events",
        *_base(),
        sa.Column("event_key", sa.String(160), nullable=False, unique=True),
        sa.Column("event", sa.String(60), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("processed_at", _TS),
        sa.Column("error", sa.Text()),
    )
    op.create_table(
        "billing_operations",
        *_base(),
        _startup(),
        sa.Column("reference", sa.String(80), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), server_default="pending", nullable=False),
        sa.Column("initiated_by", _UUID, sa.ForeignKey("users.id")),
    )
    op.create_index("ix_billing_operations_startup_id", "billing_operations", ["startup_id"])

    plans = sa.table(
        "billing_plans",
        sa.column("id", _UUID),
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("tagline", sa.String),
        sa.column("rank", sa.SmallInteger),
        sa.column("price_monthly_minor", sa.Integer),
        sa.column("price_annual_minor", sa.Integer),
        sa.column("ai_credits", sa.Integer),
        sa.column("seats", sa.Integer),
        sa.column("storage_mb", sa.Integer),
        sa.column("features", postgresql.JSONB),
        sa.column("modules", postgresql.JSONB),
    )
    import uuid

    op.bulk_insert(plans, [{**p, "id": uuid.uuid4()} for p in _PLANS])


def downgrade() -> None:
    for t in (
        "billing_operations",
        "billing_webhook_events",
        "billing_details",
        "billing_addon_purchases",
        "billing_invoices",
        "billing_payment_methods",
        "billing_subscriptions",
        "billing_plans",
    ):
        op.drop_table(t)
