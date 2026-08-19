import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import AssessmentStatus, AssessmentType


class Assessment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "assessments"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[AssessmentType] = mapped_column(
        Enum(AssessmentType, native_enum=False, length=20), nullable=False
    )
    status: Mapped[AssessmentStatus] = mapped_column(
        Enum(AssessmentStatus, native_enum=False, length=20),
        default=AssessmentStatus.in_progress,
        nullable=False,
    )
    bank_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_assessments_startup_in_progress",
            "startup_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
    )


class AssessmentAnswer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "assessment_answers"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("assessments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("assessment_id", "question_key", name="uq_answer_assessment_question"),
    )


class AssessmentResult(TimestampMixin, Base):
    __tablename__ = "assessment_results"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), primary_key=True
    )
    dimension_scores: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    overall_provisional: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
