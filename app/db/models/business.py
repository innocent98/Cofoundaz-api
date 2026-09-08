import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import CanvasType, RecordKind


class BusinessCanvas(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_canvases"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[CanvasType] = mapped_column(
        Enum(CanvasType, native_enum=False, length=20), nullable=False
    )
    blocks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        UniqueConstraint("startup_id", "type", name="uq_business_canvas_startup_type"),
    )


class BusinessRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_records"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[RecordKind] = mapped_column(
        Enum(RecordKind, native_enum=False, length=20), nullable=False
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_business_records_startup_kind", "startup_id", "kind", "position"),)
