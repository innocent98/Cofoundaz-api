import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import MissionStatus, MissionTaskStatus, TaskEffort


class Mission(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "missions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mission_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(10), nullable=False, default="system")
    status: Mapped[MissionStatus] = mapped_column(
        Enum(MissionStatus, native_enum=False, length=20),
        default=MissionStatus.pending,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("startup_id", "mission_date", name="uq_mission_startup_date"),
    )


class MissionTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "mission_tasks"

    mission_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Soft link only — no FK. roadmap_tasks may be regenerated/deleted independently
    # of a mission snapshot, and this column must not block or cascade on that.
    roadmap_task_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    effort: Mapped[TaskEffort] = mapped_column(
        Enum(TaskEffort, native_enum=False, length=20),
        default=TaskEffort.medium,
        nullable=False,
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[MissionTaskStatus] = mapped_column(
        Enum(MissionTaskStatus, native_enum=False, length=20),
        default=MissionTaskStatus.todo,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)


class MissionSettings(TimestampMixin, Base):
    __tablename__ = "mission_settings"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mission_size: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    delivery_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(6, 0))
    weekend_missions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
