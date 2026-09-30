# Module 09 — Validation Hub — Implementation Plan

**Date:** 2026-09-20
**Spec:** `docs/superpowers/specs/2026-09-20-validation-hub-design.md` (decisions D1–D10, waivers
W1–W3, all agreed with the lead)
**Branch:** `feat/validation-hub`, off `develop`
**PR target:** `develop`

---

## How this plan is built

Seven tasks, each one a full test-first cycle: write the failing tests, run them and watch them
fail, write the code, run them again, then commit.

| Task | Covers |
|---|---|
| 1 | Six enums, five models, the migration, model + migration tests |
| 2 | Question and answer validation (pure logic, no database) |
| 3 | Service — assumptions and experiments, events, evidence counts, smoke-test stats |
| 4 | Service — interviews and surveys, the public token lifecycle, analytics |
| 5 | The public surface — token lookup, response submission, and the `Limiter` move |
| 6 | Schemas, endpoints (member + public), router registration, API tests |
| 7 | Smoke routes, live e2e, FE guide, SOP, checklist |

## Conventions this module follows

- **TDD.** Tests are written and seen to fail before the code that satisfies them.
- **Tenancy.** `startup_id` always comes from the caller's membership (or, publicly, from the
  survey). Cross-workspace reads return the same 404 as a missing row.
- **Transactions.** Services `flush()`; write endpoints call `db.commit()`.
- **Responses.** The standard envelope via `success_response`; response bodies are plain dicts
  built in the service, as in `app/services/learning/` and `app/services/business/`.
- **Errors.** `NotFound`, `Forbidden`, and `AppError("VALIDATION_ERROR", …, 422)`. No new codes.
- **Events.** `event_bus.publish(db, event, payload)` — `db` first.
- **Jobs.** `job_dispatcher.enqueue(db, type, payload, startup_id)`, enqueue-only.
- **Migrations.** Produced with `alembic revision --autogenerate`; only the revision id,
  `down_revision`, Create Date and docstring are hand-edited. Number settled against the live
  `develop` head at build time; single head before pushing.
- **Tests.** Real Postgres with per-test rollback, through the `db` fixture. A test never opens its
  own connection to the application database; the only exception is the concurrency pattern, which
  uses the session-scoped `engine` fixture.
- **Commits.** Small, one per task, and **no AI-attribution trailers** (project rule).
- **CI green locally before pushing:** `poetry run ruff check app tests`,
  `poetry run black --check app tests`, `poetry run mypy app`, `poetry run pytest`, then
  `scripts/e2e_run.sh`.

> **Running these commands on this machine:** the toolchain runs inside the persistent
> `python:3.11` container, so each `poetry run X` below is run as `docker exec cfz-test X`, with
> `-e REDIS_URL=redis://host.docker.internal:6379/0` added for full-suite runs so the Redis-backed
> auth tests can reach Redis.

---

### Task 1: Schema — six enums, five models, migration `0026_validation`

**Files:**
- Modify: `app/db/models/enums.py` (six new enums)
- Create: `app/db/models/validation.py` (`Assumption`, `Experiment`, `Interview`, `Survey`,
  `SurveyResponse`)
- Modify: `app/db/models/__init__.py` (register the models)
- Create: `alembic/versions/0026_validation.py` (number settled at build time)
- Test: `tests/db/test_validation_models.py`, `tests/test_validation_migration.py`

**Interfaces:**
- Consumes: `UUIDMixin`, `TimestampMixin`, `Base`.
- Produces: the five models and six enums every later task builds on.

**Migration numbering rule:** before generating, run `git fetch origin && poetry run alembic heads`.
If the head is still `0025_roadmap_milestone_due_idx`, use `0026_validation`; otherwise take the
next free number after whatever head exists and chain onto it. Re-check immediately before pushing
and renumber if something merged first (this happened twice on Module 17).

- [ ] **Step 1: Add the enums**

Append to the end of `app/db/models/enums.py`, after `CourseLevel`:

```python
class RiskLevel(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class AssumptionStatus(enum.StrEnum):
    untested = "untested"
    testing = "testing"
    validated = "validated"
    invalidated = "invalidated"


class ExperimentType(enum.StrEnum):
    smoke_test = "smoke_test"
    landing_page = "landing_page"
    ad_test = "ad_test"
    other = "other"


class ExperimentStatus(enum.StrEnum):
    draft = "draft"
    live = "live"
    ended = "ended"


class InterviewVerdict(enum.StrEnum):
    supports = "supports"
    contradicts = "contradicts"
    neutral = "neutral"


class SurveyStatus(enum.StrEnum):
    draft = "draft"
    open = "open"
    closed = "closed"
```

- [ ] **Step 2: Create the models**

`app/db/models/validation.py`:

```python
import datetime
import uuid
from typing import Any

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text
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
    submitted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
```

There is deliberately **no `user_id`** on `SurveyResponse` and **no `evidence_count`** on
`Assumption` (spec D2, D3).

- [ ] **Step 3: Register the models**

In `app/db/models/__init__.py`, at the end of the import list, after the `user` import (the file is
alphabetical by module):

```python
from app.db.models.validation import (  # noqa: F401
    Assumption,
    Experiment,
    Interview,
    Survey,
    SurveyResponse,
)
```


- [ ] **Step 4: Autogenerate and number the migration**

```bash
git fetch origin && poetry run alembic heads     # confirm the live head first
poetry run alembic upgrade head
poetry run alembic revision --autogenerate -m "validation"
```

Rename the generated file per the numbering rule above (`0026_validation.py` if the head is still
`0025_roadmap_milestone_due_idx`), set `revision` and `down_revision`, and add a short docstring
mirroring `0023_learning.py`.

Confirm `upgrade()` creates `assumptions`, `experiments`, `interviews`, `surveys` and
`survey_responses` with:

- `ix_<table>_startup_id` on all five tables, and `ix_survey_responses_survey_id`
- a unique constraint on `surveys.token_hash`
- `server_default` on the JSONB columns (`'{}'` / `'[]'`) and on the two status columns
  (`'untested'`, `'draft'`)
- `server_default=sa.text("now()")` on every `created_at` / `updated_at`
- `ondelete="CASCADE"` on every foreign key (five to `startups`, one to `surveys`)
- **no** `user_id` column on `survey_responses`

- [ ] **Step 5: Verify single head and zero drift**

```bash
poetry run alembic heads          # exactly one head
poetry run alembic upgrade head   # applies clean
poetry run alembic check          # "No new upgrade operations detected."
```

- [ ] **Step 6: Write the model tests**

```python
# tests/db/test_validation_models.py
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    RiskLevel,
    SurveyStatus,
)
from app.db.models.validation import Assumption, Experiment, Interview, Survey, SurveyResponse
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def test_assumption_defaults_to_untested(db):
    _u, s = _ctx(db)
    row = Assumption(startup_id=s.id, statement="Founders will pay", risk=RiskLevel.high)
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.status == AssumptionStatus.untested


def test_experiment_json_columns_default_empty(db):
    _u, s = _ctx(db)
    row = Experiment(startup_id=s.id, name="Landing page", type=ExperimentType.smoke_test)
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.config == {} and row.metrics == {} and row.assumption_ids == []
    assert row.status == ExperimentStatus.draft


def test_interview_stores_quotes_and_assumption_links(db):
    _u, s = _ctx(db)
    assumption = Assumption(startup_id=s.id, statement="x", risk=RiskLevel.low)
    db.add(assumption)
    db.flush()
    row = Interview(
        startup_id=s.id,
        interviewee="Ada",
        segment="fintech",
        held_on=date(2026, 9, 20),
        notes="Said yes without prompting.",
        key_quotes=["I would pay for this today"],
        verdict=InterviewVerdict.supports,
        assumption_ids=[str(assumption.id)],
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.key_quotes == ["I would pay for this today"]
    assert row.assumption_ids == [str(assumption.id)]


def test_survey_defaults_and_token_hash_is_unique(db):
    _u, s = _ctx(db)
    first = Survey(startup_id=s.id, title="Pricing", token_hash="a" * 64)
    db.add(first)
    db.flush()
    db.refresh(first)
    assert first.status == SurveyStatus.draft and first.questions == []
    db.add(Survey(startup_id=s.id, title="Second", token_hash="a" * 64))
    with pytest.raises(IntegrityError):
        db.flush()


def test_two_draft_surveys_may_both_have_no_token(db):
    _u, s = _ctx(db)
    db.add(Survey(startup_id=s.id, title="One"))
    db.add(Survey(startup_id=s.id, title="Two"))
    db.flush()  # NULL token_hash is not caught by the unique constraint


def test_response_is_anonymous_and_dies_with_its_survey(db):
    _u, s = _ctx(db)
    survey = Survey(startup_id=s.id, title="Pricing")
    db.add(survey)
    db.flush()
    db.add(
        SurveyResponse(
            survey_id=survey.id,
            startup_id=s.id,
            answers={"q1": 9},
            submitted_at=datetime.now(UTC),
        )
    )
    db.flush()
    assert "user_id" not in SurveyResponse.__table__.columns
    db.delete(survey)
    db.flush()
    assert db.query(SurveyResponse).filter_by(survey_id=survey.id).count() == 0
```

- [ ] **Step 7: Write the migration test**

```python
# tests/test_validation_migration.py
import subprocess


def test_validation_migration_applies():
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
            "assert {'assumptions','experiments','interviews','surveys','survey_responses'} <= n, n; "
            "uq={x['name'] for x in i.get_unique_constraints('surveys')}; "
            "assert any('token_hash' in str(x) for x in i.get_indexes('surveys')) or uq, uq; "
            "cols={c['name'] for c in i.get_columns('survey_responses')}; "
            "assert 'user_id' not in cols, cols; "
            "assert {'survey_id','startup_id','answers','submitted_at'} <= cols, cols; "
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

Run: `poetry run pytest tests/db/test_validation_models.py tests/test_validation_migration.py -v`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add app/db/models/enums.py app/db/models/validation.py app/db/models/__init__.py alembic/versions/ tests/db/test_validation_models.py tests/test_validation_migration.py
git commit -m "feat(validation): assumptions, experiments, interviews, surveys and responses tables"
```


---

### Task 2: Question and answer validation

Pure logic, no database. This is the piece the public endpoint leans on, so it is built and tested
on its own first.

**Files:**
- Create: `app/services/validation/__init__.py` (empty)
- Create: `app/services/validation/questions.py`
- Create: `tests/services/validation/__init__.py` (empty)
- Test: `tests/services/validation/test_questions.py`

**Interfaces:**
- Consumes: `AppError`.
- Produces: `MAX_QUESTIONS`, `MAX_OPTIONS`, `MAX_OPEN_ANSWER`, `QUESTION_TYPES`,
  `validate_questions(questions) -> list[dict]`, `validate_answers(questions, answers) -> dict`.

- [ ] **Step 1: Create the two empty packages**

Create empty `app/services/validation/__init__.py` and `tests/services/validation/__init__.py`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/services/validation/test_questions.py
import pytest

from app.core.errors import AppError
from app.services.validation.questions import (
    MAX_OPEN_ANSWER,
    MAX_OPTIONS,
    MAX_QUESTIONS,
    validate_answers,
    validate_questions,
)


def _questions():
    return validate_questions(
        [
            {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
            {"type": "scale", "prompt": "How useful?"},
            {"type": "nps", "prompt": "Would you recommend us?"},
            {"type": "open", "prompt": "Anything else?"},
        ]
    )


def test_each_question_gets_a_stable_id_and_clean_shape():
    questions = _questions()
    assert [q["type"] for q in questions] == ["choice", "scale", "nps", "open"]
    assert all(q["id"] for q in questions)
    assert len({q["id"] for q in questions}) == 4
    assert questions[0]["required"] is True and questions[1]["required"] is False
    assert questions[0]["options"] == ["A", "B"]


def test_an_existing_id_is_kept_so_answers_keep_pointing_at_it():
    first = validate_questions([{"id": "q-1", "type": "open", "prompt": "Why?"}])
    again = validate_questions(first)
    assert again[0]["id"] == "q-1"


def test_bad_question_shapes_are_rejected():
    for bad in (
        "not a list",
        [{"type": "sketch", "prompt": "?"}],
        [{"type": "open", "prompt": "   "}],
        [{"type": "choice", "prompt": "Pick", "options": []}],
        [{"type": "choice", "prompt": "Pick", "options": ["ok", 7]}],
    ):
        with pytest.raises(AppError) as exc:
            validate_questions(bad)
        assert exc.value.http_status == 422


def test_the_caps_are_enforced():
    too_many = [{"type": "open", "prompt": f"Q{i}"} for i in range(MAX_QUESTIONS + 1)]
    with pytest.raises(AppError):
        validate_questions(too_many)
    too_wide = [
        {"type": "choice", "prompt": "Pick", "options": [f"o{i}" for i in range(MAX_OPTIONS + 1)]}
    ]
    with pytest.raises(AppError):
        validate_questions(too_wide)


def test_answers_are_returned_cleaned_and_optional_ones_may_be_missing():
    questions = _questions()
    ids = [q["id"] for q in questions]
    clean = validate_answers(
        questions,
        {ids[0]: "A", ids[1]: 4, ids[2]: 9, ids[3]: "  loved it  "},
    )
    assert clean == {ids[0]: "A", ids[1]: 4, ids[2]: 9, ids[3]: "loved it"}
    assert validate_answers(questions, {ids[0]: "B"}) == {ids[0]: "B"}


def test_a_required_question_must_be_answered():
    questions = _questions()
    with pytest.raises(AppError) as exc:
        validate_answers(questions, {questions[1]["id"]: 3})
    assert exc.value.http_status == 422


def test_an_unknown_question_id_is_rejected():
    questions = _questions()
    with pytest.raises(AppError):
        validate_answers(questions, {questions[0]["id"]: "A", "not-a-question": "hi"})


def test_answer_values_must_match_their_question_type():
    questions = _questions()
    choice, scale, nps, open_q = (q["id"] for q in questions)
    for answers in (
        {choice: "C"},
        {choice: "A", scale: 0},
        {choice: "A", scale: 6},
        {choice: "A", scale: "4"},
        {choice: "A", nps: 11},
        {choice: "A", nps: True},
        {choice: "A", open_q: 42},
        {choice: "A", open_q: "x" * (MAX_OPEN_ANSWER + 1)},
    ):
        with pytest.raises(AppError) as exc:
            validate_answers(questions, answers)
        assert exc.value.http_status == 422
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/validation/test_questions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.validation.questions'`.

- [ ] **Step 4: Write the validator**

`app/services/validation/questions.py`:

```python
"""Survey question and answer validation (Module 09).

Questions live as JSONB on ``surveys`` and answers as JSONB on ``survey_responses``; both are
checked here rather than modelled as tables (spec D7), the same way ``validate_sections`` checks
document sections in Module 18.

Every message raised here is safe to show a member of the public: it describes the respondent's
own answer and never anything about the survey's workspace (spec section 4).
"""

import uuid
from typing import Any

from app.core.errors import AppError

MAX_QUESTIONS = 50
MAX_OPTIONS = 20
MAX_OPEN_ANSWER = 4000
QUESTION_TYPES = ("choice", "scale", "nps", "open")
SCALE_MIN, SCALE_MAX = 1, 5
NPS_MIN, NPS_MAX = 0, 10


def _invalid(message: str, field: str | None = None) -> AppError:
    field_errors = [{"field": field, "message": message}] if field else None
    return AppError("VALIDATION_ERROR", message, 422, field_errors=field_errors)


def validate_questions(questions: Any) -> list[dict[str, Any]]:
    """Return the cleaned question list, giving every question a stable id.

    An id already present is kept, so editing a survey does not orphan the answers already
    collected against its questions.
    """
    if not isinstance(questions, list):
        raise _invalid("Questions must be a list.")
    if len(questions) > MAX_QUESTIONS:
        raise _invalid(f"A survey may have at most {MAX_QUESTIONS} questions.")
    return [_validate_question(question, index) for index, question in enumerate(questions)]


def _validate_question(question: Any, index: int) -> dict[str, Any]:
    field = f"questions.{index}"
    if not isinstance(question, dict):
        raise _invalid("Each question must be an object.", field)
    qtype = question.get("type")
    if qtype not in QUESTION_TYPES:
        raise _invalid(f"Question type must be one of: {', '.join(QUESTION_TYPES)}.", field)
    prompt = question.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _invalid("Each question needs a prompt.", field)
    clean: dict[str, Any] = {
        "id": str(question.get("id") or uuid.uuid4()),
        "type": qtype,
        "prompt": prompt.strip(),
        "required": bool(question.get("required", False)),
    }
    if qtype == "choice":
        clean["options"] = _validate_options(question.get("options"), field)
    return clean


def _validate_options(options: Any, field: str) -> list[str]:
    if not isinstance(options, list) or not options:
        raise _invalid("A choice question needs at least one option.", field)
    if len(options) > MAX_OPTIONS:
        raise _invalid(f"A choice question may have at most {MAX_OPTIONS} options.", field)
    if any(not isinstance(option, str) or not option.strip() for option in options):
        raise _invalid("Choice options must be non-empty text.", field)
    return [option.strip() for option in options]


def validate_answers(questions: list[dict[str, Any]], answers: Any) -> dict[str, Any]:
    """Check a respondent's answers against the survey's own questions."""
    if not isinstance(answers, dict):
        raise _invalid("Answers must be an object keyed by question id.")
    by_id = {question["id"]: question for question in questions}
    unknown = sorted(set(answers) - set(by_id))
    if unknown:
        raise _invalid("This question is not part of the survey.", f"answers.{unknown[0]}")
    clean: dict[str, Any] = {}
    for question_id, question in by_id.items():
        if question_id not in answers:
            if question["required"]:
                raise _invalid("This question is required.", f"answers.{question_id}")
            continue
        clean[question_id] = _validate_answer(question, answers[question_id])
    return clean


def _validate_answer(question: dict[str, Any], value: Any) -> Any:
    field = f"answers.{question['id']}"
    qtype = question["type"]
    if qtype == "choice":
        if value not in question.get("options", []):
            raise _invalid("Pick one of the offered options.", field)
        return value
    if qtype == "scale":
        return _whole_number(value, SCALE_MIN, SCALE_MAX, field)
    if qtype == "nps":
        return _whole_number(value, NPS_MIN, NPS_MAX, field)
    if not isinstance(value, str):
        raise _invalid("This answer must be text.", field)
    text = value.strip()
    if len(text) > MAX_OPEN_ANSWER:
        raise _invalid(f"Keep this answer under {MAX_OPEN_ANSWER} characters.", field)
    return text


def _whole_number(value: Any, low: int, high: int, field: str) -> int:
    # ``bool`` is a subclass of ``int`` in Python, so True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _invalid(f"This answer must be a whole number from {low} to {high}.", field)
    return value
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/services/validation/test_questions.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/services/validation/ tests/services/validation/
git commit -m "feat(validation): survey question and answer validation"
```


---

### Task 3: Service — assumptions and experiments

**Files:**
- Create: `app/services/validation/service.py` (assumptions + experiments half)
- Test: `tests/services/validation/test_assumptions.py`,
  `tests/services/validation/test_experiments.py`

**Interfaces:**
- Consumes: the Task 1 models and enums; `event_bus`; `NotFound`; `AppError`.
- Produces:
  - `list_assumptions(db, startup_id, *, status, risk)`, `get_assumption(db, startup_id, id)`,
    `create_assumption(...)`, `update_assumption(...)`, `evidence_counts(db, startup_id,
    assumptions)`, `serialize_assumption(assumption, evidence_count)`
  - `list_experiments(db, startup_id, *, type, status)`, `get_experiment(db, startup_id, id)`,
    `create_experiment(...)`, `update_experiment(...)`, `serialize_experiment(experiment)`,
    `smoke_test_stats(db, startup_id, id)`
  - `link_ids(db, startup_id, assumption_ids)` — shared link validation (spec D9)

- [ ] **Step 1: Write the failing assumption tests**

```python
# tests/services/validation/test_assumptions.py
import uuid

import pytest

from app.core.errors import NotFound
from app.db.models.enums import AssumptionStatus, ExperimentType, InterviewVerdict, RiskLevel
from app.db.models.validation import Experiment, Interview
from app.services.validation.service import (
    create_assumption,
    evidence_counts,
    get_assumption,
    list_assumptions,
    serialize_assumption,
    update_assumption,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, s, **kw):
    kw.setdefault("statement", "Founders will pay for this")
    kw.setdefault("risk", RiskLevel.high)
    return create_assumption(db, s.id, **kw)


def test_new_assumptions_start_untested(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    assert row.status == AssumptionStatus.untested and row.risk == RiskLevel.high


def test_list_is_newest_first_and_filters(db):
    _u, s = _ctx(db)
    _new(db, s, statement="First", risk=RiskLevel.low)
    second = _new(db, s, statement="Second", status=AssumptionStatus.testing)
    assert [a.id for a in list_assumptions(db, s.id)][0] == second.id
    assert len(list_assumptions(db, s.id, status=AssumptionStatus.testing)) == 1
    assert len(list_assumptions(db, s.id, risk=RiskLevel.low)) == 1


def test_another_workspaces_assumption_is_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = _new(db, s)
    with pytest.raises(NotFound):
        get_assumption(db, other.id, mine.id)
    with pytest.raises(NotFound):
        get_assumption(db, s.id, uuid.uuid4())


def test_editing_statement_and_risk(db):
    u, s = _ctx(db)
    row = _new(db, s)
    update_assumption(db, row, actor_id=u.id, statement="  Reworded  ", risk=RiskLevel.medium)
    assert row.statement == "Reworded" and row.risk == RiskLevel.medium


def test_moving_into_validated_emits_exactly_one_event(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.validation.service.event_bus.publish",
        lambda db, e, p: events.append((e, p)),
    )
    u, s = _ctx(db)
    row = _new(db, s)
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.validated)
    assert events == [
        (
            "validation.assumption.validated",
            {
                "startup_id": str(s.id),
                "assumption_id": str(row.id),
                "status": "validated",
                "actor_id": str(u.id),
            },
        )
    ]
    # Saving the same status again changes nothing and emits nothing.
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.validated)
    assert len(events) == 1


def test_invalidated_emits_its_own_event_and_other_moves_emit_nothing(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.validation.service.event_bus.publish",
        lambda db, e, p: events.append((e, p)),
    )
    u, s = _ctx(db)
    row = _new(db, s)
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.testing)
    assert events == []
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.invalidated)
    assert [e for e, _p in events] == ["validation.assumption.invalidated"]
    # The board may drag a card back; that emits nothing.
    update_assumption(db, row, actor_id=u.id, status=AssumptionStatus.testing)
    assert len(events) == 1


def test_evidence_count_is_derived_and_workspace_scoped(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    row = _new(db, s)
    db.add(
        Experiment(
            startup_id=s.id,
            name="Landing page",
            type=ExperimentType.smoke_test,
            assumption_ids=[str(row.id)],
        )
    )
    db.add(
        Interview(
            startup_id=s.id,
            interviewee="Ada",
            held_on=__import__("datetime").date(2026, 9, 20),
            verdict=InterviewVerdict.supports,
            assumption_ids=[str(row.id)],
        )
    )
    # A link from another workspace must not count.
    db.add(
        Experiment(
            startup_id=other.id,
            name="Theirs",
            type=ExperimentType.other,
            assumption_ids=[str(row.id)],
        )
    )
    db.flush()
    counts = evidence_counts(db, s.id, [row])
    assert counts[str(row.id)] == 2
    assert evidence_counts(db, s.id, []) == {}


def test_serialize_shape(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    out = serialize_assumption(row, 3)
    assert out["id"] == str(row.id)
    assert out["statement"] == "Founders will pay for this"
    assert out["risk"] == "high" and out["status"] == "untested"
    assert out["evidence_count"] == 3
    assert out["created_at"] and out["updated_at"]
```

- [ ] **Step 2: Write the failing experiment tests**

```python
# tests/services/validation/test_experiments.py
import uuid

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import ExperimentStatus, ExperimentType, RiskLevel
from app.services.validation.service import (
    create_assumption,
    create_experiment,
    get_experiment,
    list_experiments,
    serialize_experiment,
    smoke_test_stats,
    update_experiment,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, s, **kw):
    kw.setdefault("name", "Fake door")
    kw.setdefault("type", ExperimentType.smoke_test)
    return create_experiment(db, s.id, **kw)


def test_new_experiments_start_draft_and_empty(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    assert row.status == ExperimentStatus.draft
    assert row.config == {} and row.metrics == {} and row.assumption_ids == []


def test_links_must_belong_to_this_workspace(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = create_assumption(db, s.id, statement="Mine", risk=RiskLevel.low)
    theirs = create_assumption(db, other.id, statement="Theirs", risk=RiskLevel.low)
    row = _new(db, s, assumption_ids=[str(mine.id)])
    assert row.assumption_ids == [str(mine.id)]
    with pytest.raises(AppError) as exc:
        _new(db, s, assumption_ids=[str(theirs.id)])
    assert exc.value.http_status == 422
    with pytest.raises(AppError):
        _new(db, s, assumption_ids=[str(uuid.uuid4())])


def test_list_filters_and_cross_workspace_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    _new(db, s, name="Smoke")
    _new(db, s, name="Advert", type=ExperimentType.ad_test, status=ExperimentStatus.live)
    assert len(list_experiments(db, s.id)) == 2
    assert len(list_experiments(db, s.id, type=ExperimentType.ad_test)) == 1
    assert len(list_experiments(db, s.id, status=ExperimentStatus.live)) == 1
    mine = _new(db, s)
    with pytest.raises(NotFound):
        get_experiment(db, other.id, mine.id)


def test_update_replaces_only_what_is_given(db):
    _u, s = _ctx(db)
    row = _new(db, s, config={"headline": "Old"}, metrics={"visits": 10})
    update_experiment(db, row, status=ExperimentStatus.live, metrics={"visits": 40, "signups": 4})
    assert row.status == ExperimentStatus.live
    assert row.metrics == {"visits": 40, "signups": 4}
    assert row.config == {"headline": "Old"} and row.name == "Fake door"


def test_smoke_stats_derive_conversion(db):
    _u, s = _ctx(db)
    row = _new(db, s, metrics={"visits": 200, "signups": 13})
    stats = smoke_test_stats(db, s.id, row.id)
    assert stats["visits"] == 200 and stats["signups"] == 13
    assert stats["conversion"] == 6.5


def test_smoke_stats_never_divide_by_zero(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    stats = smoke_test_stats(db, s.id, row.id)
    assert stats == {
        "experiment_id": str(row.id),
        "name": "Fake door",
        "status": "draft",
        "visits": 0,
        "signups": 0,
        "conversion": 0.0,
    }


def test_stats_are_404_for_an_experiment_that_is_not_a_smoke_test(db):
    _u, s = _ctx(db)
    row = _new(db, s, type=ExperimentType.ad_test)
    with pytest.raises(NotFound):
        smoke_test_stats(db, s.id, row.id)


def test_serialize_shape(db):
    _u, s = _ctx(db)
    row = _new(db, s, config={"headline": "Try it"}, metrics={"visits": 1})
    out = serialize_experiment(row)
    assert out["id"] == str(row.id) and out["name"] == "Fake door"
    assert out["type"] == "smoke_test" and out["status"] == "draft"
    assert out["config"] == {"headline": "Try it"} and out["metrics"] == {"visits": 1}
    assert out["assumption_ids"] == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/validation/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.validation.service'`.

- [ ] **Step 4: Write the service (assumptions + experiments)**

`app/services/validation/service.py`:

```python
"""Validation Hub service (Module 09) — assumptions and experiments.

Services flush; the endpoints commit (spec section 5). Everything here takes ``startup_id`` from
the caller's membership, never from a request body.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import AssumptionStatus, ExperimentStatus, ExperimentType, RiskLevel
from app.db.models.validation import Assumption, Experiment, Interview
from app.platform.events import event_bus

# Only these two transitions are worth telling the rest of the system about (spec D8).
_EVENT_BY_STATUS = {
    AssumptionStatus.validated: "validation.assumption.validated",
    AssumptionStatus.invalidated: "validation.assumption.invalidated",
}


def link_ids(db: Session, startup_id: uuid.UUID, assumption_ids: Any) -> list[str]:
    """Clean a list of assumption links, rejecting anything outside this workspace (spec D9)."""
    if assumption_ids is None:
        return []
    if not isinstance(assumption_ids, list):
        raise AppError("VALIDATION_ERROR", "Assumption links must be a list.", 422)
    wanted = [str(value) for value in assumption_ids]
    if not wanted:
        return []
    known = {
        str(row.id) for row in db.query(Assumption.id).filter_by(startup_id=startup_id).all()
    }
    if [value for value in wanted if value not in known]:
        raise AppError("VALIDATION_ERROR", "Unknown assumption link.", 422)
    return wanted


# --- assumptions ---------------------------------------------------------------------------


def list_assumptions(
    db: Session,
    startup_id: uuid.UUID,
    *,
    status: AssumptionStatus | None = None,
    risk: RiskLevel | None = None,
) -> list[Assumption]:
    query = db.query(Assumption).filter_by(startup_id=startup_id)
    if status is not None:
        query = query.filter(Assumption.status == status)
    if risk is not None:
        query = query.filter(Assumption.risk == risk)
    return query.order_by(Assumption.created_at.desc(), Assumption.id.desc()).all()


def get_assumption(db: Session, startup_id: uuid.UUID, assumption_id: Any) -> Assumption:
    row = db.query(Assumption).filter_by(id=assumption_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_assumption(
    db: Session,
    startup_id: uuid.UUID,
    *,
    statement: str,
    risk: RiskLevel,
    status: AssumptionStatus | None = None,
) -> Assumption:
    row = Assumption(
        startup_id=startup_id,
        statement=statement.strip(),
        risk=risk,
        status=status or AssumptionStatus.untested,
    )
    db.add(row)
    db.flush()
    return row


def update_assumption(
    db: Session,
    assumption: Assumption,
    *,
    actor_id: uuid.UUID,
    statement: str | None = None,
    risk: RiskLevel | None = None,
    status: AssumptionStatus | None = None,
) -> Assumption:
    """Edit an assumption. Any status may follow any other; only an actual move into validated or
    invalidated publishes an event, so a repeated save cannot double-fire it."""
    if statement is not None:
        assumption.statement = statement.strip()
    if risk is not None:
        assumption.risk = risk
    event = None
    if status is not None and status != assumption.status:
        assumption.status = status
        event = _EVENT_BY_STATUS.get(status)
    db.flush()
    if event is not None:
        event_bus.publish(
            db,
            event,
            {
                "startup_id": str(assumption.startup_id),
                "assumption_id": str(assumption.id),
                "status": assumption.status.value,
                "actor_id": str(actor_id),
            },
        )
    return assumption


def evidence_counts(
    db: Session, startup_id: uuid.UUID, assumptions: list[Assumption]
) -> dict[str, int]:
    """How many experiments and interviews link to each assumption (spec D2).

    Derived on read so it can never disagree with the rows it counts. Only this workspace's
    experiments and interviews are looked at.
    """
    counts = {str(row.id): 0 for row in assumptions}
    if not counts:
        return counts
    linked = [
        row.assumption_ids
        for row in db.query(Experiment.assumption_ids).filter_by(startup_id=startup_id).all()
    ] + [
        row.assumption_ids
        for row in db.query(Interview.assumption_ids).filter_by(startup_id=startup_id).all()
    ]
    for ids in linked:
        for value in ids or []:
            if str(value) in counts:
                counts[str(value)] += 1
    return counts


def serialize_assumption(assumption: Assumption, evidence_count: int = 0) -> dict[str, Any]:
    return {
        "id": str(assumption.id),
        "statement": assumption.statement,
        "risk": assumption.risk.value,
        "status": assumption.status.value,
        "evidence_count": evidence_count,
        "created_at": assumption.created_at.isoformat(),
        "updated_at": assumption.updated_at.isoformat(),
    }


# --- experiments ---------------------------------------------------------------------------


def list_experiments(
    db: Session,
    startup_id: uuid.UUID,
    *,
    type: ExperimentType | None = None,
    status: ExperimentStatus | None = None,
) -> list[Experiment]:
    query = db.query(Experiment).filter_by(startup_id=startup_id)
    if type is not None:
        query = query.filter(Experiment.type == type)
    if status is not None:
        query = query.filter(Experiment.status == status)
    return query.order_by(Experiment.created_at.desc(), Experiment.id.desc()).all()


def get_experiment(db: Session, startup_id: uuid.UUID, experiment_id: Any) -> Experiment:
    row = db.query(Experiment).filter_by(id=experiment_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_experiment(
    db: Session,
    startup_id: uuid.UUID,
    *,
    name: str,
    type: ExperimentType,
    config: dict[str, Any] | None = None,
    status: ExperimentStatus | None = None,
    metrics: dict[str, Any] | None = None,
    assumption_ids: Any = None,
) -> Experiment:
    row = Experiment(
        startup_id=startup_id,
        name=name.strip(),
        type=type,
        config=config or {},
        status=status or ExperimentStatus.draft,
        metrics=metrics or {},
        assumption_ids=link_ids(db, startup_id, assumption_ids),
    )
    db.add(row)
    db.flush()
    return row


def update_experiment(
    db: Session,
    experiment: Experiment,
    *,
    name: str | None = None,
    type: ExperimentType | None = None,
    config: dict[str, Any] | None = None,
    status: ExperimentStatus | None = None,
    metrics: dict[str, Any] | None = None,
    assumption_ids: Any = None,
) -> Experiment:
    if name is not None:
        experiment.name = name.strip()
    if type is not None:
        experiment.type = type
    if config is not None:
        experiment.config = config
    if status is not None:
        experiment.status = status
    if metrics is not None:
        experiment.metrics = metrics
    if assumption_ids is not None:
        experiment.assumption_ids = link_ids(db, experiment.startup_id, assumption_ids)
    db.flush()
    return experiment


def serialize_experiment(experiment: Experiment) -> dict[str, Any]:
    return {
        "id": str(experiment.id),
        "name": experiment.name,
        "type": experiment.type.value,
        "status": experiment.status.value,
        "config": experiment.config,
        "metrics": experiment.metrics,
        "assumption_ids": experiment.assumption_ids,
        "created_at": experiment.created_at.isoformat(),
        "updated_at": experiment.updated_at.isoformat(),
    }


def _counter(value: Any) -> int:
    """Read one metric as a count; anything odd (missing, text, negative) reads as 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def smoke_test_stats(db: Session, startup_id: uuid.UUID, experiment_id: Any) -> dict[str, Any]:
    """Funnel read for a smoke test. 404 for anything that is not one, so the route cannot be
    used to discover other experiment types."""
    experiment = get_experiment(db, startup_id, experiment_id)
    if experiment.type != ExperimentType.smoke_test:
        raise NotFound()
    metrics = experiment.metrics or {}
    visits = _counter(metrics.get("visits"))
    signups = _counter(metrics.get("signups"))
    return {
        "experiment_id": str(experiment.id),
        "name": experiment.name,
        "status": experiment.status.value,
        "visits": visits,
        "signups": signups,
        "conversion": round(100 * signups / visits, 1) if visits else 0.0,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/services/validation/ -v`
Expected: PASS (all, including Task 2's).

- [ ] **Step 6: Commit**

```bash
git add app/services/validation/service.py tests/services/validation/test_assumptions.py tests/services/validation/test_experiments.py
git commit -m "feat(validation): assumptions and experiments service"
```


---

### Task 4: Service — interviews, surveys, the public token and analytics

Appends to the same `app/services/validation/service.py`, after replacing its import block.

**Files:**
- Modify: `app/services/validation/service.py` (replace the imports; append the second half)
- Test: `tests/services/validation/test_interviews.py`, `tests/services/validation/test_surveys.py`

**Interfaces:**
- Consumes: everything from Task 3; `validate_questions` (Task 2); `hash_token`.
- Produces:
  - `list_interviews(...)`, `get_interview(...)`, `create_interview(...)`, `update_interview(...)`,
    `serialize_interview(...)`
  - `list_surveys(...)`, `get_survey(...)`, `create_survey(...)`, `update_survey(...)` →
    `(survey, raw_token | None)`, `response_counts(...)`, `serialize_survey(...)`,
    `survey_analytics(...)`

- [ ] **Step 1: Write the failing interview tests**

```python
# tests/services/validation/test_interviews.py
import uuid
from datetime import date

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import InterviewVerdict, RiskLevel
from app.services.validation.service import (
    create_assumption,
    create_interview,
    get_interview,
    list_interviews,
    serialize_interview,
    update_interview,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, s, **kw):
    kw.setdefault("interviewee", "Ada")
    kw.setdefault("held_on", date(2026, 9, 20))
    kw.setdefault("verdict", InterviewVerdict.supports)
    return create_interview(db, s.id, **kw)


def test_defaults_are_empty_not_null(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    assert row.notes == "" and row.key_quotes == [] and row.assumption_ids == []
    assert row.segment is None


def test_list_is_most_recent_first_and_filters(db):
    _u, s = _ctx(db)
    assumption = create_assumption(db, s.id, statement="Pay", risk=RiskLevel.high)
    _new(db, s, interviewee="Older", held_on=date(2026, 9, 1), segment="fintech")
    newer = _new(
        db,
        s,
        interviewee="Newer",
        held_on=date(2026, 9, 18),
        segment="retail",
        verdict=InterviewVerdict.contradicts,
        assumption_ids=[str(assumption.id)],
    )
    assert [i.id for i in list_interviews(db, s.id)][0] == newer.id
    assert len(list_interviews(db, s.id, segment="fintech")) == 1
    assert len(list_interviews(db, s.id, verdict=InterviewVerdict.contradicts)) == 1
    assert len(list_interviews(db, s.id, assumption_id=str(assumption.id))) == 1


def test_quotes_must_be_a_list_of_text(db):
    _u, s = _ctx(db)
    with pytest.raises(AppError) as exc:
        _new(db, s, key_quotes="just one quote")
    assert exc.value.http_status == 422
    with pytest.raises(AppError):
        _new(db, s, key_quotes=["fine", 7])
    row = _new(db, s, key_quotes=["  I would pay  "])
    assert row.key_quotes == ["I would pay"]


def test_cross_workspace_is_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = _new(db, s)
    with pytest.raises(NotFound):
        get_interview(db, other.id, mine.id)
    with pytest.raises(NotFound):
        get_interview(db, s.id, uuid.uuid4())


def test_update_changes_only_what_is_given(db):
    _u, s = _ctx(db)
    row = _new(db, s, notes="First pass")
    update_interview(db, row, verdict=InterviewVerdict.neutral, segment="fintech")
    assert row.verdict == InterviewVerdict.neutral and row.segment == "fintech"
    assert row.notes == "First pass" and row.interviewee == "Ada"


def test_serialize_shape(db):
    _u, s = _ctx(db)
    row = _new(db, s, notes="Talked pricing", key_quotes=["Take my money"])
    out = serialize_interview(row)
    assert out["id"] == str(row.id) and out["interviewee"] == "Ada"
    assert out["held_on"] == "2026-09-20" and out["verdict"] == "supports"
    assert out["notes"] == "Talked pricing" and out["key_quotes"] == ["Take my money"]
    assert out["segment"] is None and out["assumption_ids"] == []
```

- [ ] **Step 2: Write the failing survey tests**

```python
# tests/services/validation/test_surveys.py
import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import NotFound
from app.db.models.enums import SurveyStatus
from app.db.models.validation import SurveyResponse
from app.services.auth.sessions import hash_token
from app.services.validation.service import (
    create_survey,
    get_survey,
    list_surveys,
    response_counts,
    serialize_survey,
    survey_analytics,
    update_survey,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _questions():
    return [
        {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
        {"type": "nps", "prompt": "Recommend us?"},
        {"type": "open", "prompt": "Anything else?"},
    ]


def _answer(db, survey, answers):
    db.add(
        SurveyResponse(
            survey_id=survey.id,
            startup_id=survey.startup_id,
            answers=answers,
            submitted_at=datetime.now(UTC),
        )
    )
    db.flush()


def test_new_surveys_are_drafts_with_no_link(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    assert survey.status == SurveyStatus.draft and survey.token_hash is None
    assert [q["id"] for q in survey.questions]  # ids assigned by the validator


def test_opening_returns_the_raw_token_once_and_stores_only_its_hash(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    survey, raw = update_survey(db, survey, status=SurveyStatus.open)
    assert raw and len(raw) >= 32
    assert survey.token_hash == hash_token(raw)
    # Re-opening does not rotate the token, and never hands it out again.
    survey, again = update_survey(db, survey, status=SurveyStatus.closed)
    assert again is None
    survey, again = update_survey(db, survey, status=SurveyStatus.open)
    assert again is None and survey.token_hash == hash_token(raw)


def test_cross_workspace_is_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = create_survey(db, s.id, title="Mine")
    with pytest.raises(NotFound):
        get_survey(db, other.id, mine.id)
    with pytest.raises(NotFound):
        get_survey(db, s.id, uuid.uuid4())


def test_list_and_response_counts(db):
    _u, s = _ctx(db)
    first = create_survey(db, s.id, title="First", questions=_questions())
    second = create_survey(db, s.id, title="Second")
    _answer(db, first, {first.questions[0]["id"]: "A"})
    counts = response_counts(db, s.id, list_surveys(db, s.id))
    assert counts[str(first.id)] == 1 and counts[str(second.id)] == 0
    assert response_counts(db, s.id, []) == {}


def test_analytics_counts_every_option_and_the_completion_rate(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    choice, nps, open_q = (q["id"] for q in survey.questions)
    _answer(db, survey, {choice: "A", nps: 9, open_q: "loved it"})
    _answer(db, survey, {choice: "A", nps: 7})
    _answer(db, survey, {nps: 5})  # misses the required choice question
    out = survey_analytics(db, s.id, survey.id)
    assert out["responses"] == 3
    assert out["completion_rate"] == 67  # 2 of 3 answered every required question
    by_id = {q["id"]: q for q in out["questions"]}
    assert by_id[choice]["counts"] == {"A": 2, "B": 0}
    assert by_id[choice]["answered"] == 2
    assert by_id[nps]["counts"] == {"5": 1, "7": 1, "9": 1}
    assert by_id[nps]["average"] == 7.0
    assert by_id[open_q]["answered"] == 1
    assert "counts" not in by_id[open_q]  # open answers are counted, never listed


def test_analytics_on_an_empty_survey(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    out = survey_analytics(db, s.id, survey.id)
    assert out["responses"] == 0 and out["completion_rate"] == 0
    assert out["questions"][1]["average"] == 0.0


def test_serialize_shape_never_exposes_the_token(db):
    _u, s = _ctx(db)
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    survey, _raw = update_survey(db, survey, status=SurveyStatus.open)
    out = serialize_survey(survey, 4)
    assert out["id"] == str(survey.id) and out["title"] == "Pricing"
    assert out["status"] == "open" and out["response_count"] == 4
    assert out["has_link"] is True
    assert "token" not in out and "token_hash" not in out
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/validation/ -v`
Expected: FAIL — `ImportError: cannot import name 'create_interview' from
'app.services.validation.service'`.

- [ ] **Step 4: Replace the import block of `app/services/validation/service.py`**

```python
import secrets
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    RiskLevel,
    SurveyStatus,
)
from app.db.models.validation import Assumption, Experiment, Interview, Survey, SurveyResponse
from app.platform.events import event_bus
from app.services.auth.sessions import hash_token
from app.services.validation.questions import validate_questions
```

- [ ] **Step 5: Append the interviews and surveys half**

```python
# --- interviews ----------------------------------------------------------------------------


def _quotes(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AppError("VALIDATION_ERROR", "Key quotes must be a list.", 422)
    if any(not isinstance(quote, str) or not quote.strip() for quote in value):
        raise AppError("VALIDATION_ERROR", "Each key quote must be non-empty text.", 422)
    return [quote.strip() for quote in value]


def list_interviews(
    db: Session,
    startup_id: uuid.UUID,
    *,
    segment: str | None = None,
    verdict: InterviewVerdict | None = None,
    assumption_id: str | None = None,
) -> list[Interview]:
    query = db.query(Interview).filter_by(startup_id=startup_id)
    if segment is not None:
        query = query.filter(Interview.segment == segment)
    if verdict is not None:
        query = query.filter(Interview.verdict == verdict)
    rows = query.order_by(Interview.held_on.desc(), Interview.created_at.desc()).all()
    if assumption_id is not None:
        rows = [row for row in rows if str(assumption_id) in (row.assumption_ids or [])]
    return rows


def get_interview(db: Session, startup_id: uuid.UUID, interview_id: Any) -> Interview:
    row = db.query(Interview).filter_by(id=interview_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_interview(
    db: Session,
    startup_id: uuid.UUID,
    *,
    interviewee: str,
    held_on: date,
    verdict: InterviewVerdict,
    segment: str | None = None,
    notes: str = "",
    key_quotes: Any = None,
    assumption_ids: Any = None,
) -> Interview:
    row = Interview(
        startup_id=startup_id,
        interviewee=interviewee.strip(),
        segment=segment,
        held_on=held_on,
        notes=notes or "",
        key_quotes=_quotes(key_quotes),
        verdict=verdict,
        assumption_ids=link_ids(db, startup_id, assumption_ids),
    )
    db.add(row)
    db.flush()
    return row


def update_interview(
    db: Session,
    interview: Interview,
    *,
    interviewee: str | None = None,
    segment: str | None = None,
    held_on: date | None = None,
    notes: str | None = None,
    key_quotes: Any = None,
    verdict: InterviewVerdict | None = None,
    assumption_ids: Any = None,
) -> Interview:
    if interviewee is not None:
        interview.interviewee = interviewee.strip()
    if segment is not None:
        interview.segment = segment
    if held_on is not None:
        interview.held_on = held_on
    if notes is not None:
        interview.notes = notes
    if key_quotes is not None:
        interview.key_quotes = _quotes(key_quotes)
    if verdict is not None:
        interview.verdict = verdict
    if assumption_ids is not None:
        interview.assumption_ids = link_ids(db, interview.startup_id, assumption_ids)
    db.flush()
    return interview


def serialize_interview(interview: Interview) -> dict[str, Any]:
    return {
        "id": str(interview.id),
        "interviewee": interview.interviewee,
        "segment": interview.segment,
        "held_on": interview.held_on.isoformat(),
        "notes": interview.notes,
        "key_quotes": interview.key_quotes,
        "verdict": interview.verdict.value,
        "assumption_ids": interview.assumption_ids,
        "created_at": interview.created_at.isoformat(),
        "updated_at": interview.updated_at.isoformat(),
    }


# --- surveys -------------------------------------------------------------------------------


def list_surveys(db: Session, startup_id: uuid.UUID) -> list[Survey]:
    return (
        db.query(Survey)
        .filter_by(startup_id=startup_id)
        .order_by(Survey.created_at.desc(), Survey.id.desc())
        .all()
    )


def get_survey(db: Session, startup_id: uuid.UUID, survey_id: Any) -> Survey:
    row = db.query(Survey).filter_by(id=survey_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def create_survey(
    db: Session, startup_id: uuid.UUID, *, title: str, questions: Any = None
) -> Survey:
    row = Survey(
        startup_id=startup_id,
        title=title.strip(),
        questions=validate_questions(questions or []),
    )
    db.add(row)
    db.flush()
    return row


def update_survey(
    db: Session,
    survey: Survey,
    *,
    title: str | None = None,
    questions: Any = None,
    status: SurveyStatus | None = None,
) -> tuple[Survey, str | None]:
    """Edit a survey. Opening it for the first time mints the public token and returns the raw
    value **once**; only its hash is stored, and re-opening never hands it out again (spec §4)."""
    raw: str | None = None
    if title is not None:
        survey.title = title.strip()
    if questions is not None:
        survey.questions = validate_questions(questions)
    if status is not None:
        survey.status = status
        if status == SurveyStatus.open and survey.token_hash is None:
            raw = secrets.token_urlsafe(32)
            survey.token_hash = hash_token(raw)
    db.flush()
    return survey, raw


def response_counts(
    db: Session, startup_id: uuid.UUID, surveys: list[Survey]
) -> dict[str, int]:
    counts = {str(row.id): 0 for row in surveys}
    if not counts:
        return counts
    rows = (
        db.query(SurveyResponse.survey_id, func.count(SurveyResponse.id))
        .filter(SurveyResponse.startup_id == startup_id)
        .group_by(SurveyResponse.survey_id)
        .all()
    )
    for survey_id, count in rows:
        if str(survey_id) in counts:
            counts[str(survey_id)] = count
    return counts


def serialize_survey(survey: Survey, response_count: int = 0) -> dict[str, Any]:
    """The owner's view of a survey. The token never appears here, in any form."""
    return {
        "id": str(survey.id),
        "title": survey.title,
        "status": survey.status.value,
        "questions": survey.questions,
        "response_count": response_count,
        "has_link": survey.token_hash is not None,
        "created_at": survey.created_at.isoformat(),
        "updated_at": survey.updated_at.isoformat(),
    }


def survey_analytics(db: Session, startup_id: uuid.UUID, survey_id: Any) -> dict[str, Any]:
    """Per-question counts and the completion rate (spec section 5).

    Open answers are counted, never listed: reading raw responses is a follow-up.
    """
    survey = get_survey(db, startup_id, survey_id)
    rows = db.query(SurveyResponse).filter_by(survey_id=survey.id).all()
    total = len(rows)
    questions = survey.questions or []
    required = [question["id"] for question in questions if question.get("required")]
    complete = sum(
        1 for row in rows if all(key in (row.answers or {}) for key in required)
    )
    out: list[dict[str, Any]] = []
    for question in questions:
        given = [
            (row.answers or {})[question["id"]]
            for row in rows
            if question["id"] in (row.answers or {})
        ]
        entry: dict[str, Any] = {
            "id": question["id"],
            "type": question["type"],
            "prompt": question["prompt"],
            "answered": len(given),
        }
        if question["type"] == "choice":
            entry["counts"] = {
                option: sum(1 for value in given if value == option)
                for option in question.get("options", [])
            }
        elif question["type"] in ("scale", "nps"):
            numbers = [
                value for value in given if isinstance(value, int) and not isinstance(value, bool)
            ]
            entry["counts"] = {str(value): numbers.count(value) for value in sorted(set(numbers))}
            entry["average"] = round(sum(numbers) / len(numbers), 1) if numbers else 0.0
        out.append(entry)
    return {
        "survey_id": str(survey.id),
        "title": survey.title,
        "status": survey.status.value,
        "responses": total,
        "completion_rate": round(100 * complete / total) if total else 0,
        "questions": out,
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `poetry run pytest tests/services/validation/ -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add app/services/validation/service.py tests/services/validation/test_interviews.py tests/services/validation/test_surveys.py
git commit -m "feat(validation): interviews, surveys, public token and analytics"
```


---

### Task 5: The public survey surface, and moving the `Limiter`

The security-sensitive task. It copies `app/services/documents/shares.py` (`open_shared`) and the
public `POST /sign/{token}` handling exactly, as the lead required.

**Files:**
- Create: `app/services/validation/public.py`
- Modify: `app/core/rate_limit.py` (hold the `Limiter` instance and its key function)
- Modify: `app/main.py` (import `limiter` instead of building it)
- Test: `tests/services/validation/test_public.py`

**Interfaces:**
- Consumes: `hash_token`, `validate_answers`, `Survey`, `SurveyResponse`, `NotFound`.
- Produces: `open_survey(db, token)`, `public_view(survey)`, `submit_response(db, token, answers)`;
  and `limiter` importable from `app.core.rate_limit`.

- [ ] **Step 1: Write the failing public-surface tests**

```python
# tests/services/validation/test_public.py
from datetime import UTC, datetime

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import SurveyStatus
from app.db.models.validation import SurveyResponse
from app.services.validation.public import open_survey, public_view, submit_response
from app.services.validation.service import create_survey, update_survey
from tests.factories import create_startup, create_user


def _questions():
    return [
        {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
        {"type": "open", "prompt": "Anything else?"},
    ]


def _open_survey(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    survey = create_survey(db, s.id, title="Pricing", questions=_questions())
    survey, raw = update_survey(db, survey, status=SurveyStatus.open)
    return s, survey, raw


def test_a_valid_token_opens_the_survey(db):
    _s, survey, raw = _open_survey(db)
    assert open_survey(db, raw).id == survey.id


def test_unknown_draft_and_closed_all_raise_the_same_404(db):
    _s, survey, raw = _open_survey(db)
    with pytest.raises(NotFound):
        open_survey(db, "not-a-real-token")
    update_survey(db, survey, status=SurveyStatus.closed)
    with pytest.raises(NotFound):
        open_survey(db, raw)
    update_survey(db, survey, status=SurveyStatus.draft)
    with pytest.raises(NotFound):
        open_survey(db, raw)


def test_the_public_view_shows_the_form_and_nothing_else(db):
    _s, survey, raw = _open_survey(db)
    view = public_view(open_survey(db, raw))
    assert set(view) == {"title", "questions"}
    assert view["title"] == "Pricing"
    assert set(view["questions"][0]) == {"id", "type", "prompt", "required", "options"}
    assert set(view["questions"][1]) == {"id", "type", "prompt", "required"}
    # Nothing about the workspace, the owner, the status or the responses.
    flat = str(view)
    assert str(survey.startup_id) not in flat
    assert str(survey.id) not in flat
    assert "response" not in flat and "status" not in flat


def test_submitting_stores_clean_answers_and_copies_the_workspace(db):
    s, survey, raw = _open_survey(db)
    choice, open_q = (q["id"] for q in survey.questions)
    before = datetime.now(UTC)
    row = submit_response(db, raw, {choice: "A", open_q: "  loved it  "})
    assert row.answers == {choice: "A", open_q: "loved it"}
    assert row.startup_id == s.id and row.survey_id == survey.id
    assert row.submitted_at >= before


def test_bad_answers_are_rejected_with_422(db):
    _s, survey, raw = _open_survey(db)
    choice, _open_q = (q["id"] for q in survey.questions)
    for answers in ({}, {choice: "C"}, {"not-a-question": "hi"}):
        with pytest.raises(AppError) as exc:
            submit_response(db, raw, answers)
        assert exc.value.http_status == 422


def test_repeat_submissions_are_allowed(db):
    _s, survey, raw = _open_survey(db)
    choice, _open_q = (q["id"] for q in survey.questions)
    submit_response(db, raw, {choice: "A"})
    submit_response(db, raw, {choice: "B"})
    assert db.query(SurveyResponse).filter_by(survey_id=survey.id).count() == 2


def test_a_closed_survey_stops_accepting_answers(db):
    _s, survey, raw = _open_survey(db)
    choice, _open_q = (q["id"] for q in survey.questions)
    update_survey(db, survey, status=SurveyStatus.closed)
    with pytest.raises(NotFound):
        submit_response(db, raw, {choice: "A"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/validation/test_public.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.validation.public'`.

- [ ] **Step 3: Write the public surface**

`app/services/validation/public.py`:

```python
"""The public survey surface (Module 09, spec section 4).

Addressed by an unguessable token whose SHA-256 hash alone is stored, exactly like the Module 18
share and signing links (`app/services/documents/shares.py`). Unknown, draft and closed surveys all
raise the same `NotFound`, so a token cannot be probed, and nothing returned here mentions the
workspace, its members, or anyone else's answers.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import SurveyStatus
from app.db.models.validation import Survey, SurveyResponse
from app.services.auth.sessions import hash_token
from app.services.validation.questions import validate_answers


def open_survey(db: Session, token: str) -> Survey:
    survey = db.query(Survey).filter_by(token_hash=hash_token(token)).first()
    # Uniform 404 for unknown / draft / closed — never reveal which one it was.
    if survey is None or survey.status != SurveyStatus.open:
        raise NotFound()
    return survey


def public_view(survey: Survey) -> dict[str, Any]:
    """Exactly what a respondent may see: the title and the questions. Nothing else — no ids of
    the survey or workspace, no status, no counts."""
    return {
        "title": survey.title,
        "questions": [
            {
                "id": question["id"],
                "type": question["type"],
                "prompt": question["prompt"],
                "required": question["required"],
                **({"options": question["options"]} if question["type"] == "choice" else {}),
            }
            for question in survey.questions or []
        ],
    }


def submit_response(db: Session, token: str, answers: Any) -> SurveyResponse:
    """Record one anonymous response. Repeats are allowed; nothing about the respondent is
    stored beyond the moment they answered (spec D3)."""
    survey = open_survey(db, token)
    row = SurveyResponse(
        survey_id=survey.id,
        startup_id=survey.startup_id,
        answers=validate_answers(survey.questions or [], answers),
        submitted_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row
```

- [ ] **Step 4: Move the `Limiter` into `app/core/rate_limit.py`**

The public response route needs `@limiter.limit(...)`, and an endpoint module cannot import
`app/main.py` (which imports the endpoints back). The instance moves; nothing about its behaviour
changes (spec D5).

**4a — add to the imports at the top of `app/core/rate_limit.py`:**

```python
from fastapi import Request
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
```

**4b — append to the end of `app/core/rate_limit.py`, moved verbatim from `app/main.py`:**

```python
def _rate_limit_key(request: Request) -> str:
    """Key rate limits on the authenticated user when possible, falling back
    to remote address for unauthenticated requests. This keeps limits tied to
    the caller rather than the source IP, so users behind a shared IP (NAT,
    corporate proxy) aren't penalized by each other's traffic.

    Lives here rather than in app/main.py so endpoint modules can import
    `limiter` for per-route `@limiter.limit(...)` decorators without importing
    the app (which imports them back).
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = jwt.decode(auth[7:], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass
    return get_remote_address(request)


limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
```

**4c — in `app/main.py`:** delete `_rate_limit_key` and the `limiter = Limiter(...)` block (the
`# Rate limiting` comment through the `limiter = Limiter(...)` call), keep
`app.state.limiter = limiter`, and change the imports:

- remove `from jose import JWTError, jwt`
- remove `from slowapi import Limiter`
- remove `from slowapi.util import get_remote_address`
- keep `from slowapi.errors import RateLimitExceeded` and `from slowapi.middleware import
  SlowAPIMiddleware`
- add `limiter` to the existing `app.core.rate_limit` import:

```python
from app.core.rate_limit import (
    install_included_router_support,
    limiter,
    verify_included_router_resolution,
)
```

- [ ] **Step 5: Run the tests to verify nothing broke**

```bash
poetry run pytest tests/services/validation/ tests/api/test_rate_limit.py -v
poetry run python -c "import app.main"   # the app still boots
```

Expected: PASS. `tests/api/test_rate_limit.py` is the guard that the limiter still works at all;
the per-route limit itself is asserted in Task 6, once the route exists.

- [ ] **Step 6: Commit**

```bash
git add app/services/validation/public.py app/core/rate_limit.py app/main.py tests/services/validation/test_public.py
git commit -m "feat(validation): public survey token surface; move Limiter for per-route limits"
```


---

### Task 6: Schemas, endpoints, router registration

**Files:**
- Create: `app/schemas/validation.py`
- Create: `app/api/v1/endpoints/validation.py`
- Modify: `app/api/v1/api.py` (register the router at `prefix="/validation"`)
- Test: `tests/api/test_validation.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5; `require_role`, `get_verified_user`, `get_db`,
  `success_response`, `job_dispatcher`, `limiter`.
- Produces: the 16 member routes and 2 public routes of spec §5 and §4.

- [ ] **Step 1: Request schemas**

`app/schemas/validation.py`:

```python
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    RiskLevel,
    SurveyStatus,
)


class AssumptionCreate(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    risk: RiskLevel
    status: AssumptionStatus | None = None


class AssumptionUpdate(BaseModel):
    statement: str | None = Field(default=None, min_length=1, max_length=2000)
    risk: RiskLevel | None = None
    status: AssumptionStatus | None = None


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: ExperimentType
    config: dict[str, Any] = Field(default_factory=dict)
    status: ExperimentStatus | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    assumption_ids: list[str] = Field(default_factory=list)


class ExperimentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: ExperimentType | None = None
    config: dict[str, Any] | None = None
    status: ExperimentStatus | None = None
    metrics: dict[str, Any] | None = None
    assumption_ids: list[str] | None = None


class InterviewCreate(BaseModel):
    interviewee: str = Field(min_length=1, max_length=255)
    held_on: date
    verdict: InterviewVerdict
    segment: str | None = Field(default=None, max_length=120)
    notes: str = ""
    key_quotes: list[Any] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)


class InterviewUpdate(BaseModel):
    interviewee: str | None = Field(default=None, min_length=1, max_length=255)
    held_on: date | None = None
    verdict: InterviewVerdict | None = None
    segment: str | None = Field(default=None, max_length=120)
    notes: str | None = None
    key_quotes: list[Any] | None = None
    assumption_ids: list[str] | None = None


class SurveyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    questions: list[Any] = Field(default_factory=list)


class SurveyUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    questions: list[Any] | None = None
    status: SurveyStatus | None = None


class ResponseSubmit(BaseModel):
    """The only body a member of the public ever sends."""

    answers: dict[str, Any] = Field(default_factory=dict)


class ScriptsGenerate(BaseModel):
    assumption_ids: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Write the failing API tests**

```python
# tests/api/test_validation.py
from datetime import UTC, datetime

import pytest

from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/validation"

NON_MEMBER_ROLES = [
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]

ROUTES = [
    ("GET", "/assumptions", None),
    ("POST", "/assumptions", {"statement": "They will pay", "risk": "high"}),
    ("GET", "/experiments", None),
    ("POST", "/experiments", {"name": "Fake door", "type": "smoke_test"}),
    ("GET", "/interviews", None),
    ("GET", "/surveys", None),
    ("POST", "/surveys", {"title": "Pricing"}),
    ("POST", "/synthesize", {}),
    ("POST", "/scripts/generate", {}),
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, verified=True, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC) if verified else None)
    if startup is None:
        startup = create_startup(db, owner=u)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def _call(client, method, path, body, headers=None):
    return client.request(method, BASE + path, json=body, headers=headers)


def _open_survey(client, headers, questions=None):
    questions = questions or [
        {"type": "choice", "prompt": "Pick one", "options": ["A", "B"], "required": True},
        {"type": "open", "prompt": "Anything else?"},
    ]
    created = client.post(
        f"{BASE}/surveys", json={"title": "Pricing", "questions": questions}, headers=headers
    ).json()["data"]
    opened = client.patch(
        f"{BASE}/surveys/{created['id']}", json={"status": "open"}, headers=headers
    ).json()["data"]
    return opened["survey"], opened["public_token"]


@pytest.mark.parametrize("role", NON_MEMBER_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_other_roles_are_forbidden(client, db, role, method, path, body):
    _founder, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    assert _call(client, method, path, body, h).status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unauthenticated_is_rejected(client, method, path, body):
    assert _call(client, method, path, body).status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unverified_member_is_forbidden(client, db, method, path, body):
    _u, _s, h = _member(db, verified=False)
    r = _call(client, method, path, body, h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


@pytest.mark.parametrize("role", [MembershipRole.founder, MembershipRole.team_member])
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_founders_and_team_members_are_allowed(client, db, role, method, path, body):
    _u, _s, h = _member(db, role=role)
    assert _call(client, method, path, body, h).status_code in (200, 201, 202)


def test_assumption_crud_and_evidence_count(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/assumptions", json={"statement": "They will pay", "risk": "high"}, headers=h
    )
    assert created.status_code == 201
    assumption = created.json()["data"]
    assert assumption["status"] == "untested" and assumption["evidence_count"] == 0

    client.post(
        f"{BASE}/experiments",
        json={
            "name": "Fake door",
            "type": "smoke_test",
            "assumption_ids": [assumption["id"]],
        },
        headers=h,
    )
    listed = client.get(f"{BASE}/assumptions", headers=h).json()["data"]["assumptions"]
    assert listed[0]["evidence_count"] == 1

    moved = client.patch(
        f"{BASE}/assumptions/{assumption['id']}", json={"status": "validated"}, headers=h
    )
    assert moved.status_code == 200 and moved.json()["data"]["status"] == "validated"
    assert (
        client.get(f"{BASE}/assumptions?status=validated", headers=h)
        .json()["data"]["assumptions"][0]["id"]
        == assumption["id"]
    )


def test_unknown_ids_are_404(client, db):
    _u, _s, h = _member(db)
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.patch(f"{BASE}/assumptions/{missing}", json={}, headers=h).status_code == 404
    assert client.get(f"{BASE}/smoke-tests/{missing}/stats", headers=h).status_code == 404
    assert client.get(f"{BASE}/surveys/{missing}/analytics", headers=h).status_code == 404


def test_another_workspace_cannot_read_or_edit(client, db):
    _u, startup, h = _member(db)
    _outsider, _own, outsider_h = _member(db)
    created = client.post(
        f"{BASE}/assumptions", json={"statement": "Mine", "risk": "low"}, headers=h
    ).json()["data"]
    assert (
        client.patch(
            f"{BASE}/assumptions/{created['id']}", json={"risk": "high"}, headers=outsider_h
        ).status_code
        == 404
    )
    assert client.get(f"{BASE}/assumptions", headers=outsider_h).json()["data"]["assumptions"] == []


def test_smoke_test_stats(client, db):
    _u, _s, h = _member(db)
    created = client.post(
        f"{BASE}/experiments",
        json={"name": "Fake door", "type": "smoke_test", "metrics": {"visits": 50, "signups": 5}},
        headers=h,
    ).json()["data"]
    stats = client.get(f"{BASE}/smoke-tests/{created['id']}/stats", headers=h).json()["data"]
    assert stats["conversion"] == 10.0


def test_opening_a_survey_returns_the_token_once(client, db):
    _u, _s, h = _member(db)
    survey, token = _open_survey(client, h)
    assert token and survey["status"] == "open" and survey["has_link"] is True
    again = client.patch(
        f"{BASE}/surveys/{survey['id']}", json={"status": "closed"}, headers=h
    ).json()["data"]
    assert again["public_token"] is None


def test_public_read_and_submit_need_no_login(client, db):
    _u, _s, h = _member(db)
    survey, token = _open_survey(client, h)

    form = client.get(f"{BASE}/surveys/{token}")
    assert form.status_code == 200
    data = form.json()["data"]
    assert set(data) == {"title", "questions"}
    assert str(survey["id"]) not in str(data)

    choice = data["questions"][0]["id"]
    sent = client.post(f"{BASE}/surveys/{token}/responses", json={"answers": {choice: "A"}})
    assert sent.status_code == 201
    assert sent.json()["data"] == {"received": True}


def test_public_routes_leak_nothing_when_the_token_is_wrong(client, db):
    _u, _s, h = _member(db)
    _survey, token = _open_survey(client, h)
    unknown = client.get(f"{BASE}/surveys/not-a-real-token")
    assert unknown.status_code == 404
    # A draft survey's token answers identically to an unknown one.
    draft = client.post(f"{BASE}/surveys", json={"title": "Draft"}, headers=h).json()["data"]
    assert client.get(f"{BASE}/surveys/{draft['id']}").status_code == 404
    assert (
        client.post(f"{BASE}/surveys/{token}x/responses", json={"answers": {}}).status_code == 404
    )


def test_bad_answers_are_422(client, db):
    _u, _s, h = _member(db)
    _survey, token = _open_survey(client, h)
    r = client.post(f"{BASE}/surveys/{token}/responses", json={"answers": {}})
    assert r.status_code == 422


def test_analytics_are_members_only_and_count_responses(client, db):
    _u, _s, h = _member(db)
    survey, token = _open_survey(client, h)
    form = client.get(f"{BASE}/surveys/{token}").json()["data"]
    choice = form["questions"][0]["id"]
    client.post(f"{BASE}/surveys/{token}/responses", json={"answers": {choice: "A"}})
    assert client.get(f"{BASE}/surveys/{survey['id']}/analytics").status_code == 401
    out = client.get(f"{BASE}/surveys/{survey['id']}/analytics", headers=h).json()["data"]
    assert out["responses"] == 1 and out["completion_rate"] == 100
    assert out["questions"][0]["counts"] == {"A": 1, "B": 0}


def test_job_stubs_enqueue_and_return_metadata(client, db):
    _u, _s, h = _member(db)
    for path in ("/synthesize", "/scripts/generate"):
        r = client.post(BASE + path, json={}, headers=h)
        assert r.status_code == 202
        body = r.json()["data"]
        assert body["job_id"] and body["status"] == "queued"


def test_the_public_response_route_carries_its_own_rate_limit():
    registered = " ".join(str(key) for key in limiter._route_limits)
    assert "submit_survey_response_endpoint" in registered
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/api/test_validation.py -q`
Expected: FAIL — every `/api/v1/validation` route answers 404 because the router does not exist.

- [ ] **Step 4: Create the router**

`app/api/v1/endpoints/validation.py`:

```python
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.envelope import success_response
from app.core.rate_limit import limiter
from app.db.models.enums import (
    AssumptionStatus,
    ExperimentStatus,
    ExperimentType,
    InterviewVerdict,
    MembershipRole,
    RiskLevel,
)
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role
from app.platform.jobs import job_dispatcher
from app.schemas.validation import (
    AssumptionCreate,
    AssumptionUpdate,
    ExperimentCreate,
    ExperimentUpdate,
    InterviewCreate,
    InterviewUpdate,
    ResponseSubmit,
    ScriptsGenerate,
    SurveyCreate,
    SurveyUpdate,
)
from app.services.validation.public import open_survey, public_view, submit_response
from app.services.validation.service import (
    create_assumption,
    create_experiment,
    create_interview,
    create_survey,
    evidence_counts,
    get_assumption,
    get_experiment,
    get_interview,
    get_survey,
    list_assumptions,
    list_experiments,
    list_interviews,
    list_surveys,
    response_counts,
    serialize_assumption,
    serialize_experiment,
    serialize_interview,
    serialize_survey,
    smoke_test_stats,
    survey_analytics,
    update_assumption,
    update_experiment,
    update_interview,
    update_survey,
)

router = APIRouter()
# Founders and team members only (spec D6, PRD line 457).
_member = require_role(MembershipRole.founder, MembershipRole.team_member)


# --- assumptions ---------------------------------------------------------------------------


@router.get("/assumptions")
def list_assumptions_endpoint(
    status: AssumptionStatus | None = None,
    risk: RiskLevel | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_assumptions(db, membership.startup_id, status=status, risk=risk)
    counts = evidence_counts(db, membership.startup_id, rows)
    return success_response(
        {"assumptions": [serialize_assumption(row, counts[str(row.id)]) for row in rows]}
    )


@router.post("/assumptions", status_code=status.HTTP_201_CREATED)
def create_assumption_endpoint(
    body: AssumptionCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_assumption(
        db, membership.startup_id, statement=body.statement, risk=body.risk, status=body.status
    )
    db.commit()
    return success_response(serialize_assumption(row, 0))


@router.patch("/assumptions/{assumption_id}")
def update_assumption_endpoint(
    assumption_id: str,
    body: AssumptionUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = get_assumption(db, membership.startup_id, assumption_id)
    update_assumption(
        db,
        row,
        actor_id=membership.user_id,
        statement=body.statement,
        risk=body.risk,
        status=body.status,
    )
    db.commit()
    counts = evidence_counts(db, membership.startup_id, [row])
    return success_response(serialize_assumption(row, counts[str(row.id)]))


# --- experiments ---------------------------------------------------------------------------


@router.get("/experiments")
def list_experiments_endpoint(
    type: ExperimentType | None = None,
    status: ExperimentStatus | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_experiments(db, membership.startup_id, type=type, status=status)
    return success_response({"experiments": [serialize_experiment(row) for row in rows]})


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
def create_experiment_endpoint(
    body: ExperimentCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_experiment(
        db,
        membership.startup_id,
        name=body.name,
        type=body.type,
        config=body.config,
        status=body.status,
        metrics=body.metrics,
        assumption_ids=body.assumption_ids,
    )
    db.commit()
    return success_response(serialize_experiment(row))


@router.patch("/experiments/{experiment_id}")
def update_experiment_endpoint(
    experiment_id: str,
    body: ExperimentUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = get_experiment(db, membership.startup_id, experiment_id)
    update_experiment(
        db,
        row,
        name=body.name,
        type=body.type,
        config=body.config,
        status=body.status,
        metrics=body.metrics,
        assumption_ids=body.assumption_ids,
    )
    db.commit()
    return success_response(serialize_experiment(row))


@router.get("/smoke-tests/{experiment_id}/stats")
def smoke_test_stats_endpoint(
    experiment_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(smoke_test_stats(db, membership.startup_id, experiment_id))


# --- interviews ----------------------------------------------------------------------------


@router.get("/interviews")
def list_interviews_endpoint(
    segment: str | None = None,
    verdict: InterviewVerdict | None = None,
    assumption_id: str | None = None,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_interviews(
        db,
        membership.startup_id,
        segment=segment,
        verdict=verdict,
        assumption_id=assumption_id,
    )
    return success_response({"interviews": [serialize_interview(row) for row in rows]})


@router.post("/interviews", status_code=status.HTTP_201_CREATED)
def create_interview_endpoint(
    body: InterviewCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_interview(
        db,
        membership.startup_id,
        interviewee=body.interviewee,
        held_on=body.held_on,
        verdict=body.verdict,
        segment=body.segment,
        notes=body.notes,
        key_quotes=body.key_quotes,
        assumption_ids=body.assumption_ids,
    )
    db.commit()
    return success_response(serialize_interview(row))


@router.patch("/interviews/{interview_id}")
def update_interview_endpoint(
    interview_id: str,
    body: InterviewUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = get_interview(db, membership.startup_id, interview_id)
    update_interview(
        db,
        row,
        interviewee=body.interviewee,
        held_on=body.held_on,
        verdict=body.verdict,
        segment=body.segment,
        notes=body.notes,
        key_quotes=body.key_quotes,
        assumption_ids=body.assumption_ids,
    )
    db.commit()
    return success_response(serialize_interview(row))


# --- surveys (members) ---------------------------------------------------------------------


@router.get("/surveys")
def list_surveys_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    rows = list_surveys(db, membership.startup_id)
    counts = response_counts(db, membership.startup_id, rows)
    return success_response(
        {"surveys": [serialize_survey(row, counts[str(row.id)]) for row in rows]}
    )


@router.post("/surveys", status_code=status.HTTP_201_CREATED)
def create_survey_endpoint(
    body: SurveyCreate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = create_survey(db, membership.startup_id, title=body.title, questions=body.questions)
    db.commit()
    return success_response(serialize_survey(row, 0))


@router.patch("/surveys/{survey_id}")
def update_survey_endpoint(
    survey_id: str,
    body: SurveyUpdate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """`public_token` carries the raw token the first time the survey is opened, and is null
    every other time. It is never recoverable afterwards (spec section 4)."""
    row = get_survey(db, membership.startup_id, survey_id)
    row, raw = update_survey(
        db, row, title=body.title, questions=body.questions, status=body.status
    )
    db.commit()
    counts = response_counts(db, membership.startup_id, [row])
    return success_response(
        {"survey": serialize_survey(row, counts[str(row.id)]), "public_token": raw}
    )


@router.get("/surveys/{survey_id}/analytics")
def survey_analytics_endpoint(
    survey_id: str,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(survey_analytics(db, membership.startup_id, survey_id))


# --- surveys (public, no authentication at all) ---------------------------------------------


@router.get("/surveys/{token}")
def public_survey_endpoint(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:  # noqa: B008
    """The form a respondent fills in. Title and questions only (spec section 4)."""
    return success_response(public_view(open_survey(db, token)))


@router.post("/surveys/{token}/responses", status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def submit_survey_response_endpoint(
    request: Request,
    token: str,
    body: ResponseSubmit,
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Anonymous submission. The reply is an acknowledgement and nothing else."""
    submit_response(db, token, body.answers)
    db.commit()
    return success_response({"received": True})


# --- job stubs -----------------------------------------------------------------------------


@router.post("/synthesize", status_code=status.HTTP_202_ACCEPTED)
def synthesize_endpoint(
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    job = job_dispatcher.enqueue(
        db,
        "validation.synthesize",
        {"startup_id": str(membership.startup_id)},
        membership.startup_id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})


@router.post("/scripts/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_scripts_endpoint(
    body: ScriptsGenerate,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    job = job_dispatcher.enqueue(
        db,
        "validation.scripts.generate",
        {"startup_id": str(membership.startup_id), "assumption_ids": body.assumption_ids},
        membership.startup_id,
    )
    db.commit()
    return success_response({"job_id": str(job.id), "status": job.status.value})
```

**Route-order note:** `GET /surveys/{token}` (public) and `GET /surveys/{survey_id}/analytics`
(member) never collide — the paths differ in shape. `POST /surveys` and
`POST /surveys/{token}/responses` likewise.

- [ ] **Step 5: Register the router**

In `app/api/v1/api.py`, add `validation` to the endpoint import tuple (after `roadmap`, keeping the
list alphabetical):

```python
    roadmap,
    validation,
)
```

and add this line after the last `include_router` call:

```python
api_router.include_router(validation.router, prefix="/validation", tags=["validation"])
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `poetry run pytest tests/api/test_validation.py -q`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add app/schemas/validation.py app/api/v1/endpoints/validation.py app/api/v1/api.py tests/api/test_validation.py
git commit -m "feat(validation): /validation endpoints, public survey routes and router registration"
```


---

### Task 7: Smoke routes, live e2e, FE guide, SOP, checklist

**Files:**
- Modify: `e2e/test_smoke.py` (add the validation routes)
- Create: `e2e/test_validation.py` (+ captures under `e2e/_captures/validation/`)
- Create: `docs/fe-integration-guide-validation.md`
- Create: `docs/sop/<build-date>-validation-hub.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: Add the validation routes to the smoke test**

In `e2e/test_smoke.py`, add these entries to the route list, directly after the last existing
block (8 spaces of indentation, matching its neighbours):

```python
        # validation hub surface
        "/api/v1/validation/assumptions",
        "/api/v1/validation/assumptions/{assumption_id}",
        "/api/v1/validation/experiments",
        "/api/v1/validation/experiments/{experiment_id}",
        "/api/v1/validation/smoke-tests/{experiment_id}/stats",
        "/api/v1/validation/interviews",
        "/api/v1/validation/interviews/{interview_id}",
        "/api/v1/validation/surveys",
        "/api/v1/validation/surveys/{survey_id}",
        "/api/v1/validation/surveys/{survey_id}/analytics",
        "/api/v1/validation/surveys/{token}",
        "/api/v1/validation/surveys/{token}/responses",
        "/api/v1/validation/synthesize",
        "/api/v1/validation/scripts/generate",
```

- [ ] **Step 2: Write the live journey**

Read `e2e/conftest.py` and `e2e/test_learning.py` first, to confirm the `make_verified_user`,
`base_url` and `capture(...)` conventions are unchanged. Then create:

```python
# e2e/test_validation.py
"""Live Validation Hub journey (Module 09): a founder onboards, records an assumption, builds and
opens a survey, **a member of the public answers it with no authentication at all**, the founder
reads the analytics, links an experiment to the assumption, and marks the assumption validated.

Every response body along the way is captured to `e2e/_captures/validation/*.json` -- those files
are the verbatim source for `docs/fe-integration-guide-validation.md`. They must be REAL bodies
from this live run, complete and untrimmed.

The public submission is deliberately made with a SEPARATE client carrying no headers, so the
capture proves the endpoint needs no session and returns nothing about the workspace.
"""

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_learning.py)."""
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


QUESTIONS = [
    {"type": "choice", "prompt": "Would you pay for this?", "options": ["Yes", "No"],
     "required": True},
    {"type": "nps", "prompt": "How likely are you to recommend it?"},
    {"type": "open", "prompt": "What would make it a must-have?"},
]


def test_validation_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)
        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Record the assumption the whole module exists to test.
        created = c.post(
            "/api/v1/validation/assumptions",
            headers=wh,
            json={"statement": "Founders will pay for validation tooling", "risk": "high"},
        )
        assert created.status_code == 201, created.text
        assumption = created.json()["data"]
        assert assumption["status"] == "untested" and assumption["evidence_count"] == 0
        capture("validation", "assumption_created", created)

        # 2. Build a survey and open it, which mints the public token exactly once.
        survey_created = c.post(
            "/api/v1/validation/surveys",
            headers=wh,
            json={"title": "Pricing check", "questions": QUESTIONS},
        )
        assert survey_created.status_code == 201, survey_created.text
        survey_id = survey_created.json()["data"]["id"]
        capture("validation", "survey_created", survey_created)

        opened = c.patch(
            f"/api/v1/validation/surveys/{survey_id}", headers=wh, json={"status": "open"}
        )
        assert opened.status_code == 200, opened.text
        token = opened.json()["data"]["public_token"]
        assert token, "opening a survey must return the raw token once"
        capture("validation", "survey_opened", opened)

    # 3. A member of the public answers it — a brand-new client with NO headers at all.
    with httpx.Client(base_url=base_url, timeout=10.0) as public:
        form = public.get(f"/api/v1/validation/surveys/{token}")
        assert form.status_code == 200, form.text
        body = form.json()["data"]
        assert set(body) == {"title", "questions"}
        assert survey_id not in form.text, "the public form must not leak the survey id"
        capture("validation", "public_form", form)

        answers = {
            body["questions"][0]["id"]: "Yes",
            body["questions"][1]["id"]: 9,
            body["questions"][2]["id"]: "Stop me re-typing interview notes",
        }
        sent = public.post(
            f"/api/v1/validation/surveys/{token}/responses", json={"answers": answers}
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["data"] == {"received": True}
        capture("validation", "public_response", sent)

        missing = public.get("/api/v1/validation/surveys/not-a-real-token")
        assert missing.status_code == 404, missing.text
        capture("validation", "public_unknown_token", missing)

    # 4. Back as the founder: the evidence is in.
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        wh = {
            **_auth_header(access),
            "X-Workspace-Id": me["active_workspace_id"],
        }

        analytics = c.get(f"/api/v1/validation/surveys/{survey_id}/analytics", headers=wh)
        assert analytics.status_code == 200, analytics.text
        data = analytics.json()["data"]
        assert data["responses"] == 1 and data["completion_rate"] == 100
        assert data["questions"][0]["counts"] == {"Yes": 1, "No": 0}
        capture("validation", "survey_analytics", analytics)

        experiment = c.post(
            "/api/v1/validation/experiments",
            headers=wh,
            json={
                "name": "Fake door landing page",
                "type": "smoke_test",
                "metrics": {"visits": 120, "signups": 9},
                "assumption_ids": [assumption["id"]],
            },
        )
        assert experiment.status_code == 201, experiment.text
        capture("validation", "experiment_created", experiment)

        stats = c.get(
            f"/api/v1/validation/smoke-tests/{experiment.json()['data']['id']}/stats", headers=wh
        )
        assert stats.status_code == 200, stats.text
        assert stats.json()["data"]["conversion"] == 7.5
        capture("validation", "smoke_test_stats", stats)

        interview = c.post(
            "/api/v1/validation/interviews",
            headers=wh,
            json={
                "interviewee": "Ada",
                "held_on": "2026-09-20",
                "verdict": "supports",
                "segment": "fintech",
                "notes": "Asked for it unprompted.",
                "key_quotes": ["I would pay for this today"],
                "assumption_ids": [assumption["id"]],
            },
        )
        assert interview.status_code == 201, interview.text
        capture("validation", "interview_created", interview)

        # 5. Two pieces of evidence, so the assumption is validated.
        validated = c.patch(
            f"/api/v1/validation/assumptions/{assumption['id']}",
            headers=wh,
            json={"status": "validated"},
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["data"]["status"] == "validated"
        assert validated.json()["data"]["evidence_count"] == 2
        capture("validation", "assumption_validated", validated)

        listed = c.get("/api/v1/validation/assumptions?status=validated", headers=wh)
        assert [a["id"] for a in listed.json()["data"]["assumptions"]] == [assumption["id"]]
        capture("validation", "assumptions_list", listed)
```

- [ ] **Step 3: Run the full e2e suite**

```bash
scripts/e2e_run.sh
```

Expected: green, including the new journey. A missing `db.commit()` surfaces here as "the response
or the assumption didn't persist" — check every write endpoint commits.

- [ ] **Step 4: Write the FE integration guide**

Create `docs/fe-integration-guide-validation.md`, **every body pasted verbatim from
`e2e/_captures/validation/`**. Cover:

- the member routes with real request/response bodies, status codes and auth headers
- **access:** founders and team members only — show a real `403` body
- **the public link:** how it is obtained (open the survey; `public_token` is returned **once**),
  what the public `GET` returns (title and questions only), and what the `POST` returns
  (`{"received": true}` and nothing else)
- the uniform `404` for unknown, draft and closed surveys
- the answer rules per question type and the caps (50 questions, 20 options, 4,000 characters)
- the `20/minute` rate limit on submissions, and the `429` body it produces
- the analytics shape: counts per option, averages for scale and NPS, answered counts for open
  questions, and the completion rate
- the job stubs: `202` with `job_id`, nothing rendered yet

End with a verification table citing capture filenames.

- [ ] **Step 5: Write the SOP**

Create `docs/sop/<build-date>-validation-hub.md` in the project's SOP style (mirror
`docs/sop/2026-09-14-learning-academy.md`): **what shipped** (with commit refs), **why**, **how**
(the public token pattern copied from Module 18; the `Limiter` move and why it was needed; derived
evidence counts; free status transitions with events only on real changes; JSONB question schema
and its caps), **what's involved** (files, the five tables, the migration, the 16 member routes and
2 public routes), **verification** (unit, API, e2e and captures), **operate / roll back** (one
migration; `downgrade` drops all five tables and is lossy; no new environment variables), and
**follow-ups** (the spec's list, §9).

- [ ] **Step 6: Update the master checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, move **Module 09 — Validation Hub** out of *Upcoming*
into its own section, in the style of the Module 17 section: what shipped, the access rule, the
public surface, tests, and the deferred list. Re-read the file first — it moves constantly.

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
git add e2e/test_smoke.py e2e/test_validation.py e2e/_captures/validation/ docs/fe-integration-guide-validation.md docs/sop/ docs/checklist/PROJECT_CHECKLIST.md
git commit -m "test(validation): live e2e + captures + FE guide + SOP + checklist"
```

- [ ] **Step 8: Before pushing**

```bash
git fetch origin && git rebase origin/develop
poetry run alembic heads          # exactly one head; renumber the migration if develop moved
poetry run alembic upgrade head
poetry run alembic check
poetry run pytest
```

Then push and open the PR into `develop`. Mention in the PR description that the `Limiter` moved
from `app/main.py` to `app/core/rate_limit.py`, since it touches a shared file (spec D5).

---

## Self-Review

**1. Spec coverage:**
- §2 access (founders and team members; workspace scoping; the public exception) → Task 6's
  `_member` on every member route, the two dependency-free public routes, and the access matrix
  tests. ✓
- §3 data model (five tables, enums, JSONB defaults, migration) → Task 1. ✓
- §4 the public surface (token hashing, uniform 404, no-leak replies, answer validation, caps,
  rate limit, repeat submissions) → Task 2 (validation), Task 5 (token + submission), Task 6 (the
  routes and the limit), and the e2e's headerless client. ✓
- §5 member API (16 routes, derived evidence counts, free transitions with events only on a real
  change, smoke stats, analytics, job stubs, `db.commit()`) → Tasks 3, 4 and 6. ✓
- §6 cross-cutting (service/endpoint split, errors, events with `db` first, jobs, the `Limiter`
  move, no new config) → Tasks 3–6. ✓
- §7 testing (unit, API, smoke, live e2e, FE guide, and the `db`-fixture rule) → every task's test
  step plus Task 7. ✓
- §8 file structure → matches Tasks 1–7. ✓
- §9 decisions D1–D10 and waivers W1–W3 → reflected in the code and recorded in the SOP and
  checklist (Task 7). ✓

**2. Placeholder scan:** the only unresolved values are the migration number, settled by Task 1's
explicit numbering rule against the live head, and the SOP's `<build-date>`, taken on the day it is
written. Every code and test step carries real content — no `TODO`, no "handle edge cases", no bare
"write tests".

**3. Type consistency:** `link_ids(db, startup_id, ids) -> list[str]`,
`evidence_counts(db, startup_id, assumptions) -> dict[str, int]`,
`update_assumption(..., actor_id, ...) -> Assumption`,
`update_survey(...) -> tuple[Survey, str | None]`,
`response_counts(db, startup_id, surveys) -> dict[str, int]`,
`open_survey(db, token) -> Survey`, `public_view(survey) -> dict`,
`submit_response(db, token, answers) -> SurveyResponse`,
`validate_questions(questions) -> list[dict]`, `validate_answers(questions, answers) -> dict`, and
the `serialize_*` views are used identically across the service, the endpoints, the tests and the
e2e. Question ids are strings everywhere; assumption links are lists of string ids everywhere.












































































