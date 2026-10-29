import uuid
from datetime import time
from typing import Any

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class NotificationPreference(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    master_email: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    categories: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    digest_mode: Mapped[str] = mapped_column(String(10), nullable=False, server_default="realtime")
    digest_time: Mapped[time] = mapped_column(Time, nullable=False, server_default="08:00")
    digest_day: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    quiet_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    quiet_from: Mapped[time] = mapped_column(Time, nullable=False, server_default="20:00")
    quiet_until: Mapped[time] = mapped_column(Time, nullable=False, server_default="06:00")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Africa/Lagos")

    __table_args__ = (
        UniqueConstraint("user_id", "startup_id", name="uq_notification_preferences_user_startup"),
    )
