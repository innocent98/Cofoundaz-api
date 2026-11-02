import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class BillingPlan(UUIDMixin, TimestampMixin, Base):
    """A sellable plan. Prices are kobo; changing one is a data change plus a new Paystack plan code."""

    __tablename__ = "billing_plans"

    key: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    tagline: Mapped[str] = mapped_column(String(120), nullable=False)
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_monthly_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    price_annual_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    paystack_plan_code_monthly: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paystack_plan_code_annual: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = unlimited
    storage_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    features: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    modules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class BillingSubscription(UUIDMixin, TimestampMixin, Base):
    """One row per workspace. `status`: free, trialing, active, past_due, canceled."""

    __tablename__ = "billing_subscriptions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    plan_key: Mapped[str] = mapped_column(String(20), nullable=False, server_default="starter")
    interval: Mapped[str] = mapped_column(String(8), nullable=False, server_default="monthly")
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="free")
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pending_plan_key: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pending_interval: Mapped[str | None] = mapped_column(String(8), nullable=True)
    payment_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    grace_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paystack_customer_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    paystack_subscription_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    paystack_email_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    usage_alert_cycle: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # A complimentary plan granted by staff (D-ADM-6): active, no Paystack link, lapses at the
    # period end. Not revenue, so MRR and paid conversion skip it.
    comped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class BillingPaymentMethod(UUIDMixin, TimestampMixin, Base):
    """A reusable card authorization reported by Paystack. We never see a card number."""

    __tablename__ = "billing_payment_methods"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    authorization_code: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(String(80), nullable=False)
    brand: Mapped[str] = mapped_column(String(20), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    exp_month: Mapped[str] = mapped_column(String(2), nullable=False)
    exp_year: Mapped[str] = mapped_column(String(4), nullable=False)
    bank: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("startup_id", "signature", name="uq_billing_pm_signature"),)


class BillingInvoice(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "billing_invoices"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # subscription|upgrade|addon
    description: Mapped[str] = mapped_column(String(160), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="NGN")
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # paid|failed|refunded
    reference: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    plan_key: Mapped[str | None] = mapped_column(String(20), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Kobo staff have recorded a refund for but not yet marked done (D-ADM-6): staff-only, counts
    # against the cap, invisible to the founder.
    refund_pending_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Kobo whose refund has really been made at Paystack and marked done; the founder sees this
    # and `status` flips to refunded only when it reaches the invoice amount.
    refunded_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "refunded_minor >= 0 AND refund_pending_minor >= 0 "
            "AND refunded_minor + refund_pending_minor <= amount_minor",
            name="ck_billing_invoices_refund_bounds",
        ),
    )


class BillingAddonPurchase(UUIDMixin, TimestampMixin, Base):
    """Credit-pack purchases; the sum of `credits` is added to the plan's monthly credits."""

    __tablename__ = "billing_addon_purchases"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    addon_key: Mapped[str] = mapped_column(String(30), nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)


class BillingDetails(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "billing_details"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    company_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)


class BillingWebhookEvent(UUIDMixin, TimestampMixin, Base):
    """Every verified Paystack event, keyed so a redelivery is recognised and skipped."""

    __tablename__ = "billing_webhook_events"

    event_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BillingOperation(UUIDMixin, TimestampMixin, Base):
    """A reference we initiated (checkout, card check, add-on, upgrade charge) and what it is for.
    The webhook and the verify route resolve a Paystack reference back to this row."""

    __tablename__ = "billing_operations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reference: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # checkout|card|addon|upgrade
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class BillingOverride(UUIDMixin, TimestampMixin, Base):
    """A Super Admin billing override (PRD 26.6, D-ADM-6): refund, credit, extend trial or comp.
    `request_id` is the caller's idempotency key: a replay returns this row instead of applying
    the override twice."""

    __tablename__ = "billing_overrides"

    request_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), unique=True, nullable=False)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # refund|credit|trial|comp
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billing_invoices.id"), nullable=True
    )
    amount_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)  # refund, kobo
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)  # AI credits
    days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # trial extension / comp length
    plan_key: Mapped[str | None] = mapped_column(String(20), nullable=True)  # comp
    # refund only: "pending" = recorded here, no money has moved at Paystack yet; "done" = staff
    # confirmed it was made there (`completed_at`, `completed_by_user_id`)
    provider_refund: Mapped[str | None] = mapped_column(String(10), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
