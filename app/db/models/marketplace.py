import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    AvailabilityKind,
    BookingStatus,
    LedgerKind,
    ListingStatus,
    ListingType,
    MarketplaceFilePurpose,
    OrderStatus,
    ProviderStatus,
    ScanStatus,
)


class MarketplaceProvider(UUIDMixin, TimestampMixin, Base):
    """A Marketplace expert. A table of its own (like `platform_staff`), not a column on `users`,
    so no mass-assignment bug on a user can make anyone an expert. Read from the DB on every
    request by `require_provider`, never from the JWT.

    `user_id` is NULL while an invite is outstanding: the row exists so the invite token has
    somewhere to live, and is linked to the account that accepts it. `email` is the address the
    invite (or the applicant's account) belongs to."""

    __tablename__ = "marketplace_providers"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    status: Mapped[ProviderStatus] = mapped_column(
        Enum(ProviderStatus, native_enum=False, length=20),
        default=ProviderStatus.invited,
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(12), nullable=False)  # invite | application

    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    headline: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Lagos", nullable=False)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    avatar_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_files.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    # Invite token: only the SHA-256 is stored. Cleared on accept (single use) and replaced on resend.
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    terms_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # {"checks": {...ticked checks with who/when...}, "prescreen": {...deterministic flags...}}
    vetting_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    licence_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    vetted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    vetted_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The latest message staff sent back (changes requested / rejection / suspension reason).
    review_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspended_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Availability settings (slice B): apply to every consultation listing of this expert.
    min_notice_h: Mapped[int] = mapped_column(
        Integer, default=24, server_default=text("24"), nullable=False
    )
    buffer_min: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    commission_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null = default
    rating_avg: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0"), server_default=text("0"), nullable=False
    )
    rating_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )


class MarketplaceFile(UUIDMixin, TimestampMixin, Base):
    """A Marketplace upload (credential scan, portfolio piece, avatar; later briefs and
    deliveries). Providers have no workspace, so these do not live in Documents. Stored private
    from the first byte: there is no public URL, only owner and (for credentials) audited staff
    reads through the API."""

    __tablename__ = "marketplace_files"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[MarketplaceFilePurpose] = mapped_column(
        Enum(MarketplaceFilePurpose, native_enum=False, length=20), nullable=False
    )
    booking_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    scan_status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, native_enum=False, length=20), default=ScanStatus.pending, nullable=False
    )
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "ix_marketplace_files_owner_live",
    MarketplaceFile.owner_user_id,
    MarketplaceFile.purpose,
    postgresql_where=MarketplaceFile.deleted_at.is_(None),
)


class MarketplaceProviderPortfolio(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "marketplace_provider_portfolio"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("marketplace_files.id", ondelete="SET NULL"), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MarketplaceListing(UUIDMixin, TimestampMixin, Base):
    """One service an expert sells. `price_minor` is kobo (NGN only, D-MP-L15). Edits to the
    reviewed fields of a live listing wait in `pending_revision_json` while the live version stays
    up (D-MP-L8); a booking (slice C) will snapshot title, price and deliverables at purchase."""

    __tablename__ = "marketplace_listings"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[ListingType] = mapped_column(
        Enum(ListingType, native_enum=False, length=20), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deliverables_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    revisions_included: Mapped[int] = mapped_column(
        Integer, default=2, server_default=text("2"), nullable=False
    )
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, native_enum=False, length=20),
        default=ListingStatus.draft,
        nullable=False,
        index=True,
    )
    # none_as_null: clearing the revision must store SQL NULL, or the queue's IS NOT NULL filter
    # would still match a JSON `null`.
    pending_revision_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    revision_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    prescreen_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    review_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unlisted_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    unlisted_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the listing was before staff took it down (`active` or `paused`), so a relist puts it
    # back exactly there and an expert's pause survives. `unlisted_via_report_id` is set only when
    # the report flow did the unlisting; a restore of that report undoes it and nothing else.
    unlisted_prior_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    unlisted_via_report_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )


class MarketplaceAvailabilityRule(UUIDMixin, TimestampMixin, Base):
    """A weekly window in the expert's own timezone (`weekday` 0 = Monday)."""

    __tablename__ = "marketplace_availability_rules"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    slot_min: Mapped[int] = mapped_column(Integer, default=30, nullable=False)


class MarketplaceAvailabilityException(UUIDMixin, TimestampMixin, Base):
    """A date override: `blocked` (all day when the times are null, else that window) or `extra`
    hours on top of the weekly rules."""

    __tablename__ = "marketplace_availability_exceptions"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    on_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[AvailabilityKind] = mapped_column(
        Enum(AvailabilityKind, native_enum=False, length=20), nullable=False
    )
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)


ACTIVE_BOOKING = (
    BookingStatus.awaiting_provider,
    BookingStatus.confirmed,
    BookingStatus.in_progress,
    BookingStatus.delivered,
    BookingStatus.disputed,
)


# Receipt numbers (`MPR-<year>-<n>`). Declared on the metadata so test databases get it too.
receipt_seq = Sequence("marketplace_receipt_seq", start=1001, metadata=Base.metadata)


class MarketplaceBooking(UUIDMixin, TimestampMixin, Base):
    """A founder's booking of one listing. Title, price, commission and deliverables are copied at
    purchase (design D-MP-L8) so later listing edits never change what was bought. `starts_at` /
    `ends_at` exist only for consultations. `slot_hold_expires_at` is the 15-minute checkout hold
    while `pending_payment`."""

    __tablename__ = "marketplace_bookings"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="RESTRICT"), nullable=False
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_providers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    booked_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, length=24),
        default=BookingStatus.pending_payment,
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title_snap: Mapped[str] = mapped_column(String(160), nullable=False)
    category_snap: Mapped[str] = mapped_column(String(20), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    commission_bps_snap: Mapped[int] = mapped_column(Integer, nullable=False)
    deliverables_snap: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    duration_min_snap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_days_snap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revisions_included_snap: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    meeting_url_snap: Mapped[str | None] = mapped_column(String(500), nullable=True)
    brief: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    slot_hold_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accept_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revisions_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    calendar_event_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("booked_by", "idempotency_key", name="uq_marketplace_booking_idem"),
        Index("ix_marketplace_bookings_startup_status", "startup_id", "status"),
        Index("ix_marketplace_bookings_hold", "status", "slot_hold_expires_at"),
        Index("ix_marketplace_bookings_accept", "status", "accept_by"),
        # Backstop for the per-expert row lock: two live bookings can never start at the same
        # instant. Checkout holds (`pending_payment`) are guarded by the lock and a slot check, so
        # a lapsed hold never blocks a new booking.
        Index(
            "uq_marketplace_booking_slot",
            "provider_id",
            "starts_at",
            unique=True,
            postgresql_where=text(
                "status IN ('awaiting_provider','confirmed','in_progress','delivered','disputed')"
                " AND starts_at IS NOT NULL"
            ),
        ),
    )


class MarketplaceOrder(UUIDMixin, TimestampMixin, Base):
    """The money side of a booking: one order per booking, paid through Cofoundaz's own Paystack
    account (D-MP-3 a). `amount_minor` is exactly the listed price; the commission comes out of
    the expert's side (D-MP-5) and `commission_minor + provider_net_minor == amount_minor`.
    Refunds are requested in `refund_due_minor` and settled by `orders.process_refund`, which writes
    the ledger entry only once Paystack accepts the refund."""

    __tablename__ = "marketplace_orders"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_bookings.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commission_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_net_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=24), default=OrderStatus.pending, nullable=False
    )
    settlement_mode: Mapped[str] = mapped_column(String(12), default="platform", nullable=False)
    paystack_reference: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    checkout_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(24), unique=True, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    refunded_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    refund_due_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0"), nullable=False
    )
    refund_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    refund_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    refund_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set and COMMITTED before a refund is sent to Paystack; cleared once the outcome is known. A
    # crash or timeout after Paystack accepted leaves it set, so the next attempt reconciles
    # instead of sending a second refund.
    refund_inflight_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Money we can't settle by ourselves (a payment whose amount didn't match, a refund Paystack
    # keeps refusing): staff look at these. `attention_amount_minor` is what Paystack says it took.
    attention_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attention_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attention_amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    refund_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_marketplace_order_amount_positive"),
        CheckConstraint(
            "commission_minor >= 0 AND provider_net_minor >= 0 "
            "AND commission_minor + provider_net_minor = amount_minor",
            name="ck_marketplace_order_split",
        ),
        CheckConstraint(
            "refunded_minor >= 0 AND refund_due_minor >= 0 "
            "AND refunded_minor + refund_due_minor <= amount_minor",
            name="ck_marketplace_order_refund_bounds",
        ),
        Index("ix_marketplace_orders_refund_due", "refund_due_minor", "refund_next_attempt_at"),
        Index("ix_marketplace_orders_attention", "attention_at"),
    )


class MarketplaceLedgerEntry(UUIDMixin, Base):
    """Append-only (a database trigger refuses UPDATE and DELETE, like `audit_log`). Amounts are
    signed kobo: a `charge` is positive (money in, held), a `refund` negative. Statuses elsewhere
    are derived from, and reconciled against, these rows. (order_id, kind, external_ref) is unique
    so a replayed webhook or retry can never write the same movement twice."""

    __tablename__ = "marketplace_ledger_entries"

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketplace_providers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kind: Mapped[LedgerKind] = mapped_column(
        Enum(LedgerKind, native_enum=False, length=20), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    external_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("order_id", "kind", "external_ref", name="uq_marketplace_ledger_once"),
        CheckConstraint("amount_minor <> 0", name="ck_marketplace_ledger_nonzero"),
    )


class MarketplaceWebhookEvent(UUIDMixin, Base):
    """One row per Paystack event the Marketplace handled, keyed so a replay (or the same event
    delivered twice with different bytes) is answered 200 and applied once."""

    __tablename__ = "marketplace_webhook_events"

    event_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    outcome: Mapped[str | None] = mapped_column(String(60), nullable=True)


class MarketplaceConsent(UUIDMixin, Base):
    """A user's acceptance of a Marketplace terms version (design §1.3.9): asked once, before the
    first booking, and again whenever the version changes."""

    __tablename__ = "marketplace_consents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "kind", "version", name="uq_marketplace_consent"),
    )
