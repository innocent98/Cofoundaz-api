import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    BusinessPlanStatus,
    CanvasType,
    RecordKind,
    SuggestionOp,
    SuggestionStatus,
)


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


class BusinessSuggestion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_suggestions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    op: Mapped[SuggestionOp] = mapped_column(
        Enum(SuggestionOp, native_enum=False, length=20), nullable=False
    )
    target: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, native_enum=False, length=20),
        nullable=False,
        server_default="pending",
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_business_suggestions_startup_status", "startup_id", "status"),)


class BusinessPositioningMap(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_positioning_maps"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    axes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"x": {"label": "Price", "low": "Low", "high": "High"}, '
            '"y": {"label": "Quality", "low": "Low", "high": "High"}}\'::jsonb'
        ),
    )

    __table_args__ = (UniqueConstraint("startup_id", name="uq_business_positioning_map_startup"),)


class BusinessPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_plans"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[BusinessPlanStatus] = mapped_column(
        Enum(BusinessPlanStatus, native_enum=False, length=20), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
