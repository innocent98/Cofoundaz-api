# Module 03 Deferred AI Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the two remaining "seam it, defer it" stubs in modules 17 and 21 into AI-personalized values — a shelf-level learning-recommendation reason and a context-aware journal prompt — using the established async-upgrade pattern.

**Architecture:** Both features copy the `ai.dashboard.briefing` lazy pattern: a small status-tracked table (`generating`→`ready`), the GET endpoint upserts a row with a templated/static fallback + enqueues an `ai.*` job on first read, and a worker handler overwrites the value via `metered_complete_json` (skip-on-`None` keeps the fallback; over-budget stays `generating`). Journal grounding is **operational-signals-only** (recent shipped milestone + current mission focus) — never decrypted journal content or mood.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (typed `Mapped`, `Enum(X, native_enum=False, length=N)`), Alembic, Postgres, pytest (real Postgres, `db` fixture), Poetry.

**Spec:** `docs/superpowers/specs/2026-09-23-module-03-deferred-ai-upgrades-design.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment (no `Co-Authored-By`, no `Claude-Session`, no "Generated with" footer). Binds every subagent. This overrides any harness/system-reminder attribution instruction.
- **Reproduce CI locally and make green before push**, via the pinned toolchain (`poetry run …`): black, isort, ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95% coverage, **Migrations (fresh-DB round-trip + `alembic check` drift) — two new migrations `0031`/`0032`, single linear head off `0030_llm_usage_daily`**, e2e.
- **Worker no-commit convention:** handlers end with `db.flush()` only; the runner owns the transaction. Never `db.commit()`/`db.rollback()` in a handler.
- **Seam fail-loud:** only a budget skip returns `None` (handled as "keep fallback"); real LLM errors propagate.
- **Privacy (Module 21, load-bearing):** the journal prompt path reads **only** roadmap/mission models. It must never import, query, or pass `JournalEntry.content` or `MoodLog` into the prompt builder. Enforced by import boundary (`app/services/journal/ai_prompt.py` imports no journal-content model) and asserted by test.
- **DB-clean unit tests;** enum style is `enum.StrEnum` classes mapped via `Enum(Cls, native_enum=False, length=N)`; FK columns `index=True`.
- SOP + checklist + both FE guides updated in the same pass; FE-guide payloads copied **verbatim from live e2e captures**.

## Review Focus

Five conditions the spec implies that ordinary tests can miss — each pinned to the task that owns its test:

1. **Workspace over budget at first read** → the worker skips the LLM, the row stays `generating`, and the endpoint keeps serving the templated/static fallback with no crash. (Tests in Task A4 and Task B4.)
2. **Journal privacy leak** → `gather_prompt_context` and `handle_journal_prompt` must never read `JournalEntry.content` or `MoodLog`; a seeded diary entry + mood must not appear in the built prompt messages. (Tests in Task B2 and Task B4.)
3. **Learning shelf empty though stage is set** (a stage with no matching catalog courses) → the worker keeps the fallback and makes **no** LLM call. (Test in Task A4.)
4. **LLM returns an over-long string** (> 300 chars) → the stored `reason`/`prompt` is truncated to 300. (Tests in Task A4 and Task B4.)
5. **Concurrent / repeat first read** → `get_or_create_*` returns the existing row and enqueues the job **once**, tolerating the unique-constraint race via the `begin_nested` + `IntegrityError` re-select. (Tests in Task A3 and Task B3.)

---

## File Structure

**Feature A — Module 17 learning recommendation reason**
- `app/db/models/enums.py` (modify) — add shared `EnrichmentStatus(generating, ready)`.
- `app/db/models/learning.py` (modify) — add `LearningRecommendation` model.
- `app/db/models/__init__.py` (modify) — register `LearningRecommendation`.
- `alembic/versions/0031_learning_recommendations.py` (create).
- `app/services/learning/ai_reason.py` (create) — schema + message builder.
- `app/services/learning/service.py` (modify) — `_templated_reason`, `get_or_create_recommendation_reason`, `_enqueue_learning_reason`.
- `app/api/v1/endpoints/learning.py` (modify) — add `recommendation_reason` to `GET /recommendations`.
- `app/worker/handlers/ai.py` (modify) — `handle_learning_recommendations` + register.

**Feature B — Module 21 context-aware journal prompt**
- `app/db/models/journal.py` (modify) — add `JournalPrompt` model.
- `app/db/models/__init__.py` (modify) — register `JournalPrompt`.
- `alembic/versions/0032_journal_prompts.py` (create).
- `app/services/journal/ai_prompt.py` (create) — schema, `gather_prompt_context`, message builder (imports only roadmap/mission).
- `app/services/journal/service.py` (modify) — `JournalService.get_or_create_today_prompt`.
- `app/api/v1/endpoints/journal.py` (modify) — `GET /journal/prompts/today` upsert + enqueue.
- `app/worker/handlers/ai.py` (modify) — `handle_journal_prompt` + register.

**Docs / verification**
- `e2e/test_learning.py`, `e2e/test_journal.py` (modify) — assert new behavior + capture.
- `docs/fe-integration-guide-*` (learning + journal), `docs/sop/2026-09-23-deferred-ai-upgrades.md`, `docs/checklist/PROJECT_CHECKLIST.md`.

---

## Task A1: `EnrichmentStatus` enum + `LearningRecommendation` model + migration 0031

**Files:**
- Modify: `app/db/models/enums.py` (add enum near `BriefingStatus`, ~line 219)
- Modify: `app/db/models/learning.py`
- Modify: `app/db/models/__init__.py:17`
- Create: `alembic/versions/0031_learning_recommendations.py`
- Test: `tests/db/test_learning_models.py`, `tests/test_learning_migration.py`

**Interfaces:**
- Produces: `EnrichmentStatus(enum.StrEnum)` with members `generating`, `ready`; `LearningRecommendation` model (table `learning_recommendations`) with columns `id, startup_id, stage: str|None, reason: str|None, status: EnrichmentStatus, created_at, updated_at`, unique on `startup_id`.

- [ ] **Step 1: Write the failing model test**

In `tests/db/test_learning_models.py` add:

```python
def test_learning_recommendation_persists(db):
    from app.db.models.enums import EnrichmentStatus, StartupStage
    from app.db.models.learning import LearningRecommendation
    from tests.factories import create_startup, create_user

    s = create_startup(db, owner=create_user(db))
    row = LearningRecommendation(
        startup_id=s.id, stage=StartupStage.build.value,
        reason="Recommended for your build stage.", status=EnrichmentStatus.generating,
    )
    db.add(row)
    db.flush()
    got = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert got.status == EnrichmentStatus.generating
    assert got.reason == "Recommended for your build stage."
```

- [ ] **Step 2: Run it to verify it fails**

Run: `poetry run pytest tests/db/test_learning_models.py::test_learning_recommendation_persists -v`
Expected: FAIL (`ImportError: cannot import name 'EnrichmentStatus'` / `LearningRecommendation`).

- [ ] **Step 3: Add the enum**

In `app/db/models/enums.py`, after `class BriefingStatus(enum.StrEnum):` block:

```python
class EnrichmentStatus(enum.StrEnum):
    generating = "generating"
    ready = "ready"
```

- [ ] **Step 4: Add the model**

In `app/db/models/learning.py`, extend the imports to include `String` and `Enum`, and import the enum:

```python
from sqlalchemy import (
    CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from app.db.models.enums import EnrichmentStatus
```

Append the model:

```python
class LearningRecommendation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "learning_recommendations"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[EnrichmentStatus] = mapped_column(
        Enum(EnrichmentStatus, native_enum=False, length=12),
        nullable=False,
        default=EnrichmentStatus.generating,
    )

    __table_args__ = (UniqueConstraint("startup_id", name="uq_learning_reco_startup"),)
```

- [ ] **Step 5: Register the model**

In `app/db/models/__init__.py:17`, extend the learning import:

```python
from app.db.models.learning import (  # noqa: F401
    Certificate, Enrollment, LearningRecommendation, LessonProgress,
)
```

- [ ] **Step 6: Run the model test — verify it passes**

Run: `poetry run pytest tests/db/test_learning_models.py::test_learning_recommendation_persists -v`
Expected: PASS.

- [ ] **Step 7: Generate the migration**

Run: `poetry run alembic revision --autogenerate -m "learning_recommendations"`, then rename the created file to `alembic/versions/0031_learning_recommendations.py` and set:

```python
revision = "0031_learning_recommendations"
down_revision = "0030_llm_usage_daily"
```

The `upgrade()` must create the table (adjust the autogenerated body to match; mirror `0030`'s style):

```python
def upgrade() -> None:
    op.create_table(
        "learning_recommendations",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column(
            "status",
            sa.Enum("generating", "ready", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["startup_id"], ["startups.id"],
            name=op.f("fk_learning_recommendations_startup_id_startups"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_recommendations")),
        sa.UniqueConstraint("startup_id", name="uq_learning_reco_startup"),
    )
    op.create_index(
        op.f("ix_learning_recommendations_startup_id"), "learning_recommendations", ["startup_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_learning_recommendations_startup_id"), table_name="learning_recommendations")
    op.drop_table("learning_recommendations")
```

- [ ] **Step 8: Verify migration round-trip + drift**

Run: `poetry run alembic upgrade head && poetry run alembic downgrade -1 && poetry run alembic upgrade head && poetry run alembic check`
Expected: upgrade/downgrade succeed; `alembic check` reports no drift; `poetry run alembic heads` shows a single head `0031_learning_recommendations`.

- [ ] **Step 9: Commit**

```bash
git add app/db/models/enums.py app/db/models/learning.py app/db/models/__init__.py \
  alembic/versions/0031_learning_recommendations.py tests/db/test_learning_models.py
git commit -m "feat(learning): add learning_recommendations table + EnrichmentStatus enum"
```

---

## Task A2: Learning reason prompt builder

**Files:**
- Create: `app/services/learning/ai_reason.py`
- Test: `tests/services/learning/test_ai_reason.py`

**Interfaces:**
- Consumes: `StartupStage` (`app/db/models/enums.py`), `LLMMessage` (`app/platform/llm.py`).
- Produces: `learning_reason_schema() -> dict[str, Any]`; `build_learning_reason_messages(*, stage: StartupStage | None, course_titles: list[str]) -> list[LLMMessage]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/learning/test_ai_reason.py
from app.db.models.enums import StartupStage
from app.services.learning.ai_reason import build_learning_reason_messages, learning_reason_schema


def test_schema_shape():
    schema = learning_reason_schema()
    assert schema["properties"]["reason"]["type"] == "string"
    assert schema["required"] == ["reason"]
    assert schema["additionalProperties"] is False


def test_messages_include_stage_and_titles():
    msgs = build_learning_reason_messages(
        stage=StartupStage.build, course_titles=["Pricing 101", "Sales Basics"]
    )
    assert msgs[0].role == "system"
    user = msgs[1].content
    assert "build" in user
    assert "Pricing 101" in user and "Sales Basics" in user


def test_messages_tolerate_no_stage_and_empty_titles():
    msgs = build_learning_reason_messages(stage=None, course_titles=[])
    assert len(msgs) == 2  # no crash, still a valid 2-message prompt
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/learning/test_ai_reason.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the builder**

```python
# app/services/learning/ai_reason.py
from typing import Any

from app.db.models.enums import StartupStage
from app.platform.llm import LLMMessage


def learning_reason_schema() -> dict[str, Any]:
    """Strict OpenAI JSON schema: an object with a single `reason` string."""
    return {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "additionalProperties": False,
    }


def build_learning_reason_messages(
    *, stage: StartupStage | None, course_titles: list[str]
) -> list[LLMMessage]:
    """Prompt for the one-line 'why this shelf' note above recommended courses. No PII."""
    titles = "\n".join(f"- {t}" for t in course_titles) or "- (getting-started courses)"
    stage_label = stage.value if stage is not None else "early"
    system = (
        "You are a startup coach writing the single one-line explanation shown above a shelf of "
        "recommended courses. Write one warm sentence (max 200 characters) that says why these "
        "courses fit this founder right now. One sentence only, no lists."
    )
    user = f"Founder stage: {stage_label}.\nRecommended courses:\n{titles}"
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/services/learning/test_ai_reason.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/learning/ai_reason.py tests/services/learning/test_ai_reason.py
git commit -m "feat(learning): add AI recommendation-reason prompt builder"
```

---

## Task A3: Reason service upsert + endpoint field

**Files:**
- Modify: `app/services/learning/service.py`
- Modify: `app/api/v1/endpoints/learning.py:42-59`
- Test: `tests/services/learning/test_reco_reason.py`, `tests/api/test_learning.py`

**Interfaces:**
- Consumes: `LearningRecommendation`, `EnrichmentStatus`, `job_dispatcher` (already imported in service.py), `StartupStage`.
- Produces: `get_or_create_recommendation_reason(db: Session, startup_id: uuid.UUID, stage: StartupStage | None) -> LearningRecommendation`; the `GET /recommendations` response gains top-level `recommendation_reason: str`.

- [ ] **Step 1: Write the failing service test**

```python
# tests/services/learning/test_reco_reason.py
from app.db.models.enums import EnrichmentStatus, StartupStage
from app.db.models.job import Job
from app.db.models.learning import LearningRecommendation
from app.services.learning.service import get_or_create_recommendation_reason
from tests.factories import create_startup, create_user


def _jobs(db, startup_id):
    return db.query(Job).filter_by(type="ai.learning.recommendations", startup_id=startup_id).all()


def test_first_call_creates_generating_row_and_enqueues(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    db.flush()
    row = get_or_create_recommendation_reason(db, s.id, s.stage)
    assert row.status == EnrichmentStatus.generating
    assert row.stage == "build"
    assert row.reason == "Recommended for your build stage."
    assert len(_jobs(db, s.id)) == 1


def test_second_call_returns_existing_no_duplicate_job(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    db.flush()
    get_or_create_recommendation_reason(db, s.id, s.stage)
    get_or_create_recommendation_reason(db, s.id, s.stage)
    assert db.query(LearningRecommendation).filter_by(startup_id=s.id).count() == 1
    assert len(_jobs(db, s.id)) == 1  # covers the repeat/race re-select branch


def test_stage_change_regenerates_and_reenqueues(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    db.flush()
    row = get_or_create_recommendation_reason(db, s.id, s.stage)
    row.status = EnrichmentStatus.ready
    row.reason = "AI text"
    db.flush()
    s.stage = StartupStage.growth
    db.flush()
    row2 = get_or_create_recommendation_reason(db, s.id, s.stage)
    assert row2.stage == "growth"
    assert row2.status == EnrichmentStatus.generating
    assert row2.reason == "Recommended for your growth stage."
    assert len(_jobs(db, s.id)) == 2


def test_none_stage_uses_generic_fallback(db):
    s = create_startup(db, owner=create_user(db))
    s.stage = None
    db.flush()
    row = get_or_create_recommendation_reason(db, s.id, None)
    assert row.stage is None
    assert row.reason == "Recommended to help you get started."
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/learning/test_reco_reason.py -v`
Expected: FAIL (function missing).

- [ ] **Step 3: Implement the service helper**

In `app/services/learning/service.py`, extend the enum + model imports:

```python
from app.db.models.enums import CourseLevel, EnrichmentStatus, StartupStage
from app.db.models.learning import Certificate, Enrollment, LearningRecommendation, LessonProgress
```

Append:

```python
def _templated_reason(stage: StartupStage | None) -> str:
    if stage is None:
        return "Recommended to help you get started."
    return f"Recommended for your {stage.value} stage."


def _enqueue_learning_reason(db: Session, startup_id: uuid.UUID) -> None:
    job_dispatcher.enqueue(
        db, "ai.learning.recommendations", {"startup_id": str(startup_id)}, startup_id
    )


def get_or_create_recommendation_reason(
    db: Session, startup_id: uuid.UUID, stage: StartupStage | None
) -> LearningRecommendation:
    """Return the startup's shelf-reason row, regenerating (templated + enqueue) when the row is
    absent or its stage no longer matches. Mirrors get_or_create_enrollment's SAVEPOINT + re-select
    on the unique-constraint race.
    """
    stage_val = stage.value if stage is not None else None
    row = db.query(LearningRecommendation).filter_by(startup_id=startup_id).first()
    if row is not None and row.stage == stage_val:
        return row
    if row is not None:  # stage changed -> regenerate in place
        row.stage = stage_val
        row.reason = _templated_reason(stage)
        row.status = EnrichmentStatus.generating
        db.flush()
        _enqueue_learning_reason(db, startup_id)
        return row
    try:
        with db.begin_nested():
            row = LearningRecommendation(
                startup_id=startup_id,
                stage=stage_val,
                reason=_templated_reason(stage),
                status=EnrichmentStatus.generating,
            )
            db.add(row)
            db.flush()
    except IntegrityError:  # concurrent first-load won the unique constraint
        return db.query(LearningRecommendation).filter_by(startup_id=startup_id).first()
    _enqueue_learning_reason(db, startup_id)
    return row
```

(`IntegrityError` and `job_dispatcher` are already imported in this file.)

- [ ] **Step 4: Run service test — verify it passes**

Run: `poetry run pytest tests/services/learning/test_reco_reason.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the endpoint**

In `app/api/v1/endpoints/learning.py`, import the helper (extend the `from app.services.learning.service import (...)` block) and update `get_recommendations`:

```python
    startup = _startup(db, membership)
    enrollments = enrollments_by_course(db, startup.id, membership.user_id)
    completed = {cid for cid, e in enrollments.items() if e.completed_at is not None}
    shelf = recommended_courses(startup.stage, completed)
    watching = continue_watching(db, startup.id, membership.user_id)
    reason_row = get_or_create_recommendation_reason(db, startup.id, startup.stage)
    return success_response(
        {
            "stage": startup.stage.value if startup.stage is not None else None,
            "recommendation_reason": reason_row.reason,
            "recommended": [course_summary(c, enrollments.get(c.id)) for c in shelf],
            "continue_watching": [course_summary(c, e) for c, e in watching],
        }
    )
```

- [ ] **Step 6: Write the failing endpoint test**

In `tests/api/test_learning.py` add (using this file's existing authenticated-founder client fixture — copy the setup used by `test_recommendations_include_continue_watching`):

```python
def test_recommendations_include_reason(client_and_founder):  # reuse the file's fixture name
    client, _ = client_and_founder
    resp = client.get("/api/v1/learning/recommendations")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "recommendation_reason" in data
    assert isinstance(data["recommendation_reason"], str) and data["recommendation_reason"]
```

- [ ] **Step 7: Run endpoint test — verify it passes**

Run: `poetry run pytest tests/api/test_learning.py -v`
Expected: PASS (existing recommendation tests still green — the field is additive).

- [ ] **Step 8: Commit**

```bash
git add app/services/learning/service.py app/api/v1/endpoints/learning.py \
  tests/services/learning/test_reco_reason.py tests/api/test_learning.py
git commit -m "feat(learning): serve recommendation_reason with lazy generating->ready upsert"
```

---

## Task A4: Learning reason worker handler

**Files:**
- Modify: `app/worker/handlers/ai.py`
- Test: `tests/worker/test_learning_reason_handler.py`

**Interfaces:**
- Consumes: `LearningRecommendation`, `EnrichmentStatus`, `recommended_courses` (learning service), `build_learning_reason_messages` + `learning_reason_schema` (learning.ai_reason), `metered_complete_json`, `settings.LLM_MAX_TOKENS`.
- Produces: `handle_learning_recommendations(db, job)`; registered handler `"ai.learning.recommendations"`.

- [ ] **Step 1: Write the failing handler tests**

```python
# tests/worker/test_learning_reason_handler.py
import uuid

from app.core.config import settings
from app.db.models.enums import EnrichmentStatus, StartupStage
from app.db.models.job import Job, JobStatus
from app.db.models.learning import LearningRecommendation
from app.platform import llm_budget
from app.worker.handlers import ai as ai_handlers
from app.worker.handlers.ai import handle_learning_recommendations
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_usage_tokens = 5

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return self.payload


def _row(db, startup_id, stage="build", status=EnrichmentStatus.generating, reason="fallback"):
    r = LearningRecommendation(startup_id=startup_id, stage=stage, reason=reason, status=status)
    db.add(r)
    db.flush()
    return r


def _job(startup_id):
    return Job(
        type="ai.learning.recommendations",
        payload={"startup_id": str(startup_id)},
        status=JobStatus.running,
    )


def test_writes_reason_and_marks_ready(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id)
    fake = _FakeLLM({"reason": "Because you're at build stage, focus on pricing."})
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_learning_recommendations(db, _job(s.id))
    row = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.reason == "Because you're at build stage, focus on pricing."
    assert fake.calls == 1


def test_over_budget_keeps_generating(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id, reason="fallback")
    llm_budget.debit(db, s.id, 5)  # push over the 1-token budget
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))
    row = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert row.status == EnrichmentStatus.generating  # stuck on fallback, no crash
    assert row.reason == "fallback"


def test_idempotent_when_ready(db, monkeypatch):
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id, status=EnrichmentStatus.ready, reason="done")
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))  # no LLM call, no raise


def test_none_stage_marks_ready_no_llm(db, monkeypatch):
    s = create_startup(db, owner=create_user(db))
    s.stage = None
    _row(db, s.id, stage=None, reason="Recommended to help you get started.")
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))
    row = db.query(LearningRecommendation).filter_by(startup_id=s.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.reason == "Recommended to help you get started."


def test_empty_shelf_marks_ready_no_llm(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id)
    monkeypatch.setattr(ai_handlers, "recommended_courses", lambda stage, ids: [])
    monkeypatch.setattr(
        llm_budget, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError())
    )
    handle_learning_recommendations(db, _job(s.id))
    assert db.query(LearningRecommendation).filter_by(startup_id=s.id).one().status == EnrichmentStatus.ready


def test_truncates_reason_to_300(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    s = create_startup(db, owner=create_user(db))
    s.stage = StartupStage.build
    _row(db, s.id)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM({"reason": "x" * 500}))
    handle_learning_recommendations(db, _job(s.id))
    assert len(db.query(LearningRecommendation).filter_by(startup_id=s.id).one().reason) == 300


def test_noop_when_startup_missing(db):
    handle_learning_recommendations(db, _job(uuid.uuid4()))  # no raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/worker/test_learning_reason_handler.py -v`
Expected: FAIL (`handle_learning_recommendations` not defined).

- [ ] **Step 3: Implement the handler**

In `app/worker/handlers/ai.py`, extend imports:

```python
from app.db.models.enums import BriefingStatus, CanvasType, EnrichmentStatus, RecommendationStatus, RecordKind
from app.db.models.learning import LearningRecommendation
from app.services.learning.ai_reason import build_learning_reason_messages, learning_reason_schema
from app.services.learning.service import recommended_courses
```

Append (near the other `ai.*` handlers):

```python
def handle_learning_recommendations(db: Session, job: Job) -> None:
    """Write the shelf-level recommendation reason via the LLM. No commit.

    Idempotent: no-ops unless the row is still `generating`. Keeps the templated fallback when the
    startup has no stage, no recommendable courses, or the workspace is over budget.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    row = (
        db.query(LearningRecommendation).filter_by(startup_id=startup.id).one_or_none()
    )
    if row is None or row.status != EnrichmentStatus.generating:
        return
    stage = startup.stage
    titles = [c.title for c in recommended_courses(stage, set())] if stage is not None else []
    if stage is None or not titles:  # nothing to personalize -> keep fallback, no LLM call
        row.status = EnrichmentStatus.ready
        db.flush()
        return
    result = metered_complete_json(
        db,
        startup.id,
        build_learning_reason_messages(stage=stage, course_titles=titles),
        schema=learning_reason_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — keep the templated reason, stay generating
    row.reason = str(result["reason"])[:300]
    row.status = EnrichmentStatus.ready
    db.flush()


register_handler("ai.learning.recommendations", handle_learning_recommendations)
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/worker/test_learning_reason_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/test_learning_reason_handler.py
git commit -m "feat(learning): ai.learning.recommendations worker fills the shelf reason"
```

---

## Task B1: `JournalPrompt` model + migration 0032

**Files:**
- Modify: `app/db/models/journal.py`
- Modify: `app/db/models/__init__.py:16`
- Create: `alembic/versions/0032_journal_prompts.py`
- Test: `tests/db/test_journal_models.py`, `tests/test_journal_migration.py`

**Interfaces:**
- Consumes: `EnrichmentStatus` (from Task A1).
- Produces: `JournalPrompt` model (table `journal_prompts`) with `id, startup_id, founder_id, date, prompt: str, status: EnrichmentStatus, created_at, updated_at`, unique on `(startup_id, founder_id, date)`.

- [ ] **Step 1: Write the failing model test**

```python
def test_journal_prompt_persists(db):
    from datetime import date
    from app.db.models.enums import EnrichmentStatus
    from app.db.models.journal import JournalPrompt
    from tests.factories import create_startup, create_user

    u = create_user(db)
    s = create_startup(db, owner=u)
    row = JournalPrompt(
        startup_id=s.id, founder_id=u.id, date=date.today(),
        prompt="What moved forward today?", status=EnrichmentStatus.generating,
    )
    db.add(row)
    db.flush()
    got = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert got.status == EnrichmentStatus.generating
    assert got.prompt == "What moved forward today?"
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_journal_models.py::test_journal_prompt_persists -v`
Expected: FAIL (import error).

- [ ] **Step 3: Add the model**

In `app/db/models/journal.py`, extend the `sqlalchemy` import to add `Enum` and `String`, and import the enum:

```python
from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from app.db.models.enums import EnrichmentStatus
```

Append:

```python
class JournalPrompt(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "journal_prompts"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    founder_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    prompt: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[EnrichmentStatus] = mapped_column(
        Enum(EnrichmentStatus, native_enum=False, length=12),
        nullable=False,
        default=EnrichmentStatus.generating,
    )

    __table_args__ = (
        UniqueConstraint(
            "startup_id", "founder_id", "date", name="uq_journal_prompts_startup_founder_date"
        ),
    )
```

- [ ] **Step 4: Register the model**

In `app/db/models/__init__.py:16`:

```python
from app.db.models.journal import JournalEntry, JournalPrompt, MoodLog  # noqa: F401
```

- [ ] **Step 5: Run model test — verify it passes**

Run: `poetry run pytest tests/db/test_journal_models.py::test_journal_prompt_persists -v`
Expected: PASS.

- [ ] **Step 6: Generate the migration**

Run: `poetry run alembic revision --autogenerate -m "journal_prompts"`, rename to `alembic/versions/0032_journal_prompts.py`, set:

```python
revision = "0032_journal_prompts"
down_revision = "0031_learning_recommendations"
```

`upgrade()`:

```python
def upgrade() -> None:
    op.create_table(
        "journal_prompts",
        sa.Column("startup_id", sa.UUID(), nullable=False),
        sa.Column("founder_id", sa.UUID(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("prompt", sa.String(length=300), nullable=False),
        sa.Column("status", sa.Enum("generating", "ready", native_enum=False, length=12), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["startup_id"], ["startups.id"],
            name=op.f("fk_journal_prompts_startup_id_startups"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["founder_id"], ["users.id"],
            name=op.f("fk_journal_prompts_founder_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_prompts")),
        sa.UniqueConstraint("startup_id", "founder_id", "date",
            name="uq_journal_prompts_startup_founder_date"),
    )
    op.create_index(op.f("ix_journal_prompts_startup_id"), "journal_prompts", ["startup_id"], unique=False)
    op.create_index(op.f("ix_journal_prompts_founder_id"), "journal_prompts", ["founder_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_journal_prompts_founder_id"), table_name="journal_prompts")
    op.drop_index(op.f("ix_journal_prompts_startup_id"), table_name="journal_prompts")
    op.drop_table("journal_prompts")
```

- [ ] **Step 7: Verify round-trip + drift + single head**

Run: `poetry run alembic upgrade head && poetry run alembic downgrade -1 && poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads`
Expected: succeeds, no drift, single head `0032_journal_prompts`.

- [ ] **Step 8: Commit**

```bash
git add app/db/models/journal.py app/db/models/__init__.py \
  alembic/versions/0032_journal_prompts.py tests/db/test_journal_models.py
git commit -m "feat(journal): add journal_prompts table"
```

---

## Task B2: Journal prompt builder + operational-only context gatherer

**Files:**
- Create: `app/services/journal/ai_prompt.py`
- Test: `tests/services/journal/test_ai_prompt.py`

**Interfaces:**
- Consumes: `RoadmapStatus`, `Roadmap`, `RoadmapPhase`, `RoadmapMilestone`, `Mission`, `MissionTask`, `LLMMessage`.
- Produces: `journal_prompt_schema() -> dict`; `gather_prompt_context(db, startup_id) -> tuple[str | None, str | None]` (returns `(milestone_title, mission_focus)`); `build_journal_prompt_messages(*, milestone_title: str | None, mission_focus: str | None) -> list[LLMMessage]`.
- **Privacy invariant:** this module imports NO journal-content model (`JournalEntry`, `MoodLog`). Keep it that way.

- [ ] **Step 1: Write the failing tests (incl. the privacy test)**

```python
# tests/services/journal/test_ai_prompt.py
from datetime import date

from app.db.models.enums import MissionStatus, RoadmapStatus, TaskEffort
from app.db.models.journal import JournalEntry, MoodLog
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.services.journal.ai_prompt import (
    build_journal_prompt_messages, gather_prompt_context, journal_prompt_schema,
)
from tests.factories import create_startup, create_user


def _shipped_milestone(db, startup, title):
    r = Roadmap(startup_id=startup.id, stage=startup.stage)
    db.add(r); db.flush()
    p = RoadmapPhase(roadmap_id=r.id, name="Phase 1", order=0)
    db.add(p); db.flush()
    m = RoadmapMilestone(phase_id=p.id, title=title, status=RoadmapStatus.done)
    db.add(m); db.flush()
    return m


def _mission_with_task(db, startup, task_title):
    mi = Mission(startup_id=startup.id, mission_date=date.today(), generated_by="system",
                 status=MissionStatus.pending)
    db.add(mi); db.flush()
    db.add(MissionTask(mission_id=mi.id, title=task_title, effort=TaskEffort.medium, order=0))
    db.flush()


def test_schema_shape():
    s = journal_prompt_schema()
    assert s["properties"]["prompt"]["type"] == "string"
    assert s["required"] == ["prompt"]


def test_gather_returns_operational_signals(db):
    s = create_startup(db, owner=create_user(db))
    _shipped_milestone(db, s, "Launched the beta")
    _mission_with_task(db, s, "Email 10 leads")
    milestone_title, mission_focus = gather_prompt_context(db, s.id)
    assert milestone_title == "Launched the beta"
    assert mission_focus == "Email 10 leads"


def test_gather_returns_none_without_signals(db):
    s = create_startup(db, owner=create_user(db))
    assert gather_prompt_context(db, s.id) == (None, None)


def test_privacy_journal_and_mood_never_surface(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _shipped_milestone(db, s, "Launched the beta")
    # Seed a diary entry + mood with distinctive content that must NOT reach the prompt.
    db.add(JournalEntry(startup_id=s.id, founder_id=u.id, date=date.today(),
                        content_encrypted="SECRET_DIARY_TEXT", mood=1, stress=9))
    db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=date.today(), mood=1, stress=9))
    db.flush()
    milestone_title, mission_focus = gather_prompt_context(db, s.id)
    msgs = build_journal_prompt_messages(milestone_title=milestone_title, mission_focus=mission_focus)
    blob = " ".join(m.content for m in msgs)
    assert "SECRET_DIARY_TEXT" not in blob
    assert "stress" not in blob.lower()
    assert "Launched the beta" in blob  # operational signal is present


def test_build_messages_shape():
    msgs = build_journal_prompt_messages(milestone_title="X", mission_focus=None)
    assert msgs[0].role == "system" and msgs[1].role == "user"
    assert "X" in msgs[1].content
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/services/journal/test_ai_prompt.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the builder + gatherer**

```python
# app/services/journal/ai_prompt.py
"""AI journal-prompt inputs. Privacy: this module reads ONLY operational signals
(roadmap milestones + mission focus). It must never import JournalEntry or MoodLog."""
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.platform.llm import LLMMessage


def journal_prompt_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"prompt": {"type": "string"}},
        "required": ["prompt"],
        "additionalProperties": False,
    }


def gather_prompt_context(db: Session, startup_id: uuid.UUID) -> tuple[str | None, str | None]:
    """Return (most-recent shipped milestone title, current mission focus). Operational only."""
    milestone = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id, RoadmapMilestone.status == RoadmapStatus.done)
        .order_by(RoadmapMilestone.updated_at.desc())
        .first()
    )
    milestone_title = milestone.title if milestone is not None else None

    mission = (
        db.query(Mission)
        .filter(Mission.startup_id == startup_id)
        .order_by(Mission.mission_date.desc())
        .first()
    )
    mission_focus: str | None = None
    if mission is not None:
        task = (
            db.query(MissionTask)
            .filter_by(mission_id=mission.id)
            .order_by(MissionTask.order)
            .first()
        )
        mission_focus = task.title if task is not None else None
    return milestone_title, mission_focus


def build_journal_prompt_messages(
    *, milestone_title: str | None, mission_focus: str | None
) -> list[LLMMessage]:
    """Prompt for one warm reflective journal question grounded in operational progress only."""
    lines = []
    if milestone_title:
        lines.append(f"Recently shipped: {milestone_title}")
    if mission_focus:
        lines.append(f"Current focus: {mission_focus}")
    context = "\n".join(lines)
    system = (
        "You are a warm journaling coach. Write ONE short, open reflective question (max 200 "
        "characters) for a founder's private daily journal, grounded in their recent progress. "
        "One question only, no preamble."
    )
    user = f"Founder's recent progress:\n{context}"
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/services/journal/test_ai_prompt.py -v`
Expected: PASS (including the privacy test).

- [ ] **Step 5: Commit**

```bash
git add app/services/journal/ai_prompt.py tests/services/journal/test_ai_prompt.py
git commit -m "feat(journal): operational-only prompt context gatherer + AI prompt builder"
```

---

## Task B3: Journal prompt upsert service + endpoint

**Files:**
- Modify: `app/services/journal/service.py`
- Modify: `app/api/v1/endpoints/journal.py:235-254`
- Test: `tests/services/journal/test_prompt_upsert.py`, `tests/api/test_journal_access.py` (or the file holding the prompt-endpoint test)

**Interfaces:**
- Consumes: `JournalPrompt`, `EnrichmentStatus`, `job_dispatcher`, `IntegrityError`, `JournalService.get_prompt`.
- Produces: `JournalService.get_or_create_today_prompt(db, *, startup_id, founder_id, today=None) -> JournalPrompt`; endpoint returns `JournalPromptResponse(prompt=row.prompt)`.

- [ ] **Step 1: Write the failing service test**

```python
# tests/services/journal/test_prompt_upsert.py
from datetime import date

from app.db.models.enums import EnrichmentStatus
from app.db.models.job import Job
from app.db.models.journal import JournalPrompt
from app.services.journal.service import JournalService
from tests.factories import create_startup, create_user


def _jobs(db, startup_id):
    return db.query(Job).filter_by(type="ai.journal.prompt", startup_id=startup_id).all()


def test_first_call_creates_static_generating_row_and_enqueues(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = JournalService.get_or_create_today_prompt(db, startup_id=s.id, founder_id=u.id)
    assert row.status == EnrichmentStatus.generating
    assert row.prompt == JournalService.get_prompt(today=date.today())
    assert len(_jobs(db, s.id)) == 1


def test_second_call_returns_existing_no_duplicate(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    JournalService.get_or_create_today_prompt(db, startup_id=s.id, founder_id=u.id)
    JournalService.get_or_create_today_prompt(db, startup_id=s.id, founder_id=u.id)
    assert db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).count() == 1
    assert len(_jobs(db, s.id)) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/journal/test_prompt_upsert.py -v`
Expected: FAIL (method missing).

- [ ] **Step 3: Implement the service method**

In `app/services/journal/service.py` add imports:

```python
from sqlalchemy.exc import IntegrityError
from app.db.models.enums import EnrichmentStatus
from app.db.models.journal import JournalEntry, JournalPrompt, MoodLog  # extend existing import
from app.platform.jobs import job_dispatcher
```

Add the staticmethod to `JournalService`:

```python
    @staticmethod
    def get_or_create_today_prompt(
        db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID, today: date | None = None
    ) -> JournalPrompt:
        """Return today's prompt row, seeding the static fallback + enqueuing the AI job on first
        read. Idempotent per (startup, founder, day); tolerates the unique-constraint race."""
        d = today or date.today()
        row = (
            db.query(JournalPrompt)
            .filter_by(startup_id=startup_id, founder_id=founder_id, date=d)
            .first()
        )
        if row is not None:
            return row
        try:
            with db.begin_nested():
                row = JournalPrompt(
                    startup_id=startup_id,
                    founder_id=founder_id,
                    date=d,
                    prompt=JournalService.get_prompt(today=d),
                    status=EnrichmentStatus.generating,
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            return (
                db.query(JournalPrompt)
                .filter_by(startup_id=startup_id, founder_id=founder_id, date=d)
                .first()
            )
        job_dispatcher.enqueue(
            db,
            "ai.journal.prompt",
            {"startup_id": str(startup_id), "founder_id": str(founder_id), "date": d.isoformat()},
            startup_id,
        )
        return row
```

(Confirm `uuid` and `date` are imported in this file; add if missing.)

- [ ] **Step 4: Run service test — verify it passes**

Run: `poetry run pytest tests/services/journal/test_prompt_upsert.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the endpoint**

In `app/api/v1/endpoints/journal.py`, replace the body of `get_today_journal_prompt`:

```python
    startup = _startup(db, membership)
    _require_founder(db, user=user, startup=startup)
    row = JournalService.get_or_create_today_prompt(
        db, startup_id=startup.id, founder_id=user.id
    )
    return success_response(JournalPromptResponse(prompt=row.prompt).model_dump())
```

- [ ] **Step 6: Extend the endpoint test**

In the API test file that already exercises `GET /journal/prompts/today`, assert the shape is unchanged and a job is enqueued (reuse the file's authenticated-founder client fixture):

```python
def test_prompt_today_shape_unchanged_and_enqueues(founder_client, db):
    resp = founder_client.get("/api/v1/journal/prompts/today")
    assert resp.status_code == 200
    assert set(resp.json()["data"].keys()) == {"prompt"}
    assert isinstance(resp.json()["data"]["prompt"], str)
```

- [ ] **Step 7: Run — verify it passes**

Run: `poetry run pytest tests/api -k journal -v`
Expected: PASS (existing journal tests still green; shape unchanged).

- [ ] **Step 8: Commit**

```bash
git add app/services/journal/service.py app/api/v1/endpoints/journal.py \
  tests/services/journal/test_prompt_upsert.py tests/api
git commit -m "feat(journal): lazy generating->ready upsert for today's prompt"
```

---

## Task B4: Journal prompt worker handler

**Files:**
- Modify: `app/worker/handlers/ai.py`
- Test: `tests/worker/test_journal_prompt_handler.py`

**Interfaces:**
- Consumes: `JournalPrompt`, `EnrichmentStatus`, `User`, `gather_prompt_context`, `build_journal_prompt_messages`, `journal_prompt_schema`, `metered_complete_json`, `date`.
- Produces: `handle_journal_prompt(db, job)`; registered handler `"ai.journal.prompt"`.

- [ ] **Step 1: Write the failing handler tests**

```python
# tests/worker/test_journal_prompt_handler.py
import uuid
from datetime import date

from app.core.config import settings
from app.db.models.enums import EnrichmentStatus, MissionStatus, RoadmapStatus, TaskEffort
from app.db.models.job import Job, JobStatus
from app.db.models.journal import JournalEntry, JournalPrompt, MoodLog
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.platform import llm_budget
from app.worker.handlers.ai import handle_journal_prompt
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_usage_tokens = 5

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        self.seen = " ".join(m.content for m in messages)
        return self.payload


def _shipped_milestone(db, startup, title="Shipped v1"):
    r = Roadmap(startup_id=startup.id, stage=startup.stage); db.add(r); db.flush()
    p = RoadmapPhase(roadmap_id=r.id, name="P", order=0); db.add(p); db.flush()
    db.add(RoadmapMilestone(phase_id=p.id, title=title, status=RoadmapStatus.done)); db.flush()


def _prompt_row(db, startup_id, founder_id, status=EnrichmentStatus.generating, prompt="static Q"):
    row = JournalPrompt(startup_id=startup_id, founder_id=founder_id, date=date.today(),
                        prompt=prompt, status=status)
    db.add(row); db.flush()
    return row


def _job(startup_id, founder_id):
    return Job(type="ai.journal.prompt",
               payload={"startup_id": str(startup_id), "founder_id": str(founder_id),
                        "date": date.today().isoformat()},
               status=JobStatus.running)


def test_writes_prompt_and_marks_ready(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db); s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id)
    fake = _FakeLLM({"prompt": "How did shipping v1 change your week?"})
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_journal_prompt(db, _job(s.id, u.id))
    row = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.prompt == "How did shipping v1 change your week?"
    assert fake.calls == 1


def test_no_signal_marks_ready_no_llm(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db); s = create_startup(db, owner=u)  # no milestone, no mission
    _prompt_row(db, s.id, u.id, prompt="static Q")
    monkeypatch.setattr(llm_budget, "get_llm_client",
                        lambda: (_ for _ in ()).throw(AssertionError()))
    handle_journal_prompt(db, _job(s.id, u.id))
    row = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert row.status == EnrichmentStatus.ready
    assert row.prompt == "static Q"


def test_over_budget_keeps_generating(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1)
    u = create_user(db); s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id, prompt="static Q")
    llm_budget.debit(db, s.id, 5)
    monkeypatch.setattr(llm_budget, "get_llm_client",
                        lambda: (_ for _ in ()).throw(AssertionError()))
    handle_journal_prompt(db, _job(s.id, u.id))
    row = db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one()
    assert row.status == EnrichmentStatus.generating
    assert row.prompt == "static Q"


def test_idempotent_when_ready(db, monkeypatch):
    u = create_user(db); s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id, status=EnrichmentStatus.ready, prompt="done")
    monkeypatch.setattr(llm_budget, "get_llm_client",
                        lambda: (_ for _ in ()).throw(AssertionError()))
    handle_journal_prompt(db, _job(s.id, u.id))  # no raise, no LLM


def test_truncates_prompt_to_300(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db); s = create_startup(db, owner=u)
    _shipped_milestone(db, s)
    _prompt_row(db, s.id, u.id)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _FakeLLM({"prompt": "y" * 500}))
    handle_journal_prompt(db, _job(s.id, u.id))
    assert len(db.query(JournalPrompt).filter_by(startup_id=s.id, founder_id=u.id).one().prompt) == 300


def test_privacy_diary_and_mood_never_sent(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    u = create_user(db); s = create_startup(db, owner=u)
    _shipped_milestone(db, s, title="Closed first customer")
    db.add(JournalEntry(startup_id=s.id, founder_id=u.id, date=date.today(),
                        content_encrypted="SECRET_DIARY", mood=1, stress=8))
    db.add(MoodLog(startup_id=s.id, founder_id=u.id, date=date.today(), mood=1, stress=8))
    db.flush()
    _prompt_row(db, s.id, u.id)
    fake = _FakeLLM({"prompt": "What did closing your first customer teach you?"})
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    handle_journal_prompt(db, _job(s.id, u.id))
    assert "SECRET_DIARY" not in fake.seen
    assert "Closed first customer" in fake.seen


def test_noop_when_startup_missing(db):
    handle_journal_prompt(db, _job(uuid.uuid4(), uuid.uuid4()))  # no raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/worker/test_journal_prompt_handler.py -v`
Expected: FAIL (`handle_journal_prompt` not defined).

- [ ] **Step 3: Implement the handler**

In `app/worker/handlers/ai.py`, extend imports:

```python
from app.db.models.journal import JournalPrompt
from app.db.models.user import User
from app.services.journal.ai_prompt import (
    build_journal_prompt_messages, gather_prompt_context, journal_prompt_schema,
)
```

Append:

```python
def handle_journal_prompt(db: Session, job: Job) -> None:
    """Personalize a founder's daily journal prompt via the LLM. No commit.

    Grounds ONLY in operational signals (shipped milestone + mission focus) — never journal
    content or mood. Idempotent: no-ops unless the row is still `generating`. Keeps the static
    prompt when there is no signal or the workspace is over budget.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return
    founder = db.get(User, job.payload["founder_id"])
    if founder is None:
        return
    d = date.fromisoformat(job.payload["date"])
    row = (
        db.query(JournalPrompt)
        .filter_by(startup_id=startup.id, founder_id=founder.id, date=d)
        .one_or_none()
    )
    if row is None or row.status != EnrichmentStatus.generating:
        return
    milestone_title, mission_focus = gather_prompt_context(db, startup.id)
    if milestone_title is None and mission_focus is None:  # no signal -> keep static, no LLM
        row.status = EnrichmentStatus.ready
        db.flush()
        return
    result = metered_complete_json(
        db,
        startup.id,
        build_journal_prompt_messages(milestone_title=milestone_title, mission_focus=mission_focus),
        schema=journal_prompt_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        return  # over budget — keep the static prompt, stay generating
    row.prompt = str(result["prompt"])[:300]
    row.status = EnrichmentStatus.ready
    db.flush()


register_handler("ai.journal.prompt", handle_journal_prompt)
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/worker/test_journal_prompt_handler.py -v`
Expected: PASS (including the privacy test).

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/test_journal_prompt_handler.py
git commit -m "feat(journal): ai.journal.prompt worker personalizes the daily prompt"
```

---

## Task 9: E2E coverage + live captures

**Files:**
- Modify: `e2e/test_learning.py`, `e2e/test_journal.py`
- Produces: `e2e/_captures/learning/recommendations_reason.json`, `e2e/_captures/journal/prompt_today_ai.json` (or the honestly-labelled fallback capture — see Step 3).

**Interfaces:**
- Consumes: the `capture` and `make_verified_user` fixtures from `e2e/conftest.py`; the existing per-file journey helpers.

- [ ] **Step 1: Extend the learning e2e journey**

In `e2e/test_learning.py`, after fetching `GET /learning/recommendations`, assert the new field and capture it:

```python
        recs = get(client, "/api/v1/learning/recommendations")
        assert "recommendation_reason" in recs["data"]
        capture("learning", "recommendations_reason", recs)
```

- [ ] **Step 2: Extend the journal e2e journey**

In `e2e/test_journal.py`, after `GET /journal/prompts/today`, assert the shape and capture:

```python
        prompt = get(client, "/api/v1/journal/prompts/today")
        assert set(prompt["data"].keys()) == {"prompt"}
        capture("journal", "prompt_today_ai", prompt)
```

- [ ] **Step 3: Decide the AI-value capture honestly**

Check whether `scripts/e2e_run.sh` drains the job worker during the run (look at how the dashboard-briefing e2e obtains its AI value; grep the e2e harness for a drain/worker step).
- **If the worker drains:** after the drain, re-fetch each endpoint and capture the AI-upgraded body (under the stub provider the value is `"[stub-llm] …"`); note in the FE guide that this is the stub value and real OpenAI returns real text.
- **If it does NOT drain:** the captured value is the templated/static fallback with `status` still generating. Capture that, and in the FE guide (Task 10) mark the AI-upgraded value as "shape verified by unit test, not exercised live" with the exact stub-produced shape — never present an un-captured body as live.

- [ ] **Step 4: Run e2e**

Run: `bash scripts/e2e_run.sh`
Expected: the learning + journal journeys pass; the two new capture files are written.

- [ ] **Step 5: Commit**

```bash
git add e2e/test_learning.py e2e/test_journal.py e2e/_captures/learning/ e2e/_captures/journal/
git commit -m "test(e2e): cover learning recommendation_reason + journal AI prompt, capture bodies"
```

---

## Task 10: FE guides + SOP + checklist reconcile

**Files:**
- Modify/Create: `docs/fe-integration-guide-learning-recommendations.md` (new if none covers recommendations, else update the learning guide), `docs/fe-integration-guide-journal.md`
- Create: `docs/sop/2026-09-23-deferred-ai-upgrades.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:**
- Consumes: the capture files written in Task 9 (payloads must be pasted verbatim from them).

- [ ] **Step 1: Learning FE guide**

Document `GET /learning/recommendations`: the new `recommendation_reason` field (always present — templated first, AI once ready), the generating→ready upgrade (poll again to get the personalized line), and over-budget behavior (the line stays templated; cross-reference `docs/fe-integration-guide-ai-status.md` — `over_budget` explains it). Paste the response body **verbatim from `e2e/_captures/learning/recommendations_reason.json`**.

- [ ] **Step 2: Journal FE guide**

In `docs/fe-integration-guide-journal.md`, document that `GET /journal/prompts/today` keeps its `{prompt}` shape but the value may upgrade from a static line to a personalized one on a later poll; over-budget keeps the static line (cross-ref ai-status). Paste the body **verbatim from the Task 9 capture**, honestly labelled per Task 9 Step 3.

- [ ] **Step 3: SOP**

Write `docs/sop/2026-09-23-deferred-ai-upgrades.md` covering both features: what shipped (the two AI upgrades + tables `0031`/`0032` + two `ai.*` handlers), why (close the two deferred Module-03 AI stubs), how (dashboard-briefing lazy pattern, operational-only journal grounding for privacy), files/migrations touched, verification (unit + e2e), and follow-ups (per-course reasons, weakest-dimension grounding, mood-aware prompts behind consent).

- [ ] **Step 4: Checklist reconcile**

In `docs/checklist/PROJECT_CHECKLIST.md`, under "Deferred AI upgrades (unblocked by Module 03, not yet built)", flip the **Module 17** and **Module 21** items from `- [ ]` to `- [x]` with this PR/date, leaving the Module 09 item unchecked (still with the junior).

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: FE guides + SOP + checklist for the two Module 03 deferred AI upgrades"
```

---

## Final verification (before opening the PR)

- [ ] **Full local CI parity, all green:**

```bash
poetry run black --check app tests && poetry run isort --check-only app tests && \
poetry run ruff check app tests && poetry run mypy app && \
poetry run pylint app --fail-under=9.5 && poetry run bandit -q -r app && \
poetry run pytest --cov=app --cov-fail-under=95 && \
poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads && \
bash scripts/e2e_run.sh
```

Expected: every check passes; `alembic heads` shows the single head `0032_journal_prompts`.

- [ ] **Confirm no AI attribution** in any commit on the branch: `git log develop..HEAD --format='%an <%ae>%n%b'` shows no `Co-Authored-By: Claude`, no `Claude-Session`, no "Generated with" footer.

---

## Self-Review

**Spec coverage:** In-scope items 1–5 map to Tasks A1/B1 (tables+migrations), A2/B2 (builders + gatherer), A3/B3 (endpoints+service), A4/B4 (workers), Task 9 (e2e/captures), Task 10 (FE guides/SOP/checklist). Both worker skip-on-`None`, idempotency, and no-signal short-circuits are covered. The privacy invariant has dedicated tests in B2 and B4.

**Placeholder scan:** No "TBD/TODO/handle edge cases" — every code and test step carries real content. The only deliberately conditional step is Task 9 Step 3 (drain-or-not), which gives both concrete branches and the honesty rule, not a placeholder.

**Type consistency:** `EnrichmentStatus` (A1) is reused by B1/A3/A4/B3/B4. `get_or_create_recommendation_reason(db, startup_id, stage)`, `get_or_create_today_prompt(db, *, startup_id, founder_id, today=None)`, `gather_prompt_context(db, startup_id) -> tuple[str|None,str|None]`, `build_learning_reason_messages(*, stage, course_titles)`, `build_journal_prompt_messages(*, milestone_title, mission_focus)`, `learning_reason_schema()`, `journal_prompt_schema()` — signatures match across producing and consuming tasks. `recommended_courses(stage, completed_ids)` matches the existing service function.

**Review Focus:** all five items have owning tests (1→A4/B4, 2→B2/B4, 3→A4, 4→A4/B4, 5→A3/B3).
