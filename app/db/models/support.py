import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import TicketCategory, TicketPriority, TicketStatus


class SupportTicket(UUIDMixin, TimestampMixin, Base):
    """A founder's request for help. Staff see every ticket; a founder sees only their own
    workspace's (and only the ones they opened, unless they hold the workspace's founder role)."""

    __tablename__ = "support_tickets"

    # Human-readable number ("T-1042"); a sequence so two tickets never share one.
    ticket_no: Mapped[int] = mapped_column(
        Integer, Sequence("support_ticket_no_seq", start=1001), unique=True, nullable=False
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[TicketCategory] = mapped_column(
        Enum(TicketCategory, native_enum=False, length=20), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, native_enum=False, length=4), nullable=False
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, native_enum=False, length=20),
        default=TicketStatus.open,
        nullable=False,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # First-reply clock: `sla_due_at` is when staff must have replied. It restarts on reopen.
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_author: Mapped[str] = mapped_column(String(10), nullable=False)  # founder | staff

    __table_args__ = (
        Index("ix_support_tickets_startup", "startup_id"),
        Index("ix_support_tickets_created_by", "created_by"),
        Index("ix_support_tickets_status_activity", "status", "last_message_at"),
        Index("ix_support_tickets_assigned", "assigned_to"),
    )


class SupportTicketMessage(UUIDMixin, TimestampMixin, Base):
    """A message in the thread the founder can read. Internal notes live in their own table so a
    founder-facing query cannot return one by forgetting a filter."""

    __tablename__ = "support_ticket_messages"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # founder | staff
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_support_ticket_messages_ticket", "ticket_id", "created_at"),)


class SupportTicketNote(UUIDMixin, TimestampMixin, Base):
    """Staff-only note. Never returned by any founder route."""

    __tablename__ = "support_ticket_notes"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_support_ticket_notes_ticket", "ticket_id", "created_at"),)


class SupportCannedResponse(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "support_canned_responses"

    title: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
