import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import RoadmapStatus, StartupStage, TaskEffort


class Roadmap(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmaps"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    stage: Mapped[StartupStage] = mapped_column(
        Enum(StartupStage, native_enum=False, length=20), nullable=False
    )
    template_key: Mapped[str] = mapped_column(String(60), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    applied_template_keys: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")


class RoadmapPhase(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_phases"

    roadmap_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmaps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class RoadmapMilestone(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_milestones"

    phase_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmap_phases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Indexed for the scheduler's overdue detector (app/worker/scheduler.py), which
    # filters `due_on < today` on every tick. Migration 0025 creates this index; the
    # `index=True` here keeps the model and schema in sync (alembic drift check).
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[RoadmapStatus] = mapped_column(
        Enum(RoadmapStatus, native_enum=False, length=20),
        default=RoadmapStatus.todo,
        nullable=False,
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_replanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_replan_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class RoadmapTask(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_tasks"

    milestone_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmap_milestones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    effort: Mapped[TaskEffort] = mapped_column(
        Enum(TaskEffort, native_enum=False, length=20),
        default=TaskEffort.medium,
        nullable=False,
    )
    status: Mapped[RoadmapStatus] = mapped_column(
        Enum(RoadmapStatus, native_enum=False, length=20),
        default=RoadmapStatus.todo,
        nullable=False,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RoadmapReplan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "roadmap_replans"

    roadmap_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmaps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    applied_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    change_count: Mapped[int] = mapped_column(Integer, nullable=False)
    changes: Mapped[list] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)


class RoadmapTaskDependency(Base):
    __tablename__ = "roadmap_task_dependencies"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmap_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roadmap_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (
        CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dep_not_self"),
    )
