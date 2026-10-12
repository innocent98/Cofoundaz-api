import uuid
from datetime import date, time

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Index, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import CalendarEventType


class CalendarEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "calendar_events"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL, not CASCADE: the event belongs to the workspace, not to whoever typed it, so a
    # departing user must not take the team's calendar with them.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[CalendarEventType] = mapped_column(
        # values_callable so the DB stores the enum *value*, matching the migration labels and the
        # lowercase strings the FE badges.
        Enum(
            CalendarEventType,
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Naive wall-clock (spec D6). There is no timezone field anywhere in the app, so a time is
    # stored exactly as sent and returned exactly as stored; nothing converts.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_calendar_events_startup_id_event_date", "startup_id", "event_date"),
    )
