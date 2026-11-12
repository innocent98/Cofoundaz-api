"""Funding Hub slice D (PRD 14.5): term sheets. Founder only (D-FUND-5).

A sheet is either typed in by the founder (confirmed on save) or extracted from an uploaded PDF by
a job, which only PROPOSES values: the founder must confirm them before they count (`confirmed_at`).
Flags come from a deterministic rules table (D-FUND-6); the model only words them.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class TermSheet(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "term_sheets"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    investor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("investors.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_files.id", ondelete="SET NULL"), nullable=True
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # extracting -> needs_confirmation -> confirmed (typed sheets are confirmed on creation);
    # failed when extraction could not run.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    terms: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    proposed: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    flags: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    extracted_by_ai: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('extracting','needs_confirmation','confirmed','failed')",
            name="ck_term_sheet_status",
        ),
        CheckConstraint(
            "(status = 'confirmed') = (confirmed_at IS NOT NULL)", name="ck_term_sheet_confirmed"
        ),
        Index("ix_term_sheets_startup", "startup_id", "created_at"),
        Index("ix_term_sheets_investor", "investor_id"),
        Index("ix_term_sheets_sha", "startup_id", "content_sha256"),
    )


class TermSheetAudit(UUIDMixin, TimestampMixin, Base):
    """Before and after of every create, confirm, edit and delete of a sheet. Founder only, so it
    lives here and not in the platform `audit_log`."""

    __tablename__ = "term_sheet_audit"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    term_sheet_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "action IN ('create','confirm','update','delete')", name="ck_term_sheet_audit_action"
        ),
        Index("ix_term_sheet_audit_startup", "startup_id", "created_at"),
    )
