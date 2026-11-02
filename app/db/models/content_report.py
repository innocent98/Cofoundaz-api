import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import ReportItemType, ReportReason, ReportStatus


class ContentReport(UUIDMixin, TimestampMixin, Base):
    """A founder's report of one AI message or comment. `snippet` is a copy of exactly that item,
    taken when the report is filed: staff read it and nothing around it (D-ADM-9), so no staff
    route follows `item_id` back to the chat or thread."""

    __tablename__ = "content_reports"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    item_type: Mapped[ReportItemType] = mapped_column(
        Enum(ReportItemType, native_enum=False, length=20), nullable=False
    )
    # Soft reference (the item may be deleted later; the report keeps its snippet).
    item_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    item_author_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[ReportReason] = mapped_column(
        Enum(ReportReason, native_enum=False, length=20), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, native_enum=False, length=20),
        default=ReportStatus.open,
        nullable=False,
    )
    escalated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_content_reports_status_created", "status", "created_at"),
        Index("ix_content_reports_item", "item_type", "item_id"),
        Index("ix_content_reports_reporter", "reporter_id"),
        # One live report per person per item.
        Index(
            "uq_content_reports_live",
            "reporter_id",
            "item_type",
            "item_id",
            unique=True,
            postgresql_where=text("status IN ('open', 'escalated')"),
        ),
    )
