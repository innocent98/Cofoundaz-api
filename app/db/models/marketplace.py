import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    AvailabilityKind,
    ListingStatus,
    ListingType,
    MarketplaceFilePurpose,
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
