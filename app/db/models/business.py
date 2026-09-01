import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import CanvasType


class BusinessCanvas(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_canvases"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[CanvasType] = mapped_column(
        Enum(CanvasType, native_enum=False, length=20), nullable=False
    )
    blocks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("startup_id", "type", name="uq_business_canvas_startup_type"),
    )
