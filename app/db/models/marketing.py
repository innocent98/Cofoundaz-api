import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import ChannelKey, ChannelStatus, ContentStatus


class ContentCalendarEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "content_calendar"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[ChannelKey] = mapped_column(
        Enum(ChannelKey, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, native_enum=False, length=12),
        nullable=False,
        default=ContentStatus.draft,
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketingChannel(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "marketing_channels"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[ChannelKey] = mapped_column(
        Enum(ChannelKey, native_enum=False, length=20), nullable=False
    )
    status: Mapped[ChannelStatus] = mapped_column(
        Enum(ChannelStatus, native_enum=False, length=12),
        nullable=False,
        default=ChannelStatus.not_started,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("startup_id", "key", name="uq_marketing_channel_startup_key"),
    )
