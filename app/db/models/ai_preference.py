import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class AiPreference(UUIDMixin, TimestampMixin, Base):
    """Settings → AI preferences (PRD 03.5): one row per member per workspace, created on first save."""

    __tablename__ = "ai_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proactive_suggestions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    daily_briefing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    tone: Mapped[str] = mapped_column(String(20), nullable=False, server_default="encouraging")

    __table_args__ = (
        UniqueConstraint("user_id", "startup_id", name="uq_ai_preferences_user_startup"),
    )
