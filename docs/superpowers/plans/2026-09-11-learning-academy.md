# Learning Academy (Module 17) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Module 17 — a founders-and-team-members learning hub: a labelled placeholder in-code catalog of courses, lessons, paths and articles; per-person, per-workspace enrolments and lesson progress; race-safe automatic enrolment; certificates on course completion; and a deterministic recommendations shelf with continue watching.

**Architecture:** Content lives in a read-only, versioned, in-code registry (`app/services/learning/catalog.py`), normalized so it can move to tables when Module 25.4 lands. Only people-related state is in Postgres — `enrollments`, `lesson_progress`, `certificates` — keyed by text catalog ids and scoped by `startup_id` + `user_id`. Course % is derived from completed lessons and written on each completion under a row lock; path % is the mean of course percentages. Completion issues a certificate, publishes `learning.course.completed`, and enqueues `learning.certificate.generate`.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Alembic, PostgreSQL, Pydantic v2, pytest (real Postgres, per-test rollback).

**Spec:** `docs/superpowers/specs/2026-09-11-learning-academy-design.md`

## Global Constraints

- **Enum:** `CourseLevel` is an `enum.StrEnum` in `app/db/models/enums.py`. It types catalog data only; no column uses it.
- **FK index convention:** every FK column gets a standalone `index=True`.
- **`get_db()` does not auto-commit:** the `POST` and `PATCH` endpoints MUST call `db.commit()`. Services only `flush()`.
- **Response envelope:** endpoints return `success_response(data)`; errors raise `AppError` subclasses.
- **Access (spec D6):** every route, reads included, depends on `require_role(MembershipRole.founder, MembershipRole.team_member)`. No route uses `require_workspace` alone.
- **Tenancy (spec D7):** every query filters on both `startup_id` and `user_id`, taken from the caller's `membership`.
- **Catalog ids are `str` path params**, not UUIDs. An unknown id raises `NotFound` (404) from the catalog lookup.
- **Race safety (spec D9):** enrolment is a get-or-create inside a SAVEPOINT with a re-select on `IntegrityError`, as in `get_or_create_canvas`. The progress roll-up takes `SELECT … FOR UPDATE` on the enrolment row, so concurrent completions in one course roll up one at a time and never store a stale percentage.
- **Progress formula (spec §5):** integers 0–100 using Python's `round()` (exact halves round to even), as `recompute_milestone_progress` does.
- **Placeholder content (spec D8):** every course, lesson, path and article title starts with `[Placeholder] `.
- **Migrations:** produced by `alembic revision --autogenerate` against the ORM models; `0019_learning` with `down_revision = "0018_document_shares"`, re-pointed if `develop` has moved; single head; `alembic check` zero drift before push.
- **No AI attribution** in any commit message or PR/issue/review body.
- **CI green locally before push:** `poetry run ruff check app tests`, `poetry run black --check app tests`, `poetry run mypy app`, `poetry run pytest`, then `scripts/e2e_run.sh`.

---

### Task 1: Schema — `CourseLevel`, three models, migration `0019_learning`

**Files:**
- Modify: `app/db/models/enums.py` (add `CourseLevel`)
- Create: `app/db/models/learning.py` (`Enrollment`, `LessonProgress`, `Certificate`)
- Modify: `app/db/models/__init__.py` (register the models)
- Create: `alembic/versions/0019_learning.py`
- Test: `tests/db/test_learning_models.py`, `tests/test_learning_migration.py`

**Interfaces:**
- Produces: `CourseLevel` (`beginner`/`intermediate`/`advanced`); ORM `Enrollment` (`id, created_at, updated_at, startup_id, user_id, course_id, progress, completed_at`), `LessonProgress` (`id, created_at, updated_at, startup_id, user_id, course_id, lesson_id, completed_at`), `Certificate` (`id, created_at, updated_at, startup_id, user_id, course_id, credential_code, issued_at`).

**Migration numbering — settle against the live head:** before writing the migration, run
`git fetch origin && poetry run alembic heads`. If the head is still `0018_document_shares`, use
`0019_learning`; otherwise use the next number after whatever head exists and chain onto it.
`poetry run alembic heads` MUST show exactly one head afterwards.

- [ ] **Step 1: Add the enum**

Append to `app/db/models/enums.py`:

```python
class CourseLevel(enum.StrEnum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"
```

- [ ] **Step 2: Create the models**

`app/db/models/learning.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


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
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "user_id", "course_id", name="uq_certificates_startup_user_course"
        ),
    )
```

The `CheckConstraint` passes the short name `progress_range`; the naming convention in
`app/db/base.py` builds `ck_enrollments_progress_range`. Passing the full name doubles the prefix.

- [ ] **Step 3: Register the models**

In `app/db/models/__init__.py`, between the `journal` and `membership` imports:

```python
from app.db.models.learning import Certificate, Enrollment, LessonProgress  # noqa: F401
```

- [ ] **Step 4: Autogenerate + number the migration**

```bash
poetry run alembic upgrade head
poetry run alembic revision --autogenerate -m "learning"
```

Rename the generated file per the numbering rule above and set `revision`/`down_revision`.
Confirm `upgrade()` creates `enrollments`, `lesson_progress` and `certificates` with:

- `ix_<table>_startup_id` and `ix_<table>_user_id` on all three tables
- `uq_enrollments_startup_user_course`, `uq_lesson_progress_startup_user_lesson`,
  `uq_certificates_startup_user_course`, and `uq_certificates_credential_code`
- `ck_enrollments_progress_range` — if autogenerate omitted it, add
  `sa.CheckConstraint("progress BETWEEN 0 AND 100", name=op.f("ck_enrollments_progress_range"))`
  to the `enrollments` table by hand
- `server_default=sa.text("now()")` on every `created_at`/`updated_at`, and `server_default="0"`
  on `progress`
- `ondelete="CASCADE"` on all six foreign keys

Add a short module docstring (mirror `0018_document_shares.py`).

- [ ] **Step 5: Verify single head + zero drift**

```bash
poetry run alembic heads          # exactly one head
poetry run alembic upgrade head   # applies clean
poetry run alembic check          # "No new upgrade operations detected."
```

- [ ] **Step 6: Write the model tests**

```python
# tests/db/test_learning_models.py
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.learning import Certificate, Enrollment, LessonProgress
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    return u, s


def test_enrollment_defaults(db):
    u, s = _ctx(db)
    enrollment = Enrollment(startup_id=s.id, user_id=u.id, course_id="c")
    db.add(enrollment)
    db.flush()
    db.refresh(enrollment)
    assert enrollment.progress == 0
    assert enrollment.completed_at is None


def test_enrollment_is_unique_per_workspace_user_and_course(db):
    u, s = _ctx(db)
    db.add(Enrollment(startup_id=s.id, user_id=u.id, course_id="c"))
    db.flush()
    db.add(Enrollment(startup_id=s.id, user_id=u.id, course_id="c"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_same_person_can_enrol_in_the_same_course_in_two_workspaces(db):
    u, first = _ctx(db)
    second = create_startup(db, owner=u, name="Second")
    db.add(Enrollment(startup_id=first.id, user_id=u.id, course_id="c"))
    db.add(Enrollment(startup_id=second.id, user_id=u.id, course_id="c"))
    db.flush()


def test_enrollment_progress_must_be_between_0_and_100(db):
    u, s = _ctx(db)
    db.add(Enrollment(startup_id=s.id, user_id=u.id, course_id="c", progress=101))
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_lesson_is_completed_once_per_person_per_workspace(db):
    u, s = _ctx(db)
    now = datetime.now(UTC)
    db.add(
        LessonProgress(
            startup_id=s.id, user_id=u.id, course_id="c", lesson_id="c-1", completed_at=now
        )
    )
    db.flush()
    db.add(
        LessonProgress(
            startup_id=s.id, user_id=u.id, course_id="c", lesson_id="c-1", completed_at=now
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_one_certificate_per_course_per_person_per_workspace(db):
    u, s = _ctx(db)
    now = datetime.now(UTC)
    db.add(
        Certificate(
            startup_id=s.id, user_id=u.id, course_id="c", credential_code="a", issued_at=now
        )
    )
    db.flush()
    db.add(
        Certificate(
            startup_id=s.id, user_id=u.id, course_id="c", credential_code="b", issued_at=now
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_credential_codes_are_unique(db):
    u, s = _ctx(db)
    now = datetime.now(UTC)
    db.add(
        Certificate(
            startup_id=s.id, user_id=u.id, course_id="c1", credential_code="same", issued_at=now
        )
    )
    db.flush()
    db.add(
        Certificate(
            startup_id=s.id, user_id=u.id, course_id="c2", credential_code="same", issued_at=now
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
```

- [ ] **Step 7: Write the migration test**

```python
# tests/test_learning_migration.py
import subprocess


def test_learning_migration_applies():
    r = subprocess.run(
        ["poetry", "run", "alembic", "upgrade", "head"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    chk = subprocess.run(
        [
            "poetry",
            "run",
            "python",
            "-c",
            "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
            "e=create_engine(settings.DATABASE_URL); i=inspect(e); n=set(i.get_table_names()); "
            "assert {'enrollments','lesson_progress','certificates'} <= n, n; "
            "uq={x['name'] for x in i.get_unique_constraints('enrollments')}; "
            "assert 'uq_enrollments_startup_user_course' in uq, uq; "
            "uq={x['name'] for x in i.get_unique_constraints('lesson_progress')}; "
            "assert 'uq_lesson_progress_startup_user_lesson' in uq, uq; "
            "uq={x['name'] for x in i.get_unique_constraints('certificates')}; "
            "assert 'uq_certificates_startup_user_course' in uq, uq; "
            "assert 'uq_certificates_credential_code' in uq, uq; "
            "ck={x['name'] for x in i.get_check_constraints('enrollments')}; "
            "assert 'ck_enrollments_progress_range' in ck, ck; "
            "print('ok')",
        ],
        capture_output=True,
        text=True,
    )
    assert chk.returncode == 0, chk.stderr


def test_exactly_one_alembic_head():
    r = subprocess.run(["poetry", "run", "alembic", "heads"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("(head)") == 1, r.stdout
```

- [ ] **Step 8: Run the tests**

Run: `poetry run pytest tests/db/test_learning_models.py tests/test_learning_migration.py -v`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add app/db/models/enums.py app/db/models/learning.py app/db/models/__init__.py alembic/versions/ tests/db/test_learning_models.py tests/test_learning_migration.py
git commit -m "feat(learning): enrolments, lesson progress and certificates tables"
```

---

### Task 2: Catalog — placeholder in-code registry

**Files:**
- Create: `app/services/learning/__init__.py` (empty)
- Create: `app/services/learning/catalog.py`
- Create: `tests/services/learning/__init__.py` (empty)
- Test: `tests/services/learning/test_catalog.py`

**Interfaces:**
- Consumes: `CourseLevel` (Task 1), `StartupStage`, `NotFound`.
- Produces: frozen dataclasses `Lesson`, `Course`, `LearningPath`, `Article`; `LEARNING_CATALOG_VERSION`, `PLACEHOLDER_PREFIX`; ordered tuples `ALL_COURSES`, `ALL_PATHS`, `ALL_ARTICLES` (tuple order is catalog order); lookups `COURSES`, `PATHS`, `ARTICLES`, `LESSONS` (`lesson_id -> (Course, Lesson)`); `get_course(course_id) -> Course`, `get_lesson(lesson_id) -> tuple[Course, Lesson]` (both 404 on unknown); `course_duration_min(course) -> int`, `path_duration_min(path) -> int`.

- [ ] **Step 1: Create the two empty packages**

Create empty `app/services/learning/__init__.py` and `tests/services/learning/__init__.py`.

- [ ] **Step 2: Write failing catalog tests**

```python
# tests/services/learning/test_catalog.py
import pytest

from app.core.errors import NotFound
from app.db.models.enums import CourseLevel, StartupStage
from app.services.learning.catalog import (
    ALL_ARTICLES,
    ALL_COURSES,
    ALL_PATHS,
    ARTICLES,
    COURSES,
    LEARNING_CATALOG_VERSION,
    LESSONS,
    PATHS,
    PLACEHOLDER_PREFIX,
    course_duration_min,
    get_course,
    get_lesson,
    path_duration_min,
)


def test_catalog_is_versioned():
    assert LEARNING_CATALOG_VERSION == 1


def test_ids_are_unique_and_lesson_ids_are_unique_catalog_wide():
    assert len(COURSES) == len(ALL_COURSES)
    assert len(PATHS) == len(ALL_PATHS)
    assert len(ARTICLES) == len(ALL_ARTICLES)
    assert len(LESSONS) == sum(len(course.lessons) for course in ALL_COURSES)


def test_every_course_has_ordered_lessons_and_every_path_has_known_courses():
    for course in ALL_COURSES:
        assert course.lessons
        assert [lesson.order for lesson in course.lessons] == list(
            range(1, len(course.lessons) + 1)
        )
    for path in ALL_PATHS:
        assert path.course_ids
        assert all(course_id in COURSES for course_id in path.course_ids)


def test_content_is_labelled_placeholder_and_realistic_in_shape():
    titles = (
        [course.title for course in ALL_COURSES]
        + [lesson.title for course in ALL_COURSES for lesson in course.lessons]
        + [path.title for path in ALL_PATHS]
        + [article.title for article in ALL_ARTICLES]
    )
    assert all(title.startswith(PLACEHOLDER_PREFIX) for title in titles)
    assert {stage for course in ALL_COURSES for stage in course.stage_tags} == set(StartupStage)
    assert len({course.level for course in ALL_COURSES}) > 1
    assert any(len(path.course_ids) >= 2 for path in ALL_PATHS)
    assert len(ALL_ARTICLES) >= 2


def test_lookups_and_unknown_ids_404():
    assert get_course("idea-shape-the-problem").level == CourseLevel.beginner
    course, lesson = get_lesson("idea-shape-the-problem-1")
    assert course.id == "idea-shape-the-problem" and lesson.order == 1
    with pytest.raises(NotFound):
        get_course("nope")
    with pytest.raises(NotFound):
        get_lesson("nope")


def test_durations_are_derived():
    course = get_course("idea-shape-the-problem")
    assert course_duration_min(course) == sum(lesson.duration_min for lesson in course.lessons)
    path = PATHS["path-validation-foundations"]
    assert path_duration_min(path) == sum(
        course_duration_min(COURSES[course_id]) for course_id in path.course_ids
    )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/learning/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.learning.catalog'`.

- [ ] **Step 4: Create the catalog**

`app/services/learning/catalog.py`:

```python
"""Learning Academy catalog (Module 17) -- read-only, versioned, in-code.

ALL CONTENT IN THIS FILE IS PLACEHOLDER. Every course, lesson, path and article title starts with
``[Placeholder] ``. Replacing it with real content is a required pre-go-live task (spec D8).

The shape is normalized so it maps one-to-one onto tables when the Module 25.4 CMS moves the
catalog into the database: courses own their ordered lessons, and paths reference courses by id
in order rather than copying them (spec D1).

Ids are stable. Enrolment, lesson-progress and certificate rows store them, so never rename an id
that anything references. Lesson ids are unique across the whole catalog.
"""

from dataclasses import dataclass

from app.core.errors import NotFound
from app.db.models.enums import CourseLevel, StartupStage

LEARNING_CATALOG_VERSION = 1
PLACEHOLDER_PREFIX = "[Placeholder] "


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    order: int
    video_ref: str
    duration_min: int
    transcript: str


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    level: CourseLevel
    stage_tags: tuple[StartupStage, ...]
    lessons: tuple[Lesson, ...]


@dataclass(frozen=True)
class LearningPath:
    id: str
    title: str
    stage: StartupStage
    course_ids: tuple[str, ...]


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    tags: tuple[str, ...]
    body: str


def _course(
    course_id: str,
    title: str,
    level: CourseLevel,
    stage_tags: tuple[StartupStage, ...],
    lessons: tuple[tuple[str, int], ...],
) -> Course:
    """Build a placeholder course; lesson ids are ``<course_id>-<n>``, unique catalog-wide."""
    return Course(
        id=course_id,
        title=PLACEHOLDER_PREFIX + title,
        level=level,
        stage_tags=stage_tags,
        lessons=tuple(
            Lesson(
                id=f"{course_id}-{n}",
                title=PLACEHOLDER_PREFIX + lesson_title,
                order=n,
                video_ref=f"placeholder://{course_id}/{n}",
                duration_min=duration_min,
                transcript="Placeholder transcript.",
            )
            for n, (lesson_title, duration_min) in enumerate(lessons, start=1)
        ),
    )


ALL_COURSES: tuple[Course, ...] = (
    _course(
        "idea-shape-the-problem",
        "Shape the problem",
        CourseLevel.beginner,
        (StartupStage.idea, StartupStage.validation),
        (
            ("Write a problem statement", 8),
            ("Find who has the problem", 10),
            ("Size the pain", 12),
        ),
    ),
    _course(
        "validation-talk-to-customers",
        "Talk to customers",
        CourseLevel.beginner,
        (StartupStage.validation,),
        (
            ("Recruit interviewees", 9),
            ("Run the interview", 14),
            ("Synthesize what you heard", 11),
        ),
    ),
    _course(
        "build-scope-the-mvp",
        "Scope the MVP",
        CourseLevel.intermediate,
        (StartupStage.validation, StartupStage.build),
        (
            ("Pick the one core flow", 12),
            ("Cut everything else", 10),
        ),
    ),
    _course(
        "launch-plan-go-to-market",
        "Plan your go-to-market",
        CourseLevel.intermediate,
        (StartupStage.launch,),
        (
            ("Choose launch channels", 11),
            ("Write launch messaging", 9),
        ),
    ),
    _course(
        "growth-find-a-channel",
        "Find a repeatable channel",
        CourseLevel.advanced,
        (StartupStage.growth,),
        (
            ("Test three channels", 13),
            ("Double down on the winner", 10),
        ),
    ),
    _course(
        "scale-build-the-org",
        "Build the org",
        CourseLevel.advanced,
        (StartupStage.scale,),
        (
            ("Document core processes", 12),
            ("Hire against the plan", 14),
        ),
    ),
)

ALL_PATHS: tuple[LearningPath, ...] = (
    LearningPath(
        id="path-validation-foundations",
        title=PLACEHOLDER_PREFIX + "Validation foundations",
        stage=StartupStage.validation,
        course_ids=(
            "idea-shape-the-problem",
            "validation-talk-to-customers",
            "build-scope-the-mvp",
        ),
    ),
    LearningPath(
        id="path-launch-to-growth",
        title=PLACEHOLDER_PREFIX + "From launch to growth",
        stage=StartupStage.launch,
        course_ids=("launch-plan-go-to-market", "growth-find-a-channel"),
    ),
)

ALL_ARTICLES: tuple[Article, ...] = (
    Article(
        id="article-interview-questions",
        title=PLACEHOLDER_PREFIX + "Questions that get honest answers",
        tags=("validation", "customers"),
        body="Placeholder article body.",
    ),
    Article(
        id="article-pricing-basics",
        title=PLACEHOLDER_PREFIX + "Pricing basics",
        tags=("pricing", "validation"),
        body="Placeholder article body.",
    ),
)

COURSES: dict[str, Course] = {course.id: course for course in ALL_COURSES}
PATHS: dict[str, LearningPath] = {path.id: path for path in ALL_PATHS}
ARTICLES: dict[str, Article] = {article.id: article for article in ALL_ARTICLES}
LESSONS: dict[str, tuple[Course, Lesson]] = {
    lesson.id: (course, lesson) for course in ALL_COURSES for lesson in course.lessons
}


def get_course(course_id: str) -> Course:
    course = COURSES.get(course_id)
    if course is None:
        raise NotFound()
    return course


def get_lesson(lesson_id: str) -> tuple[Course, Lesson]:
    found = LESSONS.get(lesson_id)
    if found is None:
        raise NotFound()
    return found


def course_duration_min(course: Course) -> int:
    return sum(lesson.duration_min for lesson in course.lessons)


def path_duration_min(path: LearningPath) -> int:
    return sum(course_duration_min(COURSES[course_id]) for course_id in path.course_ids)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/services/learning/test_catalog.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/services/learning/ tests/services/learning/
git commit -m "feat(learning): placeholder in-code course catalog"
```

---
### Task 3: Service — enrolment, lesson completion, progress, certificates

**Files:**
- Create: `app/services/learning/service.py`
- Test: `tests/services/learning/test_service.py`, `tests/services/learning/test_concurrency.py`

**Interfaces:**
- Consumes: `Enrollment`, `LessonProgress`, `Certificate` (Task 1); `Course`, `get_course`, `get_lesson` (Task 2); `event_bus`, `job_dispatcher`.
- Produces:
  - `get_or_create_enrollment(db, startup_id, user_id, course_id) -> tuple[Enrollment, bool]` — `(row, created)`; 404 on unknown course; race-safe
  - `course_progress(db, startup_id, user_id, course) -> int`
  - `complete_lesson(db, startup_id, user_id, lesson_id) -> tuple[Enrollment, Certificate | None]` — auto-enrols; the certificate is returned only when this call reached 100%
  - `list_certificates(db, startup_id, user_id) -> list[Certificate]` — newest first
  - `serialize_enrollment(enrollment) -> dict`, `serialize_certificate(cert) -> dict`

- [ ] **Step 1: Write failing service tests**

```python
# tests/services/learning/test_service.py
import pytest

from app.core.errors import NotFound
from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.learning import Certificate, Enrollment, LessonProgress
from app.services.learning.catalog import COURSES
from app.services.learning.service import (
    complete_lesson,
    course_progress,
    get_or_create_enrollment,
    list_certificates,
    serialize_certificate,
    serialize_enrollment,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    db.flush()
    return u, s


def _finish(db, s, u, course_id):
    """Complete every lesson of a course; return the certificate from each call."""
    return [
        complete_lesson(db, s.id, u.id, lesson.id)[1] for lesson in COURSES[course_id].lessons
    ]


def test_enrol_is_idempotent(db):
    u, s = _ctx(db)
    first, created = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    again, created_again = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    assert created is True and created_again is False and again.id == first.id
    assert db.query(Enrollment).filter_by(startup_id=s.id, user_id=u.id).count() == 1


def test_enrol_in_unknown_course_404(db):
    u, s = _ctx(db)
    with pytest.raises(NotFound):
        get_or_create_enrollment(db, s.id, u.id, "nope")


def test_completing_a_lesson_auto_enrols(db):
    u, s = _ctx(db)
    enrollment, cert = complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    assert enrollment.course_id == "idea-shape-the-problem"
    assert enrollment.progress == 33 and cert is None
    assert db.query(Enrollment).filter_by(startup_id=s.id, user_id=u.id).count() == 1


def test_completing_the_same_lesson_twice_changes_nothing(db):
    u, s = _ctx(db)
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    enrollment, cert = complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    assert enrollment.progress == 33 and cert is None
    assert db.query(LessonProgress).filter_by(startup_id=s.id, user_id=u.id).count() == 1


def test_completing_an_unknown_lesson_404(db):
    u, s = _ctx(db)
    with pytest.raises(NotFound):
        complete_lesson(db, s.id, u.id, "nope")


def test_course_percentage_is_rounded_completed_over_total(db):
    u, s = _ctx(db)
    course = COURSES["idea-shape-the-problem"]  # 3 lessons
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    assert course_progress(db, s.id, u.id, course) == 33
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-2")
    assert course_progress(db, s.id, u.id, course) == 67


def test_finishing_a_course_issues_one_certificate_event_and_job(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.learning.service.event_bus.publish",
        lambda e, p: events.append((e, p)),
    )
    u, s = _ctx(db)
    certs = _finish(db, s, u, "build-scope-the-mvp")  # 2 lessons
    assert certs[0] is None and certs[1] is not None
    cert = certs[1]

    enrollment = db.query(Enrollment).filter_by(startup_id=s.id, user_id=u.id).one()
    assert enrollment.progress == 100 and enrollment.completed_at is not None
    assert events == [
        (
            "learning.course.completed",
            {
                "startup_id": str(s.id),
                "user_id": str(u.id),
                "course_id": "build-scope-the-mvp",
                "certificate_id": str(cert.id),
            },
        )
    ]
    jobs = db.query(Job).filter_by(type="learning.certificate.generate", startup_id=s.id).all()
    assert [job.payload for job in jobs] == [{"certificate_id": str(cert.id)}]

    # A repeat completion on an already-finished course issues nothing new.
    _enrollment, again = complete_lesson(db, s.id, u.id, "build-scope-the-mvp-2")
    assert again is None
    assert db.query(Certificate).filter_by(startup_id=s.id, user_id=u.id).count() == 1
    assert len(events) == 1
    job_count = (
        db.query(Job).filter_by(type="learning.certificate.generate", startup_id=s.id).count()
    )
    assert job_count == 1


def test_credential_codes_are_unique_and_unguessable(db):
    u, s = _ctx(db)
    first = _finish(db, s, u, "build-scope-the-mvp")[-1]
    second = _finish(db, s, u, "launch-plan-go-to-market")[-1]
    assert first.credential_code != second.credential_code
    assert len(first.credential_code) >= 20  # secrets.token_urlsafe(16) -> 22 characters


def test_certificates_are_listed_newest_first_and_serialized(db):
    u, s = _ctx(db)
    older = _finish(db, s, u, "build-scope-the-mvp")[-1]
    newer = _finish(db, s, u, "launch-plan-go-to-market")[-1]
    assert [c.id for c in list_certificates(db, s.id, u.id)] == [newer.id, older.id]
    out = serialize_certificate(newer)
    assert out["id"] == str(newer.id)
    assert out["course_id"] == "launch-plan-go-to-market"
    assert out["credential_code"] == newer.credential_code


def test_progress_is_per_person(db):
    u, s = _ctx(db)
    teammate = create_user(db)
    create_membership(db, teammate, s, role=MembershipRole.team_member)
    db.flush()
    complete_lesson(db, s.id, teammate.id, "idea-shape-the-problem-1")
    assert course_progress(db, s.id, u.id, COURSES["idea-shape-the-problem"]) == 0
    assert list_certificates(db, s.id, u.id) == []


def test_same_person_progresses_separately_in_two_workspaces(db):
    u, first = _ctx(db)
    second = create_startup(db, owner=u, name="Second")
    create_membership(db, u, second)
    db.flush()
    complete_lesson(db, first.id, u.id, "idea-shape-the-problem-1")
    course = COURSES["idea-shape-the-problem"]
    assert course_progress(db, first.id, u.id, course) == 33
    assert course_progress(db, second.id, u.id, course) == 0


def test_serialize_enrollment(db):
    u, s = _ctx(db)
    enrollment, _created = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    assert serialize_enrollment(enrollment) == {
        "course_id": "idea-shape-the-problem",
        "progress": 0,
        "completed_at": None,
    }
```

- [ ] **Step 2: Write failing concurrency tests**

```python
# tests/services/learning/test_concurrency.py
"""Concurrency regression tests for Module 17's write paths.

Same rationale as tests/services/business/test_concurrency.py: the per-test `db` fixture wraps
everything in one savepoint that never commits, so it cannot reproduce a cross-connection race.
These tests open real, committing `Session`s against the session-scoped `engine` fixture.

Targets:
- `get_or_create_enrollment`: two concurrent enrols produce one row (SAVEPOINT + re-select).
- `complete_lesson` on the same lesson twice: one lesson_progress row and one enrolment.
- `complete_lesson` on two different lessons of an unenrolled two-lesson course: one enrolment,
  both lessons recorded, progress 100 and exactly one certificate. The enrolment row lock makes
  the roll-up serial, so neither caller stores a stale percentage.
"""

import threading
import uuid
from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.job import Job
from app.db.models.learning import Certificate, Enrollment, LessonProgress
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.learning.service import complete_lesson, get_or_create_enrollment
from tests.factories import create_startup, create_user


def _setup(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    setup = Session(bind=engine)
    try:
        user = create_user(setup, email=f"race-{uuid.uuid4().hex[:8]}@example.com")
        startup = create_startup(setup, owner=user)
        setup.commit()
        return startup.id, user.id
    finally:
        setup.close()


def _cleanup(engine: Engine, startup_id: uuid.UUID, user_id: uuid.UUID) -> None:
    cleanup = Session(bind=engine)
    try:
        # Jobs have no FK to startups, so they are removed explicitly.
        cleanup.query(Job).filter(Job.startup_id == startup_id).delete()
        # Cascades: startups -> enrollments, lesson_progress, certificates.
        cleanup.query(Startup).filter(Startup.id == startup_id).delete()
        cleanup.query(User).filter(User.id == user_id).delete()
        cleanup.commit()
    finally:
        cleanup.close()


def _race(engine: Engine, calls: list[Callable[[Session], object]]) -> list[BaseException | None]:
    barrier = threading.Barrier(len(calls))
    errors: list[BaseException | None] = []
    lock = threading.Lock()

    def run(call: Callable[[Session], object]) -> None:
        session = Session(bind=engine)
        outcome: BaseException | None
        try:
            barrier.wait(timeout=5)
            try:
                call(session)
            except BaseException as exc:  # noqa: BLE001 - captured for the assertion
                session.rollback()
                outcome = exc
            else:
                session.commit()
                outcome = None
        finally:
            session.close()
        with lock:
            errors.append(outcome)

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return errors


def test_concurrent_enrolments_create_one_row(engine: Engine):
    startup_id, user_id = _setup(engine)

    def enrol(session: Session) -> object:
        return get_or_create_enrollment(session, startup_id, user_id, "idea-shape-the-problem")

    try:
        assert _race(engine, [enrol, enrol]) == [None, None]
        verify = Session(bind=engine)
        try:
            count = (
                verify.query(Enrollment).filter_by(startup_id=startup_id, user_id=user_id).count()
            )
            assert count == 1, "exactly one enrolment, not one per racer"
        finally:
            verify.close()
    finally:
        _cleanup(engine, startup_id, user_id)


def test_concurrent_completions_of_the_same_lesson_record_it_once(engine: Engine):
    startup_id, user_id = _setup(engine)

    def complete(session: Session) -> object:
        return complete_lesson(session, startup_id, user_id, "idea-shape-the-problem-1")

    try:
        assert _race(engine, [complete, complete]) == [None, None]
        verify = Session(bind=engine)
        try:
            scope = {"startup_id": startup_id, "user_id": user_id}
            assert verify.query(LessonProgress).filter_by(**scope).count() == 1
            enrollments = verify.query(Enrollment).filter_by(**scope).all()
            assert len(enrollments) == 1
            assert enrollments[0].progress == 33
        finally:
            verify.close()
    finally:
        _cleanup(engine, startup_id, user_id)


def test_concurrent_completions_of_different_lessons_enrol_once_and_finish(engine: Engine):
    startup_id, user_id = _setup(engine)

    def first(session: Session) -> object:
        return complete_lesson(session, startup_id, user_id, "build-scope-the-mvp-1")

    def second(session: Session) -> object:
        return complete_lesson(session, startup_id, user_id, "build-scope-the-mvp-2")

    try:
        assert _race(engine, [first, second]) == [None, None]
        verify = Session(bind=engine)
        try:
            scope = {"startup_id": startup_id, "user_id": user_id}
            enrollments = verify.query(Enrollment).filter_by(**scope).all()
            assert len(enrollments) == 1, "exactly one enrolment, not one per racer"
            assert enrollments[0].progress == 100, "the roll-up must see both completions"
            assert enrollments[0].completed_at is not None
            assert verify.query(LessonProgress).filter_by(**scope).count() == 2
            assert verify.query(Certificate).filter_by(**scope).count() == 1
        finally:
            verify.close()
    finally:
        _cleanup(engine, startup_id, user_id)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/learning/test_service.py tests/services/learning/test_concurrency.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.learning.service'`.

- [ ] **Step 4: Create the service (write path)**

`app/services/learning/service.py`:

```python
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.learning import Certificate, Enrollment, LessonProgress
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.learning.catalog import Course, get_course, get_lesson


def _enrollment(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> Enrollment | None:
    return (
        db.query(Enrollment)
        .filter_by(startup_id=startup_id, user_id=user_id, course_id=course_id)
        .first()
    )


def get_or_create_enrollment(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> tuple[Enrollment, bool]:
    """Fetch or create the enrolment for ``(startup_id, user_id, course_id)``.

    Returns ``(enrollment, created)``; an unknown course raises ``NotFound``. An unguarded
    check-then-INSERT would let two concurrent callers both insert, and the loser's flush would
    raise ``IntegrityError`` against ``uq_enrollments_startup_user_course``. Mirrors
    ``get_or_create_canvas``: insert inside a SAVEPOINT, and on ``IntegrityError`` re-select the
    winner's now-committed row.
    """
    get_course(course_id)
    row = _enrollment(db, startup_id, user_id, course_id)
    if row is not None:
        return row, False
    try:
        with db.begin_nested():
            row = Enrollment(
                startup_id=startup_id, user_id=user_id, course_id=course_id, progress=0
            )
            db.add(row)
            db.flush()
    except IntegrityError:
        # A concurrent caller won the race -- their row is now committed and visible.
        existing = (
            db.query(Enrollment)
            .filter_by(startup_id=startup_id, user_id=user_id, course_id=course_id)
            .one()
        )
        return existing, False
    return row, True


def course_progress(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course: Course
) -> int:
    """Course % = round(100 x lessons completed / total lessons), spec section 5.

    Counts only this caller's completions in this workspace, and only lessons still in the
    catalog. ``round`` rounds exact halves to even, matching ``recompute_milestone_progress``.
    """
    lesson_ids = [lesson.id for lesson in course.lessons]
    done = (
        db.query(LessonProgress)
        .filter_by(startup_id=startup_id, user_id=user_id)
        .filter(LessonProgress.lesson_id.in_(lesson_ids))
        .count()
    )
    return round(100 * done / len(lesson_ids))


def _record_lesson(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str, lesson_id: str
) -> None:
    # Called only while holding the enrolment row lock, so concurrent completions of the same
    # lesson are serialised and the existence check cannot race.
    exists = (
        db.query(LessonProgress.id)
        .filter_by(startup_id=startup_id, user_id=user_id, lesson_id=lesson_id)
        .first()
    )
    if exists is not None:
        return
    db.add(
        LessonProgress(
            startup_id=startup_id,
            user_id=user_id,
            course_id=course_id,
            lesson_id=lesson_id,
            completed_at=datetime.now(UTC),
        )
    )
    db.flush()


def _issue_certificate(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> Certificate:
    cert = Certificate(
        startup_id=startup_id,
        user_id=user_id,
        course_id=course_id,
        credential_code=secrets.token_urlsafe(16),
        issued_at=datetime.now(UTC),
    )
    db.add(cert)
    db.flush()
    event_bus.publish(
        "learning.course.completed",
        {
            "startup_id": str(startup_id),
            "user_id": str(user_id),
            "course_id": course_id,
            "certificate_id": str(cert.id),
        },
    )
    job_dispatcher.enqueue(
        db, "learning.certificate.generate", {"certificate_id": str(cert.id)}, startup_id
    )
    return cert


def complete_lesson(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, lesson_id: str
) -> tuple[Enrollment, Certificate | None]:
    """Mark a lesson complete, enrolling the caller automatically if needed (spec D9).

    Returns ``(enrollment, certificate)``; ``certificate`` is set only when this call took the
    course to 100%. Idempotent: a repeat completion changes nothing and issues nothing.
    """
    course, _lesson = get_lesson(lesson_id)
    enrollment, _created = get_or_create_enrollment(db, startup_id, user_id, course.id)
    # Lock the enrolment row so concurrent completions in the same course roll up one at a time.
    # Without it, two callers could each count only their own lesson and store a stale
    # percentage -- the drift the spec forbids.
    enrollment = (
        db.query(Enrollment)
        .filter_by(id=enrollment.id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    _record_lesson(db, startup_id, user_id, course.id, lesson_id)
    enrollment.progress = course_progress(db, startup_id, user_id, course)
    certificate: Certificate | None = None
    if enrollment.progress == 100 and enrollment.completed_at is None:
        enrollment.completed_at = datetime.now(UTC)
        certificate = _issue_certificate(db, startup_id, user_id, course.id)
    db.flush()
    return enrollment, certificate


def list_certificates(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID
) -> list[Certificate]:
    return (
        db.query(Certificate)
        .filter_by(startup_id=startup_id, user_id=user_id)
        .order_by(Certificate.issued_at.desc())
        .all()
    )


def serialize_enrollment(enrollment: Enrollment) -> dict[str, Any]:
    return {
        "course_id": enrollment.course_id,
        "progress": enrollment.progress,
        "completed_at": (
            enrollment.completed_at.isoformat() if enrollment.completed_at else None
        ),
    }


def serialize_certificate(cert: Certificate) -> dict[str, Any]:
    return {
        "id": str(cert.id),
        "course_id": cert.course_id,
        "credential_code": cert.credential_code,
        "issued_at": cert.issued_at.isoformat(),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/services/learning/test_service.py tests/services/learning/test_concurrency.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/services/learning/service.py tests/services/learning/test_service.py tests/services/learning/test_concurrency.py
git commit -m "feat(learning): enrolment, lesson completion, progress and certificates"
```

---

### Task 4: Service — recommendations, continue watching, path progress, views

**Files:**
- Modify: `app/services/learning/service.py` (replace the import block; append the read path)
- Test: `tests/services/learning/test_browse.py`

**Interfaces:**
- Consumes: everything from Tasks 1-3; `ALL_COURSES`, `COURSES`, `Article`, `LearningPath`, `course_duration_min`, `path_duration_min` (Task 2); `CourseLevel`, `StartupStage`.
- Produces:
  - `enrollments_by_course(db, startup_id, user_id) -> dict[str, Enrollment]`
  - `completed_lesson_ids(db, startup_id, user_id, course_id) -> set[str]`
  - `RECOMMENDATION_SORT_KEYS: tuple[Callable[[Course], int], ...]` — level, then catalog order; the extension point for the Health Score key (spec D2)
  - `recommended_courses(stage, completed_ids) -> list[Course]`
  - `continue_watching(db, startup_id, user_id) -> list[tuple[Course, Enrollment]]`
  - `path_progress(path, progress) -> int`
  - `course_summary(course, enrollment) -> dict`, `course_detail(course, enrollment, completed_ids) -> dict`, `path_view(path, progress) -> dict`, `article_view(article) -> dict`
- Note: stage match is the **inclusion filter** in `recommended_courses` (spec §5 rule 1), so the sort keys that follow it are level, then catalog order.

- [ ] **Step 1: Write failing browse tests**

```python
# tests/services/learning/test_browse.py
from datetime import UTC, datetime, timedelta

from app.db.models.enums import MembershipRole, StartupStage
from app.services.learning.catalog import ARTICLES, COURSES, PATHS, LearningPath
from app.services.learning.service import (
    article_view,
    complete_lesson,
    completed_lesson_ids,
    continue_watching,
    course_detail,
    course_summary,
    enrollments_by_course,
    get_or_create_enrollment,
    path_progress,
    path_view,
    recommended_courses,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s)
    db.flush()
    return u, s


def _ids(courses):
    return [course.id for course in courses]


def test_recommendations_match_stage_beginner_first_then_catalog_order():
    assert _ids(recommended_courses(StartupStage.validation, set())) == [
        "idea-shape-the-problem",
        "validation-talk-to-customers",
        "build-scope-the-mvp",
    ]


def test_recommendations_exclude_completed_courses():
    completed = {"idea-shape-the-problem"}
    assert _ids(recommended_courses(StartupStage.validation, completed)) == [
        "validation-talk-to-customers",
        "build-scope-the-mvp",
    ]


def test_recommendations_for_a_single_stage():
    assert _ids(recommended_courses(StartupStage.launch, set())) == ["launch-plan-go-to-market"]


def test_recommendations_without_a_stage_fall_back_to_beginner_courses():
    assert _ids(recommended_courses(None, set())) == [
        "idea-shape-the-problem",
        "validation-talk-to-customers",
    ]


def test_path_percentage_rounds_exact_halves_to_even():
    path = LearningPath(
        id="t", title="t", stage=StartupStage.idea, course_ids=("a", "b", "c", "d")
    )
    assert path_progress(path, {"a": 100, "b": 50, "c": 50, "d": 50}) == 62  # 62.5 -> 62


def test_unenrolled_courses_count_as_zero_in_a_path():
    path = LearningPath(id="t", title="t", stage=StartupStage.idea, course_ids=("a", "b"))
    assert path_progress(path, {"a": 100}) == 50


def test_path_view_uses_the_average_of_course_progress():
    path = PATHS["path-validation-foundations"]
    view = path_view(path, {"idea-shape-the-problem": 100})
    assert view["progress"] == 33  # (100 + 0 + 0) / 3
    assert view["course_ids"] == list(path.course_ids)
    assert view["course_count"] == 3


def test_continue_watching_lists_enrolled_not_completed_most_recent_first(db):
    u, s = _ctx(db)
    older, _created = get_or_create_enrollment(db, s.id, u.id, "idea-shape-the-problem")
    newer, _created = get_or_create_enrollment(db, s.id, u.id, "validation-talk-to-customers")
    older.updated_at = datetime.now(UTC) - timedelta(hours=1)
    newer.updated_at = datetime.now(UTC)
    for lesson in COURSES["build-scope-the-mvp"].lessons:  # completed -> excluded
        complete_lesson(db, s.id, u.id, lesson.id)
    db.flush()
    assert [course.id for course, _e in continue_watching(db, s.id, u.id)] == [
        "validation-talk-to-customers",
        "idea-shape-the-problem",
    ]


def test_continue_watching_never_shows_another_members_enrolments(db):
    u, s = _ctx(db)
    teammate = create_user(db)
    create_membership(db, teammate, s, role=MembershipRole.team_member)
    db.flush()
    get_or_create_enrollment(db, s.id, teammate.id, "idea-shape-the-problem")
    assert continue_watching(db, s.id, u.id) == []


def test_course_summary_and_detail_reflect_the_callers_progress(db):
    u, s = _ctx(db)
    complete_lesson(db, s.id, u.id, "idea-shape-the-problem-1")
    course = COURSES["idea-shape-the-problem"]
    enrollment = enrollments_by_course(db, s.id, u.id)["idea-shape-the-problem"]

    summary = course_summary(course, enrollment)
    assert summary["enrolled"] is True and summary["completed"] is False
    assert summary["progress"] == 33 and summary["lesson_count"] == 3

    done = completed_lesson_ids(db, s.id, u.id, course.id)
    detail = course_detail(course, enrollment, done)
    assert [lesson["completed"] for lesson in detail["lessons"]] == [True, False, False]

    untouched = course_summary(COURSES["scale-build-the-org"], None)
    assert untouched["enrolled"] is False and untouched["progress"] == 0


def test_article_view():
    view = article_view(ARTICLES["article-pricing-basics"])
    assert view["id"] == "article-pricing-basics"
    assert view["tags"] == ["pricing", "validation"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/learning/test_browse.py -v`
Expected: FAIL — `ImportError: cannot import name 'article_view' from 'app.services.learning.service'`.

- [ ] **Step 3: Replace the import block of `app/services/learning/service.py`**

Replace the imports at the top of the file with:

```python
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.enums import CourseLevel, StartupStage
from app.db.models.learning import Certificate, Enrollment, LessonProgress
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.learning.catalog import (
    ALL_COURSES,
    COURSES,
    Article,
    Course,
    LearningPath,
    course_duration_min,
    get_course,
    get_lesson,
    path_duration_min,
)
```

- [ ] **Step 4: Append the read path to `app/services/learning/service.py`**

```python
def enrollments_by_course(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Enrollment]:
    rows = db.query(Enrollment).filter_by(startup_id=startup_id, user_id=user_id).all()
    return {row.course_id: row for row in rows}


def completed_lesson_ids(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID, course_id: str
) -> set[str]:
    rows = (
        db.query(LessonProgress.lesson_id)
        .filter_by(startup_id=startup_id, user_id=user_id, course_id=course_id)
        .all()
    )
    return {row.lesson_id for row in rows}


_LEVEL_RANK: dict[CourseLevel, int] = {
    CourseLevel.beginner: 0,
    CourseLevel.intermediate: 1,
    CourseLevel.advanced: 2,
}
_CATALOG_POSITION: dict[str, int] = {course.id: i for i, course in enumerate(ALL_COURSES)}

# Applied in turn. Stage match is the inclusion filter in ``recommended_courses``; the Health
# Score signal (spec D2) is added later as one more key here, without rewriting the ranker.
RECOMMENDATION_SORT_KEYS: tuple[Callable[[Course], int], ...] = (
    lambda course: _LEVEL_RANK[course.level],
    lambda course: _CATALOG_POSITION[course.id],
)


def recommended_courses(stage: StartupStage | None, completed_ids: set[str]) -> list[Course]:
    """Deterministic shelf, spec section 5.

    Stage match; completed courses excluded; beginner first; catalog order on ties. With no
    stage set, beginner courses from every stage.
    """
    if stage is None:
        candidates = [c for c in ALL_COURSES if c.level == CourseLevel.beginner]
    else:
        candidates = [c for c in ALL_COURSES if stage in c.stage_tags]
    remaining = [c for c in candidates if c.id not in completed_ids]
    return sorted(remaining, key=lambda c: tuple(key(c) for key in RECOMMENDATION_SORT_KEYS))


def continue_watching(
    db: Session, startup_id: uuid.UUID, user_id: uuid.UUID
) -> list[tuple[Course, Enrollment]]:
    """Enrolled, not-completed courses for this caller in this workspace, most recent first."""
    rows = (
        db.query(Enrollment)
        .filter_by(startup_id=startup_id, user_id=user_id)
        .filter(Enrollment.completed_at.is_(None))
        .order_by(Enrollment.updated_at.desc(), Enrollment.course_id)
        .all()
    )
    return [(COURSES[row.course_id], row) for row in rows if row.course_id in COURSES]


def path_progress(path: LearningPath, progress: dict[str, int]) -> int:
    """Path % = round(mean of its courses' course %); unenrolled courses count as 0."""
    values = [progress.get(course_id, 0) for course_id in path.course_ids]
    return round(sum(values) / len(values))


def course_summary(course: Course, enrollment: Enrollment | None) -> dict[str, Any]:
    return {
        "id": course.id,
        "title": course.title,
        "level": course.level.value,
        "stage_tags": [stage.value for stage in course.stage_tags],
        "lesson_count": len(course.lessons),
        "duration_min": course_duration_min(course),
        "enrolled": enrollment is not None,
        "progress": enrollment.progress if enrollment is not None else 0,
        "completed": enrollment is not None and enrollment.completed_at is not None,
    }


def course_detail(
    course: Course, enrollment: Enrollment | None, completed_ids: set[str]
) -> dict[str, Any]:
    return {
        **course_summary(course, enrollment),
        "lessons": [
            {
                "id": lesson.id,
                "title": lesson.title,
                "order": lesson.order,
                "video_ref": lesson.video_ref,
                "duration_min": lesson.duration_min,
                "transcript": lesson.transcript,
                "completed": lesson.id in completed_ids,
            }
            for lesson in course.lessons
        ],
    }


def path_view(path: LearningPath, progress: dict[str, int]) -> dict[str, Any]:
    return {
        "id": path.id,
        "title": path.title,
        "stage": path.stage.value,
        "course_ids": list(path.course_ids),
        "course_count": len(path.course_ids),
        "duration_min": path_duration_min(path),
        "progress": path_progress(path, progress),
    }


def article_view(article: Article) -> dict[str, Any]:
    return {
        "id": article.id,
        "title": article.title,
        "tags": list(article.tags),
        "body": article.body,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/services/learning/ -v`
Expected: PASS (all — the new browse tests, plus Task 2 and Task 3's tests still green).

- [ ] **Step 6: Commit**

```bash
git add app/services/learning/service.py tests/services/learning/test_browse.py
git commit -m "feat(learning): recommendations, continue watching and path progress"
```

---

### Task 5: Schemas, endpoints, router registration

**Files:**
- Create: `app/schemas/learning.py`
- Create: `app/api/v1/endpoints/learning.py`
- Modify: `app/api/v1/api.py` (register the router)
- Test: `tests/api/test_learning.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4; `require_role`, `get_verified_user`, `get_db`, `success_response`, `JSONResponse`.
- Produces: `GET /learning/recommendations`, `GET /learning/courses`, `GET /learning/courses/{course_id}`, `GET /learning/paths`, `GET /learning/articles`, `POST /learning/enrollments` (201 first time, 200 repeat), `PATCH /learning/lessons/{lesson_id}/progress`, `GET /learning/certificates`.
- Note: the `PATCH` response always carries a `certificate` key — `null` unless that call took the course to 100% — so the frontend reads one stable shape.

- [ ] **Step 1: Request schemas**

`app/schemas/learning.py`:

```python
from typing import Literal

from pydantic import BaseModel


class EnrollmentCreate(BaseModel):
    course_id: str


class LessonProgressUpdate(BaseModel):
    """v1 supports completing a lesson only; un-completing is out of scope (spec section 1)."""

    completed: Literal[True]
```

- [ ] **Step 2: Write failing API tests**

```python
# tests/api/test_learning.py
from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/learning"

NON_ACADEMY_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]

ROUTES = [
    ("GET", "/recommendations", None),
    ("GET", "/courses", None),
    ("GET", "/courses/idea-shape-the-problem", None),
    ("GET", "/paths", None),
    ("GET", "/articles", None),
    ("POST", "/enrollments", {"course_id": "idea-shape-the-problem"}),
    ("PATCH", "/lessons/idea-shape-the-problem-1/progress", {"completed": True}),
    ("GET", "/certificates", None),
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, verified=True, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC) if verified else None)
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def _call(client, method, path, body, headers=None):
    return client.request(method, BASE + path, json=body, headers=headers)


def _complete(client, headers, lesson_id, completed=True):
    return client.patch(
        f"{BASE}/lessons/{lesson_id}/progress", json={"completed": completed}, headers=headers
    )


@pytest.mark.parametrize("role", NON_ACADEMY_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_roles_other_than_founder_and_team_member_are_forbidden(
    client, db, role, method, path, body
):
    _founder, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = _call(client, method, path, body, h)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_founders_and_team_members_are_allowed(client, db, role, method, path, body):
    _u, _s, h = _member(db, role=role)
    r = _call(client, method, path, body, h)
    assert r.status_code in (200, 201), r.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unauthenticated_is_rejected(client, method, path, body):
    r = _call(client, method, path, body)
    assert r.status_code == 401, r.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unverified_member_is_forbidden(client, db, method, path, body):
    _u, _s, h = _member(db, verified=False)
    r = _call(client, method, path, body, h)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_non_member_of_the_workspace_is_forbidden(client, db):
    _owner, startup, _h = _member(db)
    outsider, _own, _oh = _member(db)
    r = client.get(f"{BASE}/courses", headers=_headers(outsider, startup))
    assert r.status_code == 403, r.text


def test_enrol_returns_201_then_200(client, db):
    _u, _s, h = _member(db)
    body = {"course_id": "idea-shape-the-problem"}
    first = client.post(f"{BASE}/enrollments", json=body, headers=h)
    again = client.post(f"{BASE}/enrollments", json=body, headers=h)
    assert first.status_code == 201, first.text
    assert again.status_code == 200, again.text
    assert first.json()["data"] == {
        "course_id": "idea-shape-the-problem",
        "progress": 0,
        "completed_at": None,
    }


def test_unknown_course_and_lesson_are_404(client, db):
    _u, _s, h = _member(db)
    enrol = client.post(f"{BASE}/enrollments", json={"course_id": "nope"}, headers=h)
    assert enrol.status_code == 404, enrol.text
    assert client.get(f"{BASE}/courses/nope", headers=h).status_code == 404
    assert _complete(client, h, "nope").status_code == 404


def test_uncompleting_a_lesson_is_rejected(client, db):
    _u, _s, h = _member(db)
    r = _complete(client, h, "idea-shape-the-problem-1", completed=False)
    assert r.status_code == 422, r.text


def test_finishing_a_course_over_http(client, db):
    _u, _s, h = _member(db)
    first = _complete(client, h, "build-scope-the-mvp-1")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["progress"] == 50
    assert first.json()["data"]["certificate"] is None

    last = _complete(client, h, "build-scope-the-mvp-2").json()["data"]
    assert last["progress"] == 100 and last["completed_at"] is not None
    cert = last["certificate"]
    assert cert["course_id"] == "build-scope-the-mvp" and cert["credential_code"]

    listed = client.get(f"{BASE}/certificates", headers=h).json()["data"]["certificates"]
    assert [c["id"] for c in listed] == [cert["id"]]

    detail = client.get(f"{BASE}/courses/build-scope-the-mvp", headers=h).json()["data"]
    assert detail["completed"] is True
    assert [lesson["completed"] for lesson in detail["lessons"]] == [True, True]


def test_recommendations_include_continue_watching(client, db):
    _u, _s, h = _member(db)
    client.post(
        f"{BASE}/enrollments", json={"course_id": "validation-talk-to-customers"}, headers=h
    )
    data = client.get(f"{BASE}/recommendations", headers=h).json()["data"]
    assert data["stage"] == "validation"
    assert [c["id"] for c in data["recommended"]] == [
        "idea-shape-the-problem",
        "validation-talk-to-customers",
        "build-scope-the-mvp",
    ]
    assert [c["id"] for c in data["continue_watching"]] == ["validation-talk-to-customers"]


def test_path_progress_over_http(client, db):
    _u, _s, h = _member(db)
    for n in (1, 2, 3):
        _complete(client, h, f"idea-shape-the-problem-{n}")
    paths = client.get(f"{BASE}/paths", headers=h).json()["data"]["paths"]
    by_id = {p["id"]: p for p in paths}
    assert by_id["path-validation-foundations"]["progress"] == 33  # (100 + 0 + 0) / 3
    assert by_id["path-launch-to-growth"]["progress"] == 0


def test_a_teammate_cannot_see_your_progress_or_certificates(client, db):
    _u, startup, h = _member(db)
    _t, _s, teammate_h = _member(db, role=MembershipRole.team_member, startup=startup)
    for n in (1, 2):
        _complete(client, h, f"build-scope-the-mvp-{n}")
    courses = client.get(f"{BASE}/courses", headers=teammate_h).json()["data"]["courses"]
    assert all(c["progress"] == 0 and c["enrolled"] is False for c in courses)
    certs = client.get(f"{BASE}/certificates", headers=teammate_h).json()["data"]
    assert certs["certificates"] == []


def test_the_same_person_progresses_separately_in_two_workspaces(client, db):
    u, _first, h_first = _member(db)
    second = create_startup(db, owner=u, name="Second", stage=StartupStage.validation)
    create_membership(db, u, second)
    db.flush()
    _complete(client, h_first, "idea-shape-the-problem-1")
    courses = client.get(f"{BASE}/courses", headers=_headers(u, second)).json()["data"]
    progress = {c["id"]: c["progress"] for c in courses["courses"]}
    assert progress["idea-shape-the-problem"] == 0


def test_articles_are_listed_and_labelled_placeholder(client, db):
    _u, _s, h = _member(db)
    articles = client.get(f"{BASE}/articles", headers=h).json()["data"]["articles"]
    assert len(articles) >= 2
    assert all(a["title"].startswith("[Placeholder] ") for a in articles)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/api/test_learning.py -v`
Expected: FAIL — the `/api/v1/learning` routes return 404 because the router does not exist yet.

- [ ] **Step 4: Create the router**

`app/api/v1/endpoints/learning.py`:

```python
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.schemas.learning import EnrollmentCreate, LessonProgressUpdate
from app.services.learning.catalog import ALL_ARTICLES, ALL_COURSES, ALL_PATHS, get_course
from app.services.learning.service import (
    article_view,
    complete_lesson,
    completed_lesson_ids,
    continue_watching,
    course_detail,
    course_summary,
    enrollments_by_course,
    get_or_create_enrollment,
    list_certificates,
    path_view,
    recommended_courses,
    serialize_certificate,
    serialize_enrollment,
)

router = APIRouter()
# Founders and team members only, reads included (spec D6, PRD RBAC matrix line 900).
_academy = require_role(MembershipRole.founder, MembershipRole.team_member)


def _startup(db: Session, membership: Membership) -> Startup:
    return db.query(Startup).filter(Startup.id == membership.startup_id).one()


@router.get("/recommendations")
def get_recommendations(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup = _startup(db, membership)
    enrollments = enrollments_by_course(db, startup.id, membership.user_id)
    completed = {cid for cid, e in enrollments.items() if e.completed_at is not None}
    shelf = recommended_courses(startup.stage, completed)
    watching = continue_watching(db, startup.id, membership.user_id)
    return success_response(
        {
            "stage": startup.stage.value if startup.stage is not None else None,
            "recommended": [course_summary(c, enrollments.get(c.id)) for c in shelf],
            "continue_watching": [course_summary(c, e) for c, e in watching],
        }
    )


@router.get("/courses")
def list_courses(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enrollments = enrollments_by_course(db, membership.startup_id, membership.user_id)
    return success_response(
        {"courses": [course_summary(c, enrollments.get(c.id)) for c in ALL_COURSES]}
    )


@router.get("/courses/{course_id}")
def get_course_detail(
    course_id: str,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    course = get_course(course_id)  # 404 if unknown
    enrollments = enrollments_by_course(db, membership.startup_id, membership.user_id)
    done = completed_lesson_ids(db, membership.startup_id, membership.user_id, course.id)
    return success_response(course_detail(course, enrollments.get(course.id), done))


@router.get("/paths")
def list_paths(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enrollments = enrollments_by_course(db, membership.startup_id, membership.user_id)
    progress = {course_id: e.progress for course_id, e in enrollments.items()}
    return success_response({"paths": [path_view(p, progress) for p in ALL_PATHS]})


@router.get("/articles")
def list_articles(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
) -> dict[str, Any]:
    return success_response({"articles": [article_view(a) for a in ALL_ARTICLES]})


@router.post("/enrollments", status_code=201)
def create_enrollment(
    body: EnrollmentCreate,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Any:
    enrollment, created = get_or_create_enrollment(
        db, membership.startup_id, membership.user_id, body.course_id
    )
    db.commit()
    return JSONResponse(
        status_code=(201 if created else 200),
        content=success_response(serialize_enrollment(enrollment)),
    )


@router.patch("/lessons/{lesson_id}/progress")
def update_lesson_progress(
    lesson_id: str,
    body: LessonProgressUpdate,
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    enrollment, certificate = complete_lesson(
        db, membership.startup_id, membership.user_id, lesson_id
    )
    db.commit()
    return success_response(
        {
            "lesson_id": lesson_id,
            **serialize_enrollment(enrollment),
            "certificate": serialize_certificate(certificate) if certificate else None,
        }
    )


@router.get("/certificates")
def list_my_certificates(
    membership: Membership = Depends(_academy),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_certificates(db, membership.startup_id, membership.user_id)
    return success_response({"certificates": [serialize_certificate(c) for c in rows]})
```

- [ ] **Step 5: Register the router**

In `app/api/v1/api.py`, add `learning` to the endpoint import tuple, between `journal` and
`mission`:

```python
    journal,
    learning,
    mission,
```

and add this line after the `documents` router registration:

```python
api_router.include_router(learning.router, prefix="/learning", tags=["learning"])
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `poetry run pytest tests/api/test_learning.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/learning.py app/api/v1/endpoints/learning.py app/api/v1/api.py tests/api/test_learning.py
git commit -m "feat(learning): /learning endpoints and router registration"
```

---

### Task 6: Smoke routes, live e2e, FE guide, SOP, checklist

**Files:**
- Modify: `e2e/test_smoke.py` (add the learning routes)
- Create: `e2e/test_learning.py` (+ captures under `e2e/_captures/learning/`)
- Create: `docs/fe-integration-guide-learning.md`
- Create: `docs/sop/<build-date>-learning-academy.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: Add the learning routes to the smoke test**

In `e2e/test_smoke.py`, add these entries to the route list, directly after the journal block:

```python
        # learning academy surface
        "/api/v1/learning/recommendations",
        "/api/v1/learning/courses",
        "/api/v1/learning/courses/{course_id}",
        "/api/v1/learning/paths",
        "/api/v1/learning/articles",
        "/api/v1/learning/enrollments",
        "/api/v1/learning/lessons/{lesson_id}/progress",
        "/api/v1/learning/certificates",
```

- [ ] **Step 2: Write the live journey**

Read `e2e/conftest.py` and `e2e/test_documents.py` first, to confirm the `make_verified_user`,
`base_url` and `capture(...)` conventions are unchanged. Then create:

```python
# e2e/test_learning.py
"""Live Learning Academy journey (Module 17): a founder onboards at the validation stage, reads
the recommendations shelf, browses the catalog and one course, enrols (201, then 200 on a
repeat), completes both lessons of a two-lesson course (progress 50, then 100 with a
certificate), confirms the finished course has left both the shelf and continue watching, and
lists paths, articles and certificates.

Every response body along the way is captured to `e2e/_captures/learning/*.json` -- those files
are the verbatim source for `docs/fe-integration-guide-learning.md`. They must be REAL bodies from
this live run, complete and untrimmed.

The Learning Academy has no roadmap or assessment dependency, so onboarding here is steps 1-4 +
complete -- the same shape as e2e/test_documents.py and e2e/test_journal.py.
"""

import httpx

COURSE = "build-scope-the-mvp"


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_documents.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def test_learning_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder at the validation stage.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Front page: a stage-matched shelf, nothing to continue yet.
        recs = c.get("/api/v1/learning/recommendations", headers=wh)
        assert recs.status_code == 200, recs.text
        assert COURSE in [x["id"] for x in recs.json()["data"]["recommended"]]
        assert recs.json()["data"]["continue_watching"] == []
        capture("learning", "recommendations_before", recs)

        # 2. The catalog grid and one course.
        courses = c.get("/api/v1/learning/courses", headers=wh)
        assert courses.status_code == 200, courses.text
        capture("learning", "courses", courses)
        detail = c.get(f"/api/v1/learning/courses/{COURSE}", headers=wh)
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["data"]["lessons"]) == 2
        capture("learning", "course_detail", detail)

        # 3. Enrol: 201 the first time, 200 on a repeat.
        enrol = c.post("/api/v1/learning/enrollments", headers=wh, json={"course_id": COURSE})
        assert enrol.status_code == 201, enrol.text
        capture("learning", "enrollment_created", enrol)
        again = c.post("/api/v1/learning/enrollments", headers=wh, json={"course_id": COURSE})
        assert again.status_code == 200, again.text
        capture("learning", "enrollment_repeat", again)

        # 4. Continue watching now shows the course.
        watching = c.get("/api/v1/learning/recommendations", headers=wh)
        assert [x["id"] for x in watching.json()["data"]["continue_watching"]] == [COURSE]
        capture("learning", "recommendations_in_progress", watching)

        # 5. Complete lesson 1 -> 50%, no certificate.
        first = c.patch(
            f"/api/v1/learning/lessons/{COURSE}-1/progress", headers=wh, json={"completed": True}
        )
        assert first.status_code == 200, first.text
        assert first.json()["data"]["progress"] == 50
        assert first.json()["data"]["certificate"] is None
        capture("learning", "lesson_completed", first)

        # 6. Complete lesson 2 -> 100% and a certificate.
        last = c.patch(
            f"/api/v1/learning/lessons/{COURSE}-2/progress", headers=wh, json={"completed": True}
        )
        assert last.status_code == 200, last.text
        finished = last.json()["data"]
        assert finished["progress"] == 100 and finished["certificate"] is not None
        capture("learning", "course_completed", last)

        # 7. The finished course leaves the shelf and continue watching.
        after = c.get("/api/v1/learning/recommendations", headers=wh).json()["data"]
        assert COURSE not in [x["id"] for x in after["recommended"]]
        assert after["continue_watching"] == []

        # 8. Paths (the formula live), articles and certificates.
        paths = c.get("/api/v1/learning/paths", headers=wh)
        assert paths.status_code == 200, paths.text
        by_id = {p["id"]: p for p in paths.json()["data"]["paths"]}
        assert by_id["path-validation-foundations"]["progress"] == 33  # (0 + 0 + 100) / 3
        capture("learning", "paths", paths)

        articles = c.get("/api/v1/learning/articles", headers=wh)
        assert articles.status_code == 200, articles.text
        capture("learning", "articles", articles)

        certs = c.get("/api/v1/learning/certificates", headers=wh)
        assert certs.status_code == 200, certs.text
        listed = certs.json()["data"]["certificates"]
        assert [x["id"] for x in listed] == [finished["certificate"]["id"]]
        capture("learning", "certificates", certs)
```

- [ ] **Step 3: Run the full e2e suite**

```bash
scripts/e2e_run.sh
```

Expected: green, including the new journey. A missing `db.commit()` surfaces here as "the enrolment
or the completion didn't persist" — verify both write endpoints commit.

- [ ] **Step 4: Write the FE integration guide**

Create `docs/fe-integration-guide-learning.md`, **every body pasted verbatim from
`e2e/_captures/learning/`**. Cover:

- the 8 endpoints with real request/response bodies, status codes and auth
- **access:** founders and team members only, reads included — show a real `403` body
- **placeholder content:** state plainly that every v1 title begins `[Placeholder] ` and real
  content replaces it before go-live
- **enrolment:** `201` the first time, `200` on a repeat, same body shape
- **automatic enrolment:** a `PATCH` on a lesson in an unenrolled course enrols the caller
- **the `certificate` key:** always present on the `PATCH` response, `null` unless that call took
  the course to 100%
- **the progress formula:** course % = lessons completed ÷ total lessons; path % = the mean of its
  courses' percentages, unenrolled courses counting as 0; both rounded, with exact halves going to
  the nearest even number — include the worked examples
- **continue watching** is returned inside `GET /learning/recommendations`, not a separate route
- an unknown course or lesson id → `404`; `{"completed": false}` → `422`

End with a verification table citing capture filenames.

- [ ] **Step 5: Write the SOP**

Create `docs/sop/<build-date>-learning-academy.md` in the project's SOP style (mirror
`docs/sop/2026-08-31-dashboard.md`): **what shipped** (with commit refs), **why**, **how** (in-code
catalog normalized for Module 25.4; savepoint get-or-create for enrolment; the enrolment row lock
that keeps the progress roll-up from drifting under concurrency; the progress formula), **what's
involved** (files, the three tables, migration `0019_learning`, the 8 routes), **verification**
(unit, concurrency, e2e and captures), **operate / roll back** (one migration; `downgrade` drops
all three tables and is lossy), and **follow-ups**:

- **replace the placeholder catalog with real content — required before go-live**
- move the catalog into the database when Module 25.4 lands
- add the Health Score signal as a recommendation sort key
- PDF rendering, sharing, and a public certificate verification endpoint
- private lesson notes, if they return

- [ ] **Step 6: Update the master checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, change the **Module 17 — Learning Academy** entry from
*junior handoff prepared* to shipped, and add a Module 17 section listing what shipped and the
follow-ups above — with real content flagged as required before go-live.

- [ ] **Step 7: Reproduce every CI check locally, then commit**

```bash
poetry run ruff check app tests
poetry run black --check app tests
poetry run mypy app
poetry run pytest
scripts/e2e_run.sh
```

All green. Then:

```bash
git add e2e/test_smoke.py e2e/test_learning.py e2e/_captures/learning/ docs/fe-integration-guide-learning.md docs/sop/ docs/checklist/PROJECT_CHECKLIST.md
git commit -m "test(learning): live e2e + captures + FE guide + SOP + checklist"
```

---

## Self-Review

**1. Spec coverage:**
- §2 access (founders and team members only, reads included; per-person and per-workspace scoping) → Task 5 (`_academy` on all 8 routes) + Task 5 access matrix and isolation tests + Task 3 isolation tests. ✓
- §3 data model (3 tables, unique constraints, `progress` CHECK, `server_default` timestamps, migration `0019_learning`) → Task 1. ✓
- §4 catalog (placeholder labelling, realistic shape, stable ids, catalog-wide lesson ids, derived counts) → Task 2. ✓
- §5 recommendations rule + extensible sort keys → Task 4; continue watching inside recommendations → Tasks 4 + 5; race-safe automatic enrolment → Task 3; progress formula → Tasks 3 + 4; idempotency and 201/200 → Tasks 3 + 5; `db.commit()` → Task 5. ✓
- §6 credential code, event, job → Task 3. ✓
- §7 testing — unit (Tasks 1-5), concurrency (Task 3), migration (Task 1), smoke + live e2e + FE guide (Task 6). ✓
- §8 file structure → matches Tasks 1-6. ✓
- §9 decisions D1-D10, waivers W1-W2, follow-ups → reflected across Tasks 1-5 and recorded in the SOP and checklist (Task 6). ✓

**2. Placeholder scan:** the only unresolved values are the migration filename, settled by Task 1's explicit numbering rule, and the SOP filename's `<build-date>`, taken on the day it is written. Every code and test step carries real content — no `TODO`, "handle edge cases" or bare "write tests".

**3. Type consistency:** `get_or_create_enrollment(db, startup_id, user_id, course_id) -> tuple[Enrollment, bool]`, `complete_lesson(db, startup_id, user_id, lesson_id) -> tuple[Enrollment, Certificate | None]`, `course_progress(db, startup_id, user_id, course) -> int`, `list_certificates(db, startup_id, user_id)`, `enrollments_by_course(db, startup_id, user_id)`, `completed_lesson_ids(db, startup_id, user_id, course_id)`, `recommended_courses(stage, completed_ids)`, `continue_watching(db, startup_id, user_id) -> list[tuple[Course, Enrollment]]`, `path_progress(path, progress)`, and the `course_summary` / `course_detail` / `path_view` / `article_view` / `serialize_enrollment` / `serialize_certificate` views are used identically across the service, endpoints and tests. Catalog ids (`idea-shape-the-problem`, `build-scope-the-mvp`, `path-validation-foundations`, …) and lesson ids (`<course_id>-<n>`) match across the catalog, tests and e2e. `EnrollmentCreate` and `LessonProgressUpdate` match their endpoint use.