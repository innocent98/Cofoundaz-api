import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import BriefingStatus


class DailyBriefing(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "daily_briefings"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    briefing_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[BriefingStatus] = mapped_column(
        Enum(BriefingStatus, native_enum=False, length=12),
        nullable=False,
        default=BriefingStatus.generating,
    )
    briefing: Mapped[str] = mapped_column(Text, nullable=False)
    risks: Mapped[str] = mapped_column(Text, nullable=False)
    opportunities: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("startup_id", "briefing_date", name="uq_briefing_startup_date"),
    )
