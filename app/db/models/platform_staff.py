import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import StaffRole


class PlatformStaff(UUIDMixin, TimestampMixin, Base):
    """Who may use the staff consoles. A table of its own, not a column on `users`, so no
    mass-assignment bug on a user can grant staff power. Read from the DB on every request
    (never from the JWT), so `revoked_at` takes effect on the next call."""

    __tablename__ = "platform_staff"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    role: Mapped[StaffRole] = mapped_column(
        Enum(StaffRole, native_enum=False, length=20), nullable=False
    )
    # Admin scopes: support | content | moderation. Empty for a Super Admin (all scopes).
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
