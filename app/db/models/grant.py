"""Funding Hub slice E (PRD 14.6): grants.

`grants` is the shared catalogue (not tenant data), imported from `content/funding/` and checked
by a human before it ships: every row keeps `verified_on` and `source_url`. `grant_matches` is
what the weekly match job suggests to one workspace, and `grant_applications` is that workspace's
own tracker (a catalogue grant, or one it added by hand).
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin

MATCH_STATES = ("suggested", "dismissed", "not_relevant", "applying")
APPLICATION_STATUSES = ("planning", "drafting", "submitted", "awarded", "rejected", "withdrawn")


class Grant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "grants"

    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    funder: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_min_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    amount_max_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    amount_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    regions: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sectors: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    stages: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    eligibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    verified_on: Mapped[date] = mapped_column(Date, nullable=False)
    verification: Mapped[str] = mapped_column(String(10), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Derived by the importer from the stated text, so the prefilter is plain code.
    country_codes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    pan_africa: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    in_kind: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        CheckConstraint("verification IN ('verified','partial')", name="ck_grant_verification"),
    )


class GrantMatch(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "grant_matches"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    grant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("grants.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 to 100
    why: Mapped[str] = mapped_column(String(300), nullable=False, server_default="")
    ai_scored: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="suggested")
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("startup_id", "grant_id", name="uq_grant_match"),
        CheckConstraint(
            "state IN ('suggested','dismissed','not_relevant','applying')", name="ck_match_state"
        ),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_match_score"),
        Index("ix_grant_matches_startup", "startup_id", "state", "score"),
    )


class GrantApplication(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "grant_applications"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    grant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("grants.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    funder: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="planning")
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('planning','drafting','submitted','awarded','rejected','withdrawn')",
            name="ck_grant_app_status",
        ),
        # One live tracker row per catalogue grant per workspace; manual rows have no grant.
        Index(
            "uq_grant_app_grant",
            "startup_id",
            "grant_id",
            unique=True,
            postgresql_where=text("grant_id IS NOT NULL"),
        ),
        Index("ix_grant_apps_startup", "startup_id", "status"),
    )


class GrantAnswer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "grant_application_answers"

    application_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("grant_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    question: Mapped[str] = mapped_column(String(1000), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ai_drafted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    draft_status: Mapped[str | None] = mapped_column(String(12), nullable=True)  # queued/failed
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "draft_status IS NULL OR draft_status IN ('queued','failed')", name="ck_answer_draft"
        ),
        Index("ix_grant_answers_app", "application_id", "position"),
    )
