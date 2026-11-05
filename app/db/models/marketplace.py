import uuid
from datetime import date, datetime
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
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import MarketplaceFilePurpose, ProviderStatus, ScanStatus


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
