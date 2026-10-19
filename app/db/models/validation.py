import datetime
import uuid
from typing import Any

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    RiskLevel,
    SurveyStatus,
)


class Assumption(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "assumptions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=20), nullable=False
    )
    status: Mapped[AssumptionStatus] = mapped_column(
        Enum(AssumptionStatus, native_enum=False, length=20),
        nullable=False,
        server_default=AssumptionStatus.untested.value,
    )


class Experiment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "experiments"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[ExperimentType] = mapped_column(
        Enum(ExperimentType, native_enum=False, length=20), nullable=False
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus, native_enum=False, length=20),
        nullable=False,
        server_default=ExperimentStatus.draft.value,
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    assumption_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # D-VAL-1: a smoke test's public page lives at /p/{slug}. Globally unique (it is a URL),
    # copied from config["slug"] on save. Null until the founder picks one.
    slug: Mapped[str | None] = mapped_column(String(60), nullable=True, unique=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SmokeTestEvent(UUIDMixin, TimestampMixin, Base):
    """One thing a visitor did on a hosted smoke-test page: a visit, a CTA or preorder click, or
    an email signup. Nothing about the visitor is stored except the email they typed (signups
    only) and a SHA-256 of the anonymous visitor id the page generates, used to count people
    rather than page loads."""

    __tablename__ = "smoke_test_events"
    __table_args__ = (
        Index(
            "uq_smoke_signup_email",
            "experiment_id",
            "email",
            unique=True,
            postgresql_where=text("kind = 'signup'"),
        ),
        Index("ix_smoke_events_experiment_kind", "experiment_id", "kind"),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    visitor_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)


class SmokeTestSlug(Base):
    """Every slug a workspace has ever claimed stays reserved to that workspace, so deleting or
    renaming a test never lets another workspace take over its public URL (and the waitlist
    emails of anyone still following an old link)."""

    __tablename__ = "smoke_test_slugs"

    slug: Mapped[str] = mapped_column(String(60), primary_key=True)
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Interview(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "interviews"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interviewee: Mapped[str] = mapped_column(String(255), nullable=False)
    segment: Mapped[str | None] = mapped_column(String(120), nullable=True)
    held_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    key_quotes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    verdict: Mapped[InterviewVerdict] = mapped_column(
        Enum(InterviewVerdict, native_enum=False, length=20), nullable=False
    )
    assumption_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )


class Survey(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "surveys"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    questions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    status: Mapped[SurveyStatus] = mapped_column(
        Enum(SurveyStatus, native_enum=False, length=20),
        nullable=False,
        server_default=SurveyStatus.draft.value,
    )
    # SHA-256 of the public token, set the first time the survey is opened. The raw token is
    # returned once and never stored (spec section 4).
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)


class SurveyResponse(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "survey_responses"

    survey_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("surveys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Copied from the survey so member reads stay workspace-scoped; never supplied by the
    # respondent (spec decision D4).
    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    submitted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
