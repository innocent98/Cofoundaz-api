import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import CourseLevel, EnrichmentStatus, ReviewStatus


def _review_column() -> Mapped[ReviewStatus]:
    return mapped_column(
        Enum(ReviewStatus, native_enum=False, length=12),
        nullable=False,
        default=ReviewStatus.draft,
        server_default=ReviewStatus.draft.value,
    )


class AcademyCourse(TimestampMixin, Base):
    """A course, loaded from ``content/academy/`` by slug (``id`` is the slug)."""

    __tablename__ = "academy_courses"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    outcomes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    level: Mapped[CourseLevel] = mapped_column(
        Enum(CourseLevel, native_enum=False, length=12), nullable=False
    )
    stage_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    gap_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    review_status: Mapped[ReviewStatus] = _review_column()

    lessons: Mapped[list["AcademyLesson"]] = relationship(
        order_by="AcademyLesson.position", cascade="all, delete-orphan", lazy="selectin"
    )


class AcademyLesson(TimestampMixin, Base):
    __tablename__ = "academy_lessons"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    course_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("academy_courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    video_id: Mapped[str | None] = mapped_column(String(11), nullable=True)

    __table_args__ = (UniqueConstraint("course_id", "position", name="uq_academy_lessons_order"),)


class AcademyPath(TimestampMixin, Base):
    __tablename__ = "academy_paths"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    course_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    review_status: Mapped[ReviewStatus] = _review_column()


class AcademyArticle(TimestampMixin, Base):
    __tablename__ = "academy_articles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    read_min: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    review_status: Mapped[ReviewStatus] = _review_column()


class Enrollment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "enrollments"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "user_id", "course_id", name="uq_enrollments_startup_user_course"
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_range"),
    )


class LessonProgress(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "lesson_progress"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[str] = mapped_column(Text, nullable=False)
    lesson_id: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "user_id", "lesson_id", name="uq_lesson_progress_startup_user_lesson"
        ),
    )


class Certificate(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "certificates"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[str] = mapped_column(Text, nullable=False)
    credential_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The holder's name as printed on the PDF, frozen at issue time.
    holder_name: Mapped[str] = mapped_column(Text, nullable=False)
    # Short public credential ID, ``CFZ-XXXX-XXXX``. The only handle the public verify route takes.
    verify_code: Mapped[str] = mapped_column(String(14), nullable=False, unique=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "user_id", "course_id", name="uq_certificates_startup_user_course"
        ),
    )


class CertificateFile(TimestampMixin, Base):
    """The rendered PDF, kept apart from ``certificates`` so list queries never load the bytes."""

    __tablename__ = "certificate_files"

    certificate_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("certificates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class LessonPosition(UUIDMixin, TimestampMixin, Base):
    """Where a learner paused a lesson (seconds), so Continue can resume. Per user."""

    __tablename__ = "lesson_positions"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[str] = mapped_column(Text, nullable=False)
    lesson_id: Mapped[str] = mapped_column(Text, nullable=False)
    position_s: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "user_id", "lesson_id", name="uq_lesson_positions_startup_user_lesson"
        ),
        CheckConstraint("position_s >= 0", name="position_non_negative"),
    )


class LessonNote(UUIDMixin, TimestampMixin, Base):
    """A learner's private note on a lesson. Read and written only by its author."""

    __tablename__ = "lesson_notes"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lesson_id: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "user_id", "lesson_id", name="uq_lesson_notes_startup_user_lesson"
        ),
    )


class LearningRecommendation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "learning_recommendations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # One reason per hero course. ``context_key`` fingerprints the stage and gaps the reason was
    # written for; a different key means the reason is stale and is regenerated.
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    context_key: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[EnrichmentStatus] = mapped_column(
        Enum(EnrichmentStatus, native_enum=False, length=12),
        nullable=False,
        default=EnrichmentStatus.generating,
    )

    __table_args__ = (
        UniqueConstraint("startup_id", "course_id", name="uq_learning_reco_startup_course"),
    )
