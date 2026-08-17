# Startup Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Module 07 — the adaptive, resumable Startup Assessment: a server-driven single-question runner across 5 dimensions with a static versioned question bank + declarative skip logic, per-answer autosave, deterministic per-dimension scoring at completion, and the onboarding linkage (completing the initial assessment flips `assessment_pending`).

**Architecture:** Thin FastAPI router `app/api/v1/endpoints/assessments.py` delegating to `app/services/assessment/` (a static `bank.py`, a pure `engine.py`, a pure `scoring.py`, and a `service.py` that orchestrates DB + events). Assessments are workspace-scoped via `X-Workspace-Id` + `memberships`; mutations require Founder, reads any active member. Adaptivity and scoring are pure functions over the bank + answers + startup, so they're unit-testable without HTTP or DB.

**Tech Stack:** FastAPI 0.115, SQLAlchemy 2.0 (typed `Mapped`, JSONB), Alembic, PostgreSQL, pytest + real Postgres.

**Spec:** `docs/superpowers/specs/2026-08-16-assessment-design.md` — read it alongside this plan.

## Global Constraints

- **Response envelope** (`app/core/envelope.py`): endpoints return `success_response(data)`; errors via raised `AppError` subclasses. Never hand-roll the dict.
- **Access:** all endpoints workspace-scoped. **Mutations** (`POST /assessments`, `/answers`, `/complete`) require Founder — `Depends(require_role(MembershipRole.founder))`. **Reads** (`GET` list/detail/compare/next-question is Founder too since it's part of the take-flow) — list/detail/compare use `Depends(require_workspace)` (any active member). The dependency returns a `Membership`; the workspace is `membership.startup_id`.
- **Scoring is per-dimension deterministic**; only *applicable* (non-skipped) *scored* questions (`max > 0`) count. `overall_provisional = round(mean(dimensions))`. Narrative is a templated string.
- **One in-progress per workspace** (partial-unique index). First-ever assessment = `initial`; later = `quarterly`. Completing an `initial` sets `startup_profiles.assessment_pending = False`.
- **Forward-flow answers:** an answer must be for the current `next_question`; editing prior answers is out of scope.
- **Enums:** `Enum(PyEnum, native_enum=False, length=…)`. UUID v4 PKs (`UUIDMixin`), `TimestampMixin`. Timezone-aware UTC (`datetime.now(UTC)`).
- **Idempotency:** `complete` self-guards via `status == completed` (returns stored results, no re-enqueue).
- **Events/jobs:** `event_bus.publish("assessment.completed", …)`; `job_dispatcher.enqueue("healthscore.recalculate", …)` + `("roadmap.replan", …)`.
- **Do NOT add `Co-Authored-By`/AI-attribution trailers to commits. Do NOT write per-task `docs/sop/` docs** — one consolidated SOP in the final task.

### Existing interfaces (carry into every task brief)

```python
# app/db/tenancy.py → require_workspace(...) -> Membership (reads X-Workspace-Id + active membership);
#   require_role(*roles) -> dependency -> Membership (403 if role not in roles). Membership has .startup_id, .user_id, .role
# app/api/deps.py → get_current_user; app/core/security.py → create_access_token(str(user_id))
# app/core/envelope.py → success_response(data, meta=None)
# app/core/errors.py → AppError(code,message,http_status,field_errors); NotFound(404), Forbidden(403)
# app/db/models/startup.py → Startup(stage: StartupStage|None, business_model: BusinessModel|None, profile: StartupProfile);
#   StartupProfile(assessment_pending: bool, onboarding_completed_at)
# app/db/models/enums.py → StartupStage(idea|validation|build|launch|growth|scale), BusinessModel(b2b|b2c|b2b2c|marketplace|hardware|services), MembershipRole(founder|...)
# app/platform/jobs.py → job_dispatcher.enqueue(db, type: str, payload: dict, startup_id: uuid.UUID|None=None) -> Job
# app/platform/events.py → event_bus.publish(event: str, payload: dict); event_bus.published (list, tests)
# app/api/v1/api.py → api_router.include_router(...) (health, jobs, auth, onboarding, invitations mounted)
# tests/factories.py → create_user(db,*,email=None,**kw), create_startup(db,*,owner,name="Acme",**kw), create_membership(db,user,startup,role=founder)
# tests/conftest.py → db (rolled-back Session), client (TestClient sharing db, limiter disabled)
```

**Endpoint test auth pattern:** seed `user` + `startup` + founder `membership` via factories, `db.flush()`; call with `headers={"Authorization": f"Bearer {create_access_token(str(user.id))}", "X-Workspace-Id": str(startup.id)}`. The user needs `email_verified_at` only if a route uses `get_verified_user` — assessment routes use `require_workspace`/`require_role` (which use `get_current_user`), so verification is not required, but an active founder membership IS.

Latest migration: `0003_onboarding`. New migration `down_revision = "0003_onboarding"`.

---

## File Structure

**Create:**
- `app/db/models/assessment.py` — `Assessment`, `AssessmentAnswer`, `AssessmentResult`.
- `app/services/assessment/__init__.py`, `bank.py`, `engine.py`, `scoring.py`, `service.py`.
- `app/schemas/assessment.py` — request models.
- `app/api/v1/endpoints/assessments.py` — the router.
- `tests/services/assessment/*`, `tests/api/assessment/*`, `e2e/test_assessment.py`.

**Modify:**
- `app/db/models/enums.py` — add `AssessmentType`, `AssessmentStatus`, `Dimension`.
- `app/db/models/__init__.py` — register the 3 models.
- `tests/factories.py` — add `create_assessment`, `create_answer`.
- `app/api/v1/api.py` — mount the assessments router.
- `alembic/versions/` — `0004_assessment`.

---

## Task 1: Enums + models + factories

**Files:**
- Modify: `app/db/models/enums.py`, `app/db/models/__init__.py`, `tests/factories.py`
- Create: `app/db/models/assessment.py`
- Test: `tests/db/test_assessment_models.py`

**Interfaces:**
- Produces: `AssessmentType(initial|quarterly)`, `AssessmentStatus(in_progress|completed|abandoned)`, `Dimension(product|market|money|legal|team)`; `Assessment(startup_id, type, status, bank_version, created_by, started_at, completed_at)` with a partial-unique index on `startup_id WHERE status='in_progress'`; `AssessmentAnswer(assessment_id, question_key, value_json, answered_at)` unique `(assessment_id, question_key)`; `AssessmentResult(assessment_id PK, dimension_scores, overall_provisional, narrative)`; factories `create_assessment`, `create_answer`.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_assessment_models.py
import pytest
from sqlalchemy.exc import IntegrityError
from app.db.models.assessment import Assessment, AssessmentAnswer, AssessmentResult
from app.db.models.enums import AssessmentType, AssessmentStatus
from tests.factories import create_user, create_startup, create_assessment


def test_assessment_defaults(db):
    u = create_user(db); s = create_startup(db, owner=u)
    a = create_assessment(db, s, creator=u)
    assert a.status == AssessmentStatus.in_progress and a.type == AssessmentType.initial
    assert a.bank_version

def test_one_in_progress_per_startup(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_assessment(db, s, creator=u); db.flush()
    create_assessment(db, s, creator=u)
    with pytest.raises(IntegrityError):
        db.flush()

def test_answer_upsert_unique(db):
    u = create_user(db); s = create_startup(db, owner=u)
    a = create_assessment(db, s, creator=u)
    db.add(AssessmentAnswer(assessment_id=a.id, question_key="q1", value_json="yes")); db.flush()
    db.add(AssessmentAnswer(assessment_id=a.id, question_key="q1", value_json="no"))
    with pytest.raises(IntegrityError):
        db.flush()

def test_result_roundtrip(db):
    u = create_user(db); s = create_startup(db, owner=u)
    a = create_assessment(db, s, creator=u)
    db.add(AssessmentResult(assessment_id=a.id,
                            dimension_scores={"product": 60, "market": 40, "money": 50, "legal": 70, "team": 55},
                            overall_provisional=55, narrative="Strongest: legal."))
    db.flush()
    r = db.query(AssessmentResult).filter_by(assessment_id=a.id).one()
    assert r.dimension_scores["legal"] == 70 and r.overall_provisional == 55
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/db/test_assessment_models.py -v`
Expected: FAIL — models missing.

- [ ] **Step 3: Add enums**

```python
# app/db/models/enums.py  (append)
class AssessmentType(str, enum.Enum):
    initial = "initial"
    quarterly = "quarterly"


class AssessmentStatus(str, enum.Enum):
    in_progress = "in_progress"
    completed = "completed"
    abandoned = "abandoned"


class Dimension(str, enum.Enum):
    product = "product"
    market = "market"
    money = "money"
    legal = "legal"
    team = "team"
```

- [ ] **Step 4: Implement the models + register them**

```python
# app/db/models/assessment.py
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import AssessmentStatus, AssessmentType


class Assessment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "assessments"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("startups.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[AssessmentType] = mapped_column(
        Enum(AssessmentType, native_enum=False, length=20), nullable=False)
    status: Mapped[AssessmentStatus] = mapped_column(
        Enum(AssessmentStatus, native_enum=False, length=20),
        default=AssessmentStatus.in_progress, nullable=False)
    bank_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_assessments_startup_in_progress", "startup_id", unique=True,
              postgresql_where=text("status = 'in_progress'")),
    )


class AssessmentAnswer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "assessment_answers"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    question_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("assessment_id", "question_key", name="uq_answer_assessment_question"),)


class AssessmentResult(TimestampMixin, Base):
    __tablename__ = "assessment_results"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assessments.id", ondelete="CASCADE"), primary_key=True)
    dimension_scores: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    overall_provisional: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
```

```python
# app/db/models/__init__.py  (append)
from app.db.models.assessment import Assessment, AssessmentAnswer, AssessmentResult  # noqa: F401
```

- [ ] **Step 5: Add factories**

```python
# tests/factories.py  (append; add imports)
from app.db.models.assessment import Assessment, AssessmentAnswer
from app.db.models.enums import AssessmentStatus, AssessmentType


def create_assessment(db, startup, *, creator=None, type=AssessmentType.initial,
                      status=AssessmentStatus.in_progress, bank_version="v1"):
    a = Assessment(startup_id=startup.id, type=type, status=status, bank_version=bank_version,
                   created_by=(creator.id if creator else startup.created_by))
    db.add(a); db.flush()
    return a


def create_answer(db, assessment, *, question_key, value):
    ans = AssessmentAnswer(assessment_id=assessment.id, question_key=question_key, value_json=value)
    db.add(ans); db.flush()
    return ans
```

- [ ] **Step 6: Run test to verify it passes**

Run: `poetry run pytest tests/db/test_assessment_models.py -v`
Expected: PASS (4 tests). Full suite green.

- [ ] **Step 7: Commit**

```bash
git add app/db/models/ tests/factories.py tests/db/test_assessment_models.py
git commit -m "feat(db): assessment models (assessments, answers, results) + enums"
```

---

## Task 2: Alembic migration 0004

**Files:**
- Create: `alembic/versions/0004_assessment.py`
- Test: `tests/test_assessment_migration.py`

**Interfaces:**
- Produces: migration adding the 3 tables incl. the **partial-unique** `in_progress` index and the `(assessment_id, question_key)` unique; clean `alembic upgrade head`; no drift.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_assessment_migration.py
import subprocess

def test_assessment_migration_applies():
    r = subprocess.run(["poetry", "run", "alembic", "upgrade", "head"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    chk = subprocess.run(["poetry", "run", "python", "-c",
        "from sqlalchemy import create_engine, inspect; from app.core.config import settings; "
        "e=create_engine(settings.DATABASE_URL); i=inspect(e); n=set(i.get_table_names()); "
        "assert {'assessments','assessment_answers','assessment_results'} <= n; "
        "idx=[x['name'] for x in i.get_indexes('assessments')]; "
        "assert 'uq_assessments_startup_in_progress' in idx; print('ok')"],
        capture_output=True, text=True)
    assert chk.returncode == 0, chk.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_assessment_migration.py -v`
Expected: FAIL — migration missing.

- [ ] **Step 3: Autogenerate + verify**

```bash
poetry run alembic revision --autogenerate -m "assessment" --rev-id 0004_assessment
```
Confirm `down_revision = "0003_onboarding"` and `upgrade()` creates `assessments` (+ its FK indexes + the partial-unique index `uq_assessments_startup_in_progress` with `postgresql_where=sa.text("status = 'in_progress'")`), `assessment_answers` (+ `uq_answer_assessment_question`), `assessment_results`. **If autogenerate omits the partial `postgresql_where`, hand-add it** to the `op.create_index(...)` call. Remove any spurious ops; `downgrade()` drops the 3 tables.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_assessment_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Verify no drift + delete temp**

Run: `poetry run alembic revision --autogenerate -m "verify" --rev-id _tmp` → confirm empty `upgrade()` → **delete the temp file**.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0004_assessment.py tests/test_assessment_migration.py
git commit -m "feat(db): migration 0004 — assessment tables"
```

---

## Task 3: Question bank v1

**Files:**
- Create: `app/services/assessment/__init__.py`, `app/services/assessment/bank.py`
- Test: `tests/services/assessment/test_bank.py`

**Interfaces:**
- Produces: `Question` dataclass (`key, dimension: Dimension, section: str, qtype: str, options: list[dict]|None, scoring: dict, show_if: dict|None`); `QType` constants (`SINGLE_CHOICE, MULTI_CHOICE, SCALE_1_5, NUMERIC_CURRENCY, SHORT_TEXT`); `Bank(version: str, questions: list[Question])`; `ASSESSMENT_BANK: Bank`; helper `question_by_key(bank, key) -> Question | None`.
- Scoring convention: `scoring` is `{"max": <int>, <answer_value>: <points>, ...}`. `SCALE_1_5` uses `{"max": 5}` (points = the 1–5 value). `NUMERIC_CURRENCY`/`SHORT_TEXT` are informational: `{"max": 0}` (captured, not scored).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/assessment/test_bank.py
from app.services.assessment.bank import ASSESSMENT_BANK, question_by_key, QType
from app.db.models.enums import Dimension


def test_bank_versioned_and_nonempty():
    assert ASSESSMENT_BANK.version == "v1"
    assert len(ASSESSMENT_BANK.questions) >= 8

def test_every_dimension_has_a_scored_question():
    for dim in Dimension:
        scored = [q for q in ASSESSMENT_BANK.questions if q.dimension == dim and q.scoring.get("max", 0) > 0]
        assert scored, f"dimension {dim} has no scored question"

def test_question_keys_unique_and_lookup():
    keys = [q.key for q in ASSESSMENT_BANK.questions]
    assert len(keys) == len(set(keys))
    assert question_by_key(ASSESSMENT_BANK, keys[0]).key == keys[0]
    assert question_by_key(ASSESSMENT_BANK, "nope") is None

def test_choice_questions_have_options_and_scoring_within_max():
    for q in ASSESSMENT_BANK.questions:
        if q.qtype in (QType.SINGLE_CHOICE, QType.MULTI_CHOICE):
            assert q.options, q.key
            opt_values = {o["value"] for o in q.options}
            for k, v in q.scoring.items():
                if k != "max":
                    assert k in opt_values, f"{q.key}: scoring key {k} not an option"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/assessment/test_bank.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the bank**

```python
# app/services/assessment/bank.py
from dataclasses import dataclass, field
from app.db.models.enums import Dimension


class QType:
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    SCALE_1_5 = "scale_1_5"
    NUMERIC_CURRENCY = "numeric_currency"
    SHORT_TEXT = "short_text"


@dataclass(frozen=True)
class Question:
    key: str
    dimension: Dimension
    section: str
    qtype: str
    scoring: dict
    options: list[dict] | None = None
    show_if: dict | None = None


@dataclass(frozen=True)
class Bank:
    version: str
    questions: list[Question]


def question_by_key(bank: Bank, key: str) -> Question | None:
    return next((q for q in bank.questions if q.key == key), None)


def _opt(value: str, label: str) -> dict:
    return {"value": value, "label": label}


ASSESSMENT_BANK = Bank(
    version="v1",
    questions=[
        # --- Product ---
        Question("product_stage", Dimension.product, "Product",
                 QType.SINGLE_CHOICE, {"max": 4, "idea": 1, "prototype": 2, "mvp": 3, "live": 4},
                 options=[_opt("idea", "Just an idea"), _opt("prototype", "A prototype"),
                          _opt("mvp", "A working MVP"), _opt("live", "Live with users")]),
        Question("product_confidence", Dimension.product, "Product",
                 QType.SCALE_1_5, {"max": 5},
                 show_if={"answer": "product_stage", "in": ["mvp", "live"]}),
        # --- Market ---
        Question("market_clarity", Dimension.market, "Market",
                 QType.SCALE_1_5, {"max": 5}),
        Question("market_research", Dimension.market, "Market",
                 QType.SINGLE_CHOICE, {"max": 3, "none": 0, "some": 2, "deep": 3},
                 options=[_opt("none", "No research yet"), _opt("some", "Some interviews"),
                          _opt("deep", "Deep, ongoing research")]),
        # --- Money ---
        Question("has_revenue", Dimension.money, "Money",
                 QType.SINGLE_CHOICE, {"max": 2, "no": 0, "yes": 2},
                 options=[_opt("no", "Not yet"), _opt("yes", "Yes, we have revenue")]),
        Question("mrr", Dimension.money, "Money",
                 QType.NUMERIC_CURRENCY, {"max": 0},
                 show_if={"answer": "has_revenue", "eq": "yes"}),
        Question("runway_confidence", Dimension.money, "Money",
                 QType.SCALE_1_5, {"max": 5}),
        # --- Legal ---
        Question("incorporated", Dimension.legal, "Legal",
                 QType.SINGLE_CHOICE, {"max": 2, "no": 0, "yes": 2},
                 options=[_opt("no", "Not incorporated"), _opt("yes", "Incorporated")]),
        Question("ip_assigned", Dimension.legal, "Legal",
                 QType.SINGLE_CHOICE, {"max": 2, "no": 0, "unsure": 1, "yes": 2},
                 options=[_opt("no", "No"), _opt("unsure", "Not sure"), _opt("yes", "Yes")],
                 show_if={"answer": "incorporated", "eq": "yes"}),
        # --- Team ---
        Question("team_size", Dimension.team, "Team",
                 QType.SINGLE_CHOICE, {"max": 3, "solo": 1, "cofounders": 3, "team": 3},
                 options=[_opt("solo", "Just me"), _opt("cofounders", "Co-founders"),
                          _opt("team", "A team")]),
        Question("team_confidence", Dimension.team, "Team",
                 QType.SCALE_1_5, {"max": 5}),
    ],
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/assessment/test_bank.py -v`
Expected: PASS (4 tests). Create empty `tests/services/assessment/__init__.py`.

- [ ] **Step 5: Commit**

```bash
git add app/services/assessment/__init__.py app/services/assessment/bank.py tests/services/assessment/
git commit -m "feat(assessment): versioned question bank v1"
```

---

## Task 4: Adaptive engine

**Files:**
- Create: `app/services/assessment/engine.py`
- Test: `tests/services/assessment/test_engine.py`

**Interfaces:**
- Consumes: `Bank`, `Question`, `QType`, `AppError`, `Startup`.
- Produces:
  - `is_applicable(question, answers: dict, startup) -> bool` — evaluates `show_if` (`None`→True; `{"field": f, "in": [...]}` against `startup.<f>` enum `.value`; `{"answer": k, "eq": v}`/`{"answer": k, "in": [...]}` against `answers.get(k)`; `{"all": [...]}`/`{"any": [...]}` recursive).
  - `next_question(bank, answers: dict, startup) -> Question | None` — first applicable + unanswered question in bank order.
  - `validate_answer(question, value) -> None` — raises `AppError("INVALID_ANSWER", …, 422)` on type/option/range mismatch.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/assessment/test_engine.py
import pytest
from app.core.errors import AppError
from app.services.assessment.bank import ASSESSMENT_BANK, question_by_key, QType
from app.services.assessment.engine import is_applicable, next_question, validate_answer
from app.db.models.enums import StartupStage
from tests.factories import create_user, create_startup


def _startup(db, **kw):
    u = create_user(db); return create_startup(db, owner=u, **kw)


def test_show_if_prior_answer(db):
    s = _startup(db)
    mrr = question_by_key(ASSESSMENT_BANK, "mrr")
    assert is_applicable(mrr, {"has_revenue": "yes"}, s) is True
    assert is_applicable(mrr, {"has_revenue": "no"}, s) is False
    assert is_applicable(mrr, {}, s) is False  # unanswered gate → not applicable yet

def test_next_question_skips_inapplicable(db):
    s = _startup(db)
    # First question is always product_stage (no show_if)
    q = next_question(ASSESSMENT_BANK, {}, s)
    assert q.key == "product_stage"
    # Answering has_revenue=no means mrr is skipped; next after it moves on
    answers = {"product_stage": "idea", "market_clarity": 3, "market_research": "some",
               "has_revenue": "no"}
    q2 = next_question(ASSESSMENT_BANK, answers, s)
    assert q2.key != "mrr"  # mrr skipped

def test_next_question_none_when_done(db):
    s = _startup(db)
    # Answer every applicable question (has_revenue=no, incorporated=no prune the conditionals)
    answers = {"product_stage": "idea", "market_clarity": 3, "market_research": "some",
               "has_revenue": "no", "runway_confidence": 3, "incorporated": "no",
               "team_size": "solo", "team_confidence": 4}
    assert next_question(ASSESSMENT_BANK, answers, s) is None

def test_validate_answer_types():
    validate_answer(question_by_key(ASSESSMENT_BANK, "product_stage"), "idea")   # ok
    with pytest.raises(AppError):
        validate_answer(question_by_key(ASSESSMENT_BANK, "product_stage"), "bogus")
    validate_answer(question_by_key(ASSESSMENT_BANK, "market_clarity"), 5)       # ok
    with pytest.raises(AppError):
        validate_answer(question_by_key(ASSESSMENT_BANK, "market_clarity"), 9)   # scale out of range
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/assessment/test_engine.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the engine**

```python
# app/services/assessment/engine.py
from typing import Any
from app.core.errors import AppError
from app.services.assessment.bank import Bank, Question, QType


def _cond(node: dict, answers: dict, startup) -> bool:
    if "all" in node:
        return all(_cond(c, answers, startup) for c in node["all"])
    if "any" in node:
        return any(_cond(c, answers, startup) for c in node["any"])
    if "field" in node:
        attr = getattr(startup, node["field"], None)
        val = attr.value if attr is not None and hasattr(attr, "value") else attr
        if "in" in node:
            return val in node["in"]
        return val == node.get("eq")
    if "answer" in node:
        val = answers.get(node["answer"])
        if val is None:
            return False
        if "in" in node:
            return val in node["in"]
        return val == node.get("eq")
    return True


def is_applicable(question: Question, answers: dict, startup) -> bool:
    if question.show_if is None:
        return True
    return _cond(question.show_if, answers, startup)


def next_question(bank: Bank, answers: dict, startup) -> Question | None:
    for q in bank.questions:
        if q.key in answers:
            continue
        if is_applicable(q, answers, startup):
            return q
    return None


def validate_answer(question: Question, value: Any) -> None:
    err = AppError("INVALID_ANSWER", "That answer isn't valid for this question.", 422)
    if question.qtype == QType.SINGLE_CHOICE:
        if value not in {o["value"] for o in (question.options or [])}:
            raise err
    elif question.qtype == QType.MULTI_CHOICE:
        opts = {o["value"] for o in (question.options or [])}
        if not isinstance(value, list) or not value or any(v not in opts for v in value):
            raise err
    elif question.qtype == QType.SCALE_1_5:
        if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 5):
            raise err
    elif question.qtype == QType.NUMERIC_CURRENCY:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise err
    elif question.qtype == QType.SHORT_TEXT:
        if not isinstance(value, str) or not value.strip():
            raise err
    else:
        raise err
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/assessment/test_engine.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/assessment/engine.py tests/services/assessment/test_engine.py
git commit -m "feat(assessment): adaptive engine (applicability, next-question, validation)"
```

---

## Task 5: Scoring

**Files:**
- Create: `app/services/assessment/scoring.py`
- Test: `tests/services/assessment/test_scoring.py`

**Interfaces:**
- Consumes: `Bank`, `QType`, `is_applicable`, `Dimension`.
- Produces: `score(bank, answers: dict, startup) -> dict` returning `{"dimension_scores": {dim.value: 0..100}, "overall_provisional": int, "narrative": str}`. Per dimension: sum earned points ÷ sum `max` over that dimension's **applicable, scored (`max>0`), answered** questions → 0–100 int; 50 if none. `overall_provisional = round(mean)`. `narrative` names the top and bottom dimensions.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/assessment/test_scoring.py
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.scoring import score
from tests.factories import create_user, create_startup


def _startup(db, **kw):
    u = create_user(db); return create_startup(db, owner=u, **kw)


def test_score_dimensions_0_100(db):
    s = _startup(db)
    answers = {"product_stage": "live", "product_confidence": 5, "market_clarity": 5,
               "market_research": "deep", "has_revenue": "yes", "mrr": 5000,
               "runway_confidence": 5, "incorporated": "yes", "ip_assigned": "yes",
               "team_size": "team", "team_confidence": 5}
    out = score(ASSESSMENT_BANK, answers, s)
    assert set(out["dimension_scores"]) == {"product", "market", "money", "legal", "team"}
    assert all(0 <= v <= 100 for v in out["dimension_scores"].values())
    assert out["dimension_scores"]["legal"] == 100  # incorporated=yes(2/2)+ip=yes(2/2)
    assert 0 <= out["overall_provisional"] <= 100
    assert isinstance(out["narrative"], str) and out["narrative"]

def test_skipped_questions_excluded_from_denominator(db):
    s = _startup(db)
    # has_revenue=no → mrr skipped (informational anyway); money dim = runway only
    low = {"product_stage": "idea", "market_clarity": 1, "market_research": "none",
           "has_revenue": "no", "runway_confidence": 1, "incorporated": "no",
           "team_size": "solo", "team_confidence": 1}
    out = score(ASSESSMENT_BANK, low, s)
    # money = runway_confidence 1/5 = 20
    assert out["dimension_scores"]["money"] == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/assessment/test_scoring.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement scoring**

```python
# app/services/assessment/scoring.py
from app.db.models.enums import Dimension
from app.services.assessment.bank import Bank, Question, QType
from app.services.assessment.engine import is_applicable


def _points(q: Question, value) -> int:
    if q.qtype == QType.SCALE_1_5:
        return int(value)
    if q.qtype == QType.SINGLE_CHOICE:
        return int(q.scoring.get(value, 0))
    if q.qtype == QType.MULTI_CHOICE:
        earned = sum(int(q.scoring.get(v, 0)) for v in value)
        return min(earned, int(q.scoring["max"]))
    return 0


def score(bank: Bank, answers: dict, startup) -> dict:
    dim_scores: dict[str, int] = {}
    for dim in Dimension:
        earned = maxsum = 0
        for q in bank.questions:
            if q.dimension != dim:
                continue
            if q.scoring.get("max", 0) <= 0:
                continue  # informational
            if q.key not in answers or not is_applicable(q, answers, startup):
                continue
            earned += _points(q, answers[q.key])
            maxsum += int(q.scoring["max"])
        dim_scores[dim.value] = round(100 * earned / maxsum) if maxsum else 50

    overall = round(sum(dim_scores.values()) / len(dim_scores))
    top = max(dim_scores, key=dim_scores.get)
    low = min(dim_scores, key=dim_scores.get)
    narrative = (f"Your strongest area is {top} ({dim_scores[top]}). "
                 f"Focus next on {low} ({dim_scores[low]}).")
    return {"dimension_scores": dim_scores, "overall_provisional": overall, "narrative": narrative}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/assessment/test_scoring.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/assessment/scoring.py tests/services/assessment/test_scoring.py
git commit -m "feat(assessment): deterministic per-dimension scoring"
```

---

## Task 6: Service layer + `POST /assessments` (start/resume)

**Files:**
- Create: `app/services/assessment/service.py`, `app/schemas/assessment.py`, `app/api/v1/endpoints/assessments.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/assessment/test_start.py`

**Interfaces:**
- Consumes: `require_role`, `require_workspace`, `Assessment`, `AssessmentStatus`, `AssessmentType`, `next_question`, `ASSESSMENT_BANK`, `Startup`.
- Produces:
  - `answered_map(db, assessment) -> dict[str, Any]` — `{question_key: value_json}`.
  - `serialize_question(q) -> dict | None` — `{key, dimension, section, qtype, options}` or None.
  - `start_or_resume(db, startup, user) -> Assessment` — returns existing `in_progress`, else creates (`initial` if no completed assessment for the startup exists, else `quarterly`).
  - `POST /api/v1/assessments` (Founder) → `{assessment_id, type, status, next_question}`; router mounted at `/api/v1/assessments`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/assessment/test_start.py
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, AssessmentStatus, AssessmentType
from app.db.models.assessment import Assessment
from tests.factories import create_user, create_startup, create_membership


def _founder(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder); db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_start_creates_initial_with_next_question(client, db):
    u, s, h = _founder(db); db.commit()
    r = client.post("/api/v1/assessments", headers=h)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["type"] == "initial" and data["status"] == "in_progress"
    assert data["next_question"]["key"] == "product_stage"

def test_start_resumes_existing(client, db):
    u, s, h = _founder(db); db.commit()
    first = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    again = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    assert first == again  # resumed, not a 2nd assessment
    assert db.query(Assessment).filter(Assessment.startup_id == s.id).count() == 1

def test_start_requires_founder(client, db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.team_member); db.commit()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    assert client.post("/api/v1/assessments", headers=h).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/assessment/test_start.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement service + schema**

```python
# app/services/assessment/service.py
import uuid
from typing import Any
from sqlalchemy.orm import Session
from app.db.models.assessment import Assessment, AssessmentAnswer
from app.db.models.enums import AssessmentStatus, AssessmentType
from app.db.models.startup import Startup
from app.db.models.user import User
from app.services.assessment.bank import ASSESSMENT_BANK, Question
from app.services.assessment.engine import next_question


def answered_map(db: Session, assessment: Assessment) -> dict[str, Any]:
    rows = db.query(AssessmentAnswer).filter(AssessmentAnswer.assessment_id == assessment.id).all()
    return {r.question_key: r.value_json for r in rows}


def serialize_question(q: Question | None) -> dict | None:
    if q is None:
        return None
    return {"key": q.key, "dimension": q.dimension.value, "section": q.section,
            "qtype": q.qtype, "options": q.options}


def start_or_resume(db: Session, startup: Startup, user: User) -> Assessment:
    existing = (db.query(Assessment)
                .filter(Assessment.startup_id == startup.id,
                        Assessment.status == AssessmentStatus.in_progress).first())
    if existing is not None:
        return existing
    has_completed = (db.query(Assessment)
                     .filter(Assessment.startup_id == startup.id,
                             Assessment.status == AssessmentStatus.completed).first() is not None)
    a = Assessment(startup_id=startup.id, created_by=user.id,
                   type=AssessmentType.quarterly if has_completed else AssessmentType.initial,
                   status=AssessmentStatus.in_progress, bank_version=ASSESSMENT_BANK.version)
    db.add(a); db.flush()
    return a
```

```python
# app/schemas/assessment.py
from pydantic import BaseModel
from typing import Any


class AnswerRequest(BaseModel):
    question_key: str
    value: Any
```

- [ ] **Step 4: Implement the endpoint + mount + a startup helper**

The workspace `Startup` is resolved from `membership.startup_id`. Add a tiny dependency to load it.

```python
# app/api/v1/endpoints/assessments.py
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.api.deps import get_current_user
from app.services.assessment.bank import ASSESSMENT_BANK
from app.services.assessment.engine import next_question
from app.services.assessment.service import answered_map, serialize_question, start_or_resume

router = APIRouter()


def _startup(db: Session, membership: Membership) -> Startup:
    s = db.query(Startup).filter(Startup.id == membership.startup_id).first()
    if s is None:
        raise NotFound()
    return s


@router.post("", status_code=201)
def start_assessment(
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    startup = _startup(db, membership)
    a = start_or_resume(db, startup, user)
    nq = next_question(ASSESSMENT_BANK, answered_map(db, a), startup)
    db.commit()
    return success_response({"assessment_id": str(a.id), "type": a.type.value,
                             "status": a.status.value, "next_question": serialize_question(nq)})
```

```python
# app/api/v1/api.py  (add)
from app.api.v1.endpoints import assessments
api_router.include_router(assessments.router, prefix="/assessments", tags=["assessments"])
```

Create empty `tests/api/assessment/__init__.py`.

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/api/assessment/test_start.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add app/services/assessment/service.py app/schemas/assessment.py app/api/v1/endpoints/assessments.py app/api/v1/api.py tests/api/assessment/
git commit -m "feat(assessment): POST /assessments start/resume"
```

---

## Task 7: `GET /assessments/{id}/next-question`

**Files:**
- Modify: `app/api/v1/endpoints/assessments.py`
- Test: `tests/api/assessment/test_next_question.py`

**Interfaces:**
- Consumes: `require_role`, `_startup`, `next_question`, `answered_map`, `serialize_question`, `Assessment`.
- Produces: `GET /assessments/{id}/next-question` (Founder) → `{next_question | null}`; 404 if the assessment isn't this workspace's.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/assessment/test_next_question.py
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_user, create_startup, create_membership


def _founder(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder); db.flush()
    return u, s, {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}


def test_next_question_for_resume(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    r = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h)
    assert r.status_code == 200
    assert r.json()["data"]["next_question"]["key"] == "product_stage"

def test_next_question_foreign_assessment_404(client, db):
    u, s, h = _founder(db); db.commit()
    import uuid
    r = client.get(f"/api/v1/assessments/{uuid.uuid4()}/next-question", headers=h)
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/assessment/test_next_question.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement — add a shared assessment loader + the route**

```python
# app/api/v1/endpoints/assessments.py  (add)
from app.db.models.assessment import Assessment


def _assessment(db: Session, membership: Membership, assessment_id: uuid.UUID) -> Assessment:
    a = (db.query(Assessment)
         .filter(Assessment.id == assessment_id, Assessment.startup_id == membership.startup_id).first())
    if a is None:
        raise NotFound()
    return a


@router.get("/{assessment_id}/next-question")
def get_next_question(
    assessment_id: uuid.UUID,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    a = _assessment(db, membership, assessment_id)
    startup = _startup(db, membership)
    nq = next_question(ASSESSMENT_BANK, answered_map(db, a), startup)
    return success_response({"next_question": serialize_question(nq)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/assessment/test_next_question.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/assessments.py tests/api/assessment/test_next_question.py
git commit -m "feat(assessment): GET /assessments/{id}/next-question"
```

---

## Task 8: `POST /assessments/{id}/answers` (autosave upsert)

**Files:**
- Modify: `app/api/v1/endpoints/assessments.py`, `app/services/assessment/service.py`
- Test: `tests/api/assessment/test_answers.py`

**Interfaces:**
- Consumes: `AnswerRequest`, `question_by_key`, `validate_answer`, `next_question`, `AssessmentAnswer`, `AssessmentStatus`.
- Produces: `submit_answer(db, assessment, startup, question_key, value) -> Question | None` — 404-able via caller; raises `INVALID_ANSWER` (422) if the assessment isn't in progress, the key isn't the current `next_question`, or the value fails validation; **upserts** the answer; returns the new next question. Endpoint `POST /assessments/{id}/answers`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/assessment/test_answers.py
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.db.models.assessment import AssessmentAnswer
from tests.factories import create_user, create_startup, create_membership


def _founder(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder); db.flush()
    return u, s, {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}


def test_answer_advances_and_upserts(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    r = client.post(f"/api/v1/assessments/{aid}/answers", headers=h,
                    json={"question_key": "product_stage", "value": "mvp"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["next_question"]["key"] != "product_stage"
    # re-answer the same (current-if-resubmitted) question upserts, not duplicates
    n = db.query(AssessmentAnswer).filter(AssessmentAnswer.question_key == "product_stage").count()
    assert n == 1

def test_answer_wrong_question_422(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    # market_clarity is not the current next question (product_stage is)
    r = client.post(f"/api/v1/assessments/{aid}/answers", headers=h,
                    json={"question_key": "market_clarity", "value": 3})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_ANSWER"

def test_answer_bad_value_422(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    r = client.post(f"/api/v1/assessments/{aid}/answers", headers=h,
                    json={"question_key": "product_stage", "value": "bogus"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/assessment/test_answers.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement `submit_answer` + route**

```python
# app/services/assessment/service.py  (add)
from app.core.errors import AppError
from app.db.models.enums import AssessmentStatus
from app.services.assessment.bank import ASSESSMENT_BANK, question_by_key
from app.services.assessment.engine import next_question, validate_answer


def submit_answer(db: Session, assessment: Assessment, startup: Startup,
                  question_key: str, value) -> Question | None:
    if assessment.status != AssessmentStatus.in_progress:
        raise AppError("INVALID_ANSWER", "This assessment is not in progress.", 422)
    answers = answered_map(db, assessment)
    current = next_question(ASSESSMENT_BANK, answers, startup)
    if current is None or current.key != question_key:
        raise AppError("INVALID_ANSWER", "That isn't the current question.", 422)
    validate_answer(current, value)
    row = (db.query(AssessmentAnswer)
           .filter(AssessmentAnswer.assessment_id == assessment.id,
                   AssessmentAnswer.question_key == question_key).first())
    if row is None:
        db.add(AssessmentAnswer(assessment_id=assessment.id, question_key=question_key, value_json=value))
    else:
        row.value_json = value
    db.flush()
    return next_question(ASSESSMENT_BANK, answered_map(db, assessment), startup)
```

```python
# app/api/v1/endpoints/assessments.py  (add)
from app.schemas.assessment import AnswerRequest
from app.services.assessment.service import submit_answer


@router.post("/{assessment_id}/answers")
def post_answer(
    assessment_id: uuid.UUID, payload: AnswerRequest,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    a = _assessment(db, membership, assessment_id)
    startup = _startup(db, membership)
    nq = submit_answer(db, a, startup, payload.question_key, payload.value)
    db.commit()
    return success_response({"next_question": serialize_question(nq)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/assessment/test_answers.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/assessment/service.py app/api/v1/endpoints/assessments.py tests/api/assessment/test_answers.py
git commit -m "feat(assessment): POST /assessments/{id}/answers autosave"
```

---

## Task 9: `POST /assessments/{id}/complete`

**Files:**
- Modify: `app/api/v1/endpoints/assessments.py`, `app/services/assessment/service.py`
- Test: `tests/api/assessment/test_complete.py`

**Interfaces:**
- Consumes: `score`, `next_question`, `AssessmentResult`, `AssessmentType`, `event_bus`, `job_dispatcher`, `AppError`.
- Produces: `complete_assessment(db, assessment, startup) -> dict` — if already `completed`, return stored results (no re-enqueue). Else gate (`next_question` must be None else `422 ASSESSMENT_INCOMPLETE` naming the next key), compute `score`, write `AssessmentResult`, set `status=completed`/`completed_at`; if `type==initial` set `startup.profile.assessment_pending=False`; emit `assessment.completed`; enqueue `healthscore.recalculate` + `roadmap.replan`. Endpoint `POST /assessments/{id}/complete`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/assessment/test_complete.py
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, JobStatus, AssessmentStatus
from app.db.models.job import Job
from app.db.models.assessment import AssessmentResult
from tests.factories import create_user, create_startup, create_membership


def _founder(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder); db.flush()
    return u, s, {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}


def _walk(client, h, aid):
    # answer questions until none remain, using deterministic values
    values = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
    while True:
        nq = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h).json()["data"]["next_question"]
        if nq is None:
            break
        if nq["qtype"] in ("single_choice",):
            val = nq["options"][0]["value"]
        elif nq["qtype"] == "multi_choice":
            val = [nq["options"][0]["value"]]
        else:
            val = values[nq["qtype"]]
        client.post(f"/api/v1/assessments/{aid}/answers", headers=h,
                    json={"question_key": nq["key"], "value": val})


def test_complete_gate_blocks_incomplete(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "ASSESSMENT_INCOMPLETE"

def test_complete_scores_and_sideeffects(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    _walk(client, h, aid)
    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["status"] == "completed" and set(data["dimension_scores"]) == {"product","market","money","legal","team"}
    assert db.query(AssessmentResult).filter_by(assessment_id=aid).count() == 1
    db.refresh(s); db.refresh(s.profile)
    assert s.profile.assessment_pending is False  # initial flips it
    types = {j.type for j in db.query(Job).filter(Job.status == JobStatus.queued).all()}
    assert {"healthscore.recalculate", "roadmap.replan"} <= types

def test_complete_is_idempotent(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    _walk(client, h, aid)
    client.post(f"/api/v1/assessments/{aid}/complete", headers=h)
    before = db.query(Job).count()
    r = client.post(f"/api/v1/assessments/{aid}/complete", headers=h)
    assert r.status_code == 200 and r.json()["data"]["status"] == "completed"
    assert db.query(Job).count() == before  # no new jobs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/assessment/test_complete.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement `complete_assessment` + route**

```python
# app/services/assessment/service.py  (add)
from datetime import UTC, datetime
from app.db.models.assessment import AssessmentResult
from app.db.models.enums import AssessmentType
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.assessment.scoring import score


def _result_dict(r: AssessmentResult) -> dict:
    return {"dimension_scores": r.dimension_scores, "overall_provisional": r.overall_provisional,
            "narrative": r.narrative}


def complete_assessment(db: Session, assessment: Assessment, startup: Startup) -> dict:
    if assessment.status == AssessmentStatus.completed:
        r = db.query(AssessmentResult).filter_by(assessment_id=assessment.id).first()
        return {"status": "completed", **(_result_dict(r) if r else {})}
    answers = answered_map(db, assessment)
    nq = next_question(ASSESSMENT_BANK, answers, startup)
    if nq is not None:
        raise AppError("ASSESSMENT_INCOMPLETE", "A few questions are still unanswered.", 422,
                       field_errors=[{"field": nq.key, "message": "Please answer this question."}])
    scored = score(ASSESSMENT_BANK, answers, startup)
    db.add(AssessmentResult(assessment_id=assessment.id, dimension_scores=scored["dimension_scores"],
                            overall_provisional=scored["overall_provisional"], narrative=scored["narrative"]))
    assessment.status = AssessmentStatus.completed
    assessment.completed_at = datetime.now(UTC)
    if assessment.type == AssessmentType.initial:
        startup.profile.assessment_pending = False
    event_bus.publish("assessment.completed",
                      {"assessment_id": str(assessment.id), "startup_id": str(startup.id),
                       "dimension_scores": scored["dimension_scores"]})
    job_dispatcher.enqueue(db, "healthscore.recalculate", {"startup_id": str(startup.id)}, startup.id)
    job_dispatcher.enqueue(db, "roadmap.replan", {"startup_id": str(startup.id)}, startup.id)
    db.flush()
    return {"status": "completed", **scored}
```

```python
# app/api/v1/endpoints/assessments.py  (add)
from app.services.assessment.service import complete_assessment


@router.post("/{assessment_id}/complete")
def post_complete(
    assessment_id: uuid.UUID,
    membership: Membership = Depends(require_role(MembershipRole.founder)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    a = _assessment(db, membership, assessment_id)
    startup = _startup(db, membership)
    result = complete_assessment(db, a, startup)
    db.commit()
    return success_response(result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/assessment/test_complete.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/assessment/service.py app/api/v1/endpoints/assessments.py tests/api/assessment/test_complete.py
git commit -m "feat(assessment): POST /assessments/{id}/complete (gate + score + side-effects)"
```

---

## Task 10: List, detail, compare

**Files:**
- Modify: `app/api/v1/endpoints/assessments.py`
- Test: `tests/api/assessment/test_results.py`

**Interfaces:**
- Consumes: `require_workspace` (any active member), `Assessment`, `AssessmentResult`, `AssessmentAnswer`.
- Produces: `GET /assessments` (member) → list; `GET /assessments/{id}` (member) → detail (answers grouped by dimension + results); `GET /assessments/compare?ids=a,b,c` (member) → per-id `dimension_scores` (≤3, completed, this workspace; else 422/404).

- [ ] **Step 1: Write the failing test**

```python
# tests/api/assessment/test_results.py
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_user, create_startup, create_membership


def _founder(db):
    u = create_user(db); s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder); db.flush()
    return u, s, {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}


def _walk(client, h, aid):
    vals = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
    while True:
        nq = client.get(f"/api/v1/assessments/{aid}/next-question", headers=h).json()["data"]["next_question"]
        if nq is None:
            break
        val = nq["options"][0]["value"] if nq["qtype"] == "single_choice" else (
            [nq["options"][0]["value"]] if nq["qtype"] == "multi_choice" else vals[nq["qtype"]])
        client.post(f"/api/v1/assessments/{aid}/answers", headers=h, json={"question_key": nq["key"], "value": val})


def test_list_and_detail_and_compare(client, db):
    u, s, h = _founder(db); db.commit()
    aid = client.post("/api/v1/assessments", headers=h).json()["data"]["assessment_id"]
    _walk(client, h, aid)
    client.post(f"/api/v1/assessments/{aid}/complete", headers=h)

    lst = client.get("/api/v1/assessments", headers=h)
    assert lst.status_code == 200 and len(lst.json()["data"]) == 1

    det = client.get(f"/api/v1/assessments/{aid}", headers=h)
    assert det.status_code == 200
    assert det.json()["data"]["result"]["dimension_scores"]

    cmp = client.get(f"/api/v1/assessments/compare?ids={aid}", headers=h)
    assert cmp.status_code == 200 and len(cmp.json()["data"]) == 1

def test_team_member_can_read_results(client, db):
    owner = create_user(db); s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    tm = create_user(db); create_membership(db, tm, s, role=MembershipRole.team_member); db.commit()
    tm_h = {"Authorization": f"Bearer {create_access_token(str(tm.id))}", "X-Workspace-Id": str(s.id)}
    assert client.get("/api/v1/assessments", headers=tm_h).status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/api/assessment/test_results.py -v`
Expected: FAIL — routes missing.

- [ ] **Step 3: Implement the three read routes**

```python
# app/api/v1/endpoints/assessments.py  (add)
from fastapi import Query
from app.db.models.assessment import AssessmentAnswer, AssessmentResult


@router.get("")
def list_assessments(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    rows = (db.query(Assessment).filter(Assessment.startup_id == membership.startup_id)
            .order_by(Assessment.started_at.desc()).all())
    results = {r.assessment_id: r for r in
               db.query(AssessmentResult)
               .filter(AssessmentResult.assessment_id.in_([a.id for a in rows] or [uuid.uuid4()])).all()}
    return success_response([
        {"id": str(a.id), "type": a.type.value, "status": a.status.value,
         "started_at": a.started_at.isoformat(),
         "completed_at": a.completed_at.isoformat() if a.completed_at else None,
         "overall": results[a.id].overall_provisional if a.id in results else None}
        for a in rows])


@router.get("/compare")
def compare_assessments(
    ids: str = Query(...),
    membership: Membership = Depends(require_workspace),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    id_list = [x for x in ids.split(",") if x]
    if len(id_list) > 3:
        raise AppError("VALIDATION_ERROR", "Compare at most 3 assessments.", 422)
    out = []
    for raw in id_list:
        r = (db.query(AssessmentResult).join(Assessment, Assessment.id == AssessmentResult.assessment_id)
             .filter(AssessmentResult.assessment_id == uuid.UUID(raw),
                     Assessment.startup_id == membership.startup_id).first())
        if r is None:
            raise NotFound()
        out.append({"assessment_id": raw, "dimension_scores": r.dimension_scores})
    return success_response(out)


@router.get("/{assessment_id}")
def get_assessment(
    assessment_id: uuid.UUID,
    membership: Membership = Depends(require_workspace),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    a = _assessment(db, membership, assessment_id)
    answers = db.query(AssessmentAnswer).filter(AssessmentAnswer.assessment_id == a.id).all()
    result = db.query(AssessmentResult).filter_by(assessment_id=a.id).first()
    return success_response({
        "id": str(a.id), "type": a.type.value, "status": a.status.value,
        "answers": [{"question_key": x.question_key, "value": x.value_json} for x in answers],
        "result": None if result is None else {
            "dimension_scores": result.dimension_scores,
            "overall_provisional": result.overall_provisional, "narrative": result.narrative},
    })
```
**Route order matters:** define `/compare` BEFORE `/{assessment_id}` so "compare" isn't captured as an id. (In the file, the literal-path route must be registered before the path-param route.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/api/assessment/test_results.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/assessments.py tests/api/assessment/test_results.py
git commit -m "feat(assessment): list, detail, compare read endpoints"
```

---

## Task 11: Live E2E + consolidated SOP

**Files:**
- Create: `e2e/test_assessment.py`, `docs/sop/2026-08-16-assessment.md`

**Interfaces:**
- Consumes: the running server + `make_verified_user`, `http`, `base_url` from `e2e/conftest.py`; the onboarding + assessment endpoints.

- [ ] **Step 1: Write the E2E journey**

```python
# e2e/test_assessment.py
"""Live assessment journey: a founder onboards, completes onboarding, then takes
the adaptive assessment end-to-end and gets scored, with assessment_pending flipped
and the recalc jobs enqueued.
"""
import httpx


def test_assessment_journey(base_url, make_verified_user):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {access}"}

        # Onboard just enough to complete.
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
        c.patch("/api/v1/onboarding/state", headers=auth,
                json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get first customers"]})
        c.post("/api/v1/onboarding/complete", headers=auth)

        # Discover the workspace id from /auth/me, then take the assessment.
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wsid = me["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": wsid}

        start = c.post("/api/v1/assessments", headers=wh)
        assert start.status_code == 201, start.text
        aid = start.json()["data"]["assessment_id"]

        vals = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
        while True:
            nq = c.get(f"/api/v1/assessments/{aid}/next-question", headers=wh).json()["data"]["next_question"]
            if nq is None:
                break
            if nq["qtype"] == "single_choice":
                val = nq["options"][0]["value"]
            elif nq["qtype"] == "multi_choice":
                val = [nq["options"][0]["value"]]
            else:
                val = vals[nq["qtype"]]
            c.post(f"/api/v1/assessments/{aid}/answers", headers=wh,
                   json={"question_key": nq["key"], "value": val})

        done = c.post(f"/api/v1/assessments/{aid}/complete", headers=wh)
        assert done.status_code == 200, done.text
        assert set(done.json()["data"]["dimension_scores"]) == {"product", "market", "money", "legal", "team"}

        # assessment_pending flipped off.
        state = c.get("/api/v1/onboarding/state", headers=auth).json()["data"]
        assert state["assessment_pending"] is False
```

- [ ] **Step 2: Run the E2E + full suite + lint**

Run: `make e2e` (the assessment journey + all prior pass), then `poetry run pytest -q && make lint`. Note the final counts.

- [ ] **Step 3: Write the SOP**

Create `docs/sop/2026-08-16-assessment.md` per the repo convention (What shipped / Why / How / What's involved / Verification / Operate / Follow-ups). Cover: the endpoints; the data model (`assessments`/`assessment_answers`/`assessment_results`, migration `0004`, partial-unique in_progress index); the static versioned bank + adaptive engine + deterministic scoring; the onboarding linkage (`assessment_pending`); events/jobs; and the deferred follow-ups (quarterly cron, AI narrative → Module 03, overall Health Score → Module 06, admin-editable bank → Module 25, go-back editing).

- [ ] **Step 4: Commit**

```bash
git add e2e/test_assessment.py docs/sop/2026-08-16-assessment.md
git commit -m "test(e2e): assessment journey + assessment SOP"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §3 data model → T1/T2; §4 engine/bank → T3/T4; §5 scoring → T5; §6 endpoints → T6 (start/resume), T7 (next-question), T8 (answers), T9 (complete + side-effects + idempotency), T10 (list/detail/compare); §7 errors → INVALID_ANSWER (T4/T8), ASSESSMENT_INCOMPLETE (T9), NotFound framing (T7+); §8 events/jobs → T9; §9 testing → each task + T11 e2e.
- **Type consistency:** `answered_map`, `serialize_question`, `start_or_resume`, `submit_answer`, `complete_assessment`, `next_question`/`is_applicable`/`validate_answer`, `score`, `question_by_key`, `_assessment`/`_startup` are referenced consistently across tasks.
- **Deferred (by design):** quarterly cron, AI narrative (Module 03), overall Health Score (Module 06), async worker draining recalc jobs (Modules 05/06), admin-editable bank (Module 25), go-back answer editing.
- **Route ordering caution (T10):** the literal `/compare` route must be registered before the `/{assessment_id}` catch to avoid capture; sub-paths (`/{id}/next-question` etc.) are unambiguous.
