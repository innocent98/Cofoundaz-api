import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDMixin
from app.db.models.enums import BusinessModel, StartupStage


class Startup(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "startups"

    name: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    website: Mapped[str | None] = mapped_column(String, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    business_model: Mapped[BusinessModel | None] = mapped_column(
        Enum(BusinessModel, native_enum=False, length=20), nullable=True
    )
    stage: Mapped[StartupStage | None] = mapped_column(
        Enum(StartupStage, native_enum=False, length=20), nullable=True
    )
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    profile: Mapped["StartupProfile"] = relationship(
        back_populates="startup", uselist=False, cascade="all, delete-orphan"
    )


class StartupProfile(TimestampMixin, Base):
    __tablename__ = "startup_profiles"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), primary_key=True
    )
    goals: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assessment_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ai_panel: Mapped[str | None] = mapped_column(Text, nullable=True)

    startup: Mapped["Startup"] = relationship(back_populates="profile")
