import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class ReadinessScore(UUIDMixin, TimestampMixin, Base):
    """The latest Investor Readiness score for a workspace (one row; PRD 15 `readiness_scores`).
    `updated_at` is the compute time: the 15 minute throttle reads it."""

    __tablename__ = "readiness_scores"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    must_haves: Mapped[list] = mapped_column(JSONB, nullable=False)
    gaps: Mapped[list] = mapped_column(JSONB, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Last "your score improved" notification (at most one a day, on a band change or +5).
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReadinessScoreHistory(UUIDMixin, TimestampMixin, Base):
    """One point per change of score, dimension scores or cap state."""

    __tablename__ = "readiness_score_history"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dimension_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("ix_readiness_history_startup_created", "startup_id", "created_at"),)


class ReadinessInput(UUIDMixin, TimestampMixin, Base):
    """Founder-entered market size (a claim, so it only scores with a source URL)."""

    __tablename__ = "readiness_inputs"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tam_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sam_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    som_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DeckReview(UUIDMixin, TimestampMixin, Base):
    """One review of one pitch deck PDF (PRD 15.2). `version` counts reviews per workspace, so
    "Re-review new version" is review N+1; `content_sha256` stops the same bytes spending tokens
    twice. `status` is queued | running | ready | failed (`error` says why)."""

    __tablename__ = "deck_reviews"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_files.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    slide_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    top_fixes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    slides: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("startup_id", "version", name="uq_deck_reviews_startup_version"),
        Index("ix_deck_reviews_startup_sha", "startup_id", "content_sha256"),
    )


class MockSession(UUIDMixin, TimestampMixin, Base):
    """One Mock Q&A session (PRD 15.3, decision D-IR-7). `status` is active | ending | scored |
    failed | abandoned. `context` and `transcript` hold the founder's own words and numbers, so
    they are cleared after 90 days (`purged_at`); the scorecard is kept."""

    __tablename__ = "mock_sessions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    focus: Mapped[str] = mapped_column(String(20), nullable=False)
    intensity: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    transcript: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    scorecard: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rating: Mapped[str | None] = mapped_column(String(12), nullable=True)
    average: Mapped[float | None] = mapped_column(Float, nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_mock_sessions_startup_started", "startup_id", "started_at"),)


class ReadinessNarrative(UUIDMixin, TimestampMixin, Base):
    """The founder's financial narrative draft (PRD 15.4): one editable text per workspace."""

    __tablename__ = "readiness_narrative"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class NarrativeCheck(UUIDMixin, TimestampMixin, Base):
    """One "check my narrative against my numbers" run (PRD 15.4, slice C). The model only extracts
    `claims`; `facts` are read from Finance and Funding and `findings` are computed in code, so the
    model never judges a number. `status` is queued | running | ready | failed (`error` says why).
    """

    __tablename__ = "narrative_checks"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False
    )
    deck_review_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deck_reviews.id", ondelete="SET NULL"), nullable=True
    )
    draft_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    claims: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    use_of_funds: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    facts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    findings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_narrative_checks_startup_created", "startup_id", "created_at"),)
