import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    DraftKind,
    DraftStatus,
    EnrollmentStatus,
    SendKind,
    SendStatus,
    SequenceStatus,
    SuppressionReason,
)


def _startup_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _user_fk() -> Mapped[uuid.UUID | None]:
    return mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class EmailSequence(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "email_sequences"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # [{wait_days, subject, body}], 1 to 10 steps; validated on write.
    steps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    stop_on_stage_change: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[SequenceStatus] = mapped_column(
        Enum(SequenceStatus, native_enum=False, length=10),
        nullable=False,
        default=SequenceStatus.active,
    )
    created_by: Mapped[uuid.UUID | None] = _user_fk()


class SequenceEnrollment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sequence_enrollments"
    __table_args__ = (
        # Due-scan: the scheduler walks active rows by next_send_at.
        Index(
            "ix_enrollments_due",
            "next_send_at",
            postgresql_where=text("status = 'active'"),
        ),
        # One live enrollment per (sequence, address): enforced by the database, not just the code.
        Index(
            "uq_enrollment_active_email",
            "sequence_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    sequence_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("email_sequences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sales_leads.id", ondelete="SET NULL"), nullable=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sales_contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)  # lower-cased
    # Merge-tag values frozen at enrolment: a later edit of the lead does not rewrite history.
    first_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    company: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    enrolled_by: Mapped[uuid.UUID | None] = _user_fk()
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[EnrollmentStatus] = mapped_column(
        Enum(EnrollmentStatus, native_enum=False, length=12),
        nullable=False,
        default=EnrollmentStatus.active,
    )
    stopped_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    current_step: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SequenceSend(UUIDMixin, TimestampMixin, Base):
    """One outgoing email (a sequence step or a deal follow-up). Doubles as the reservation that
    makes sending at-most-once, and as the ledger the caps and the auto-pause count."""

    __tablename__ = "sequence_sends"
    __table_args__ = (
        # A step is sent once, whatever races: the second reservation violates this.
        UniqueConstraint("enrollment_id", "step_index", name="uq_send_enrollment_step"),
        Index("ix_sends_reserved", "reserved_at"),
        Index("ix_sends_startup_reserved", "startup_id", "reserved_at"),
        Index("ix_sends_recipient", "startup_id", "recipient_email", "reserved_at"),
    )

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sequence_enrollments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[SendKind] = mapped_column(
        Enum(SendKind, native_enum=False, length=10), nullable=False, default=SendKind.sequence
    )
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deals.id", ondelete="SET NULL"), nullable=True
    )
    sent_by: Mapped[uuid.UUID | None] = _user_fk()
    message_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[SendStatus] = mapped_column(
        Enum(SendStatus, native_enum=False, length=10), nullable=False, default=SendStatus.pending
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    bounce_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SalesSuppression(UUIDMixin, Base):
    """Workspace-wide do-not-email list (D-SAL-8, lead call 2): never crosses workspaces."""

    __tablename__ = "sales_suppressions"
    __table_args__ = (UniqueConstraint("startup_id", "email", name="uq_suppression_startup_email"),)

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)  # lower-cased
    reason: Mapped[SuppressionReason] = mapped_column(
        Enum(SuppressionReason, native_enum=False, length=12), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImapProcessed(UUIDMixin, Base):
    """Bounce mail already handled: (UIDVALIDITY, UID) is the IMAP identity of a message, so a
    re-poll after a crash, or a mailbox that failed to move the mail, never counts it twice."""

    __tablename__ = "sales_imap_processed"
    __table_args__ = (UniqueConstraint("uidvalidity", "uid", name="uq_imap_uidvalidity_uid"),)

    uidvalidity: Mapped[int] = mapped_column(Integer, nullable=False)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SalesDraft(UUIDMixin, TimestampMixin, Base):
    """An AI-written email draft (a sequence step, or a deal follow-up). Appends nothing: the FE
    drops the output into the step, or sends the follow-up once."""

    __tablename__ = "sales_drafts"

    startup_id: Mapped[uuid.UUID] = _startup_fk()
    kind: Mapped[DraftKind] = mapped_column(
        Enum(DraftKind, native_enum=False, length=14), nullable=False
    )
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, native_enum=False, length=10),
        nullable=False,
        default=DraftStatus.generating,
    )
    requested_by: Mapped[uuid.UUID | None] = _user_fk()
    sequence_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("email_sequences.id", ondelete="CASCADE"), nullable=True
    )
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
