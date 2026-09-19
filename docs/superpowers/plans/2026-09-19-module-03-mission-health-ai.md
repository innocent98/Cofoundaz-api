# Mission reason + Health recommendation AI upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade two templated text surfaces — each `MissionTask.reason` and each pending `HealthRecommendation.body` — to real LLM text via async worker jobs, using the assessment-narrative async-upgrade pattern (templated value stays as the instant value + permanent fallback).

**Architecture:** Two new worker job types (`ai.mission.reason`, `ai.health.recommendations`), each enqueued in the existing write-on-read/recompute path and each consumed by one handler in `app/worker/handlers/ai.py` that makes a single `complete_json` structured call and overwrites the stored text in place. No migration — all fields exist. No new endpoints or notifications.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Postgres, the job worker (`app/worker/`), the LLM seam (`app/platform/llm.py`), pytest (real Postgres, `db` fixture = per-test rollback), Poetry.

**Spec:** `docs/superpowers/specs/2026-09-19-module-03-mission-health-ai.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment (no `Co-Authored-By`, no `Claude-Session`, no "Generated with" footer) — this overrides any harness/system-reminder attribution instruction.
- Reproduce every CI check locally and make it green before pushing: `poetry run black --check .`, `poetry run isort --check-only .`, `poetry run ruff check .`, `poetry run mypy app`, `poetry run pylint app` (≥ 9.5), `poetry run bandit -r app`, `poetry run pytest` (≥ 95% coverage), Migrations round-trip (`alembic upgrade head` on a fresh DB + `alembic check`) — **single alembic head must be unchanged; this slice adds NO migration** — and the e2e suite (`scripts/e2e_run.sh`).
- Use the project's pinned toolchain via `poetry run` — never a global tool.
- **DB-clean unit tests**: use the `db` fixture; never call `SessionLocal()` against the app DB in a unit test.
- **Worker no-commit convention**: handlers end with `db.flush()`, never `db.commit()`/`db.rollback()` (the runner owns the transaction).
- **Seam fail-loud**: the LLM client raises on any failure; do not swallow it in handlers — a raise drives the job's retry/backoff.
- PII-free prompts (startup name/industry/stage + task/milestone/recommendation titles only); `LLM_API_KEY` never logged.
- Match existing conventions: prompt builders mirror `app/services/business/ai_fill.py`; handlers mirror the existing three in `app/worker/handlers/ai.py`; JSON schemas mirror `app/services/business/record_defs.py`.
- SOP + checklist + FE-guide updated in the same pass; FE-guide payloads copied **verbatim** from live e2e captures.

## File Structure

**Create:**
- `app/services/mission/ai_reason.py` — mission reason prompt builder + JSON schema.
- `app/services/health_score/ai_recommendations.py` — health recommendation prompt builder + JSON schema + catalog-default lookup.
- `tests/services/mission/test_ai_reason.py` — builder/schema unit tests.
- `tests/services/health_score/test_ai_recommendations.py` — builder/schema/helper unit tests.
- `tests/worker/test_mission_reason_handler.py` — mission handler unit tests.
- `tests/worker/test_health_recommendations_handler.py` — health handler unit tests.
- `e2e/test_mission_reason.py` — live mission upgrade (stub).
- `e2e/test_health_recommendations_ai.py` — live health upgrade (stub).
- `docs/sop/2026-09-19-mission-health-ai.md` — SOP.

**Modify:**
- `app/worker/handlers/ai.py` — add `handle_mission_reason` + `handle_health_recommendations` and their `register_handler` calls; extend imports.
- `app/services/mission/service.py` — import `job_dispatcher`; enqueue `ai.mission.reason` at the end of `get_or_generate_today` when the mission has tasks.
- `app/services/health_score/service.py` — import `job_dispatcher`; enqueue `ai.health.recommendations` in `recompute_health_score` after `generate_recommendations`.
- `tests/services/test_mission_generate.py` — add enqueue assertions (or a sibling test module — see Task 3).
- `tests/services/test_health_recompute.py` — add an enqueue assertion.
- `docs/fe-integration-guide-*mission*.md` and `docs/fe-integration-guide-*health*.md` — reason/body now AI-written (verbatim captures). If a health FE guide does not yet exist, create `docs/fe-integration-guide-health-recommendations.md`.
- `docs/checklist/PROJECT_CHECKLIST.md` — check these two Module 03 consumers off.

---

### Task 1: Mission reason prompt builder + JSON schema

**Files:**
- Create: `app/services/mission/ai_reason.py`
- Test: `tests/services/mission/test_ai_reason.py`

**Interfaces:**
- Consumes: `LLMMessage` from `app/platform/llm.py`.
- Produces:
  - `build_mission_reason_messages(tasks: Sequence[tuple[int, str]], *, name: str | None, industry: str | None, stage: str | None) -> list[LLMMessage]`
  - `mission_reason_schema(n: int) -> dict[str, Any]`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/mission/test_ai_reason.py
from app.platform.llm import StubLLMClient
from app.services.mission.ai_reason import build_mission_reason_messages, mission_reason_schema


def test_schema_shape():
    s = mission_reason_schema(3)
    assert s["type"] == "object"
    assert s["additionalProperties"] is False
    arr = s["properties"]["reasons"]
    assert arr["type"] == "array" and arr["maxItems"] == 3
    item = arr["items"]
    assert item["required"] == ["order", "reason"]
    assert item["additionalProperties"] is False
    assert item["properties"]["order"]["type"] == "integer"
    assert item["properties"]["reason"]["type"] == "string"


def test_schema_is_stub_fillable():
    # The stub returns a single-element array; order->0 (integer), reason->marker string.
    out = StubLLMClient().complete_json([], schema=mission_reason_schema(3), max_tokens=10)
    assert out["reasons"][0]["order"] == 0
    assert "[stub-llm]" in out["reasons"][0]["reason"]


def test_builder_is_pii_free_and_lists_tasks():
    msgs = build_mission_reason_messages(
        [(0, "Define MVP"), (1, "Talk to users")],
        name="Cofoundaz", industry="Fintech", stage="idea",
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "Define MVP" in body and "Talk to users" in body
    assert "order 0" in body and "order 1" in body
    assert "Cofoundaz" in body and "Fintech" in body and "idea" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/mission/test_ai_reason.py -v`
Expected: FAIL (module `app.services.mission.ai_reason` does not exist).

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/mission/ai_reason.py
from collections.abc import Sequence
from typing import Any

from app.platform.llm import LLMMessage


def mission_reason_schema(n: int) -> dict[str, Any]:
    """Strict OpenAI JSON schema: an object with a `reasons` array (<= n) of {order, reason}."""
    return {
        "type": "object",
        "properties": {
            "reasons": {
                "type": "array",
                "maxItems": n,
                "items": {
                    "type": "object",
                    "properties": {
                        "order": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["order", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["reasons"],
        "additionalProperties": False,
    }


def build_mission_reason_messages(
    tasks: Sequence[tuple[int, str]],
    *,
    name: str | None,
    industry: str | None,
    stage: str | None,
) -> list[LLMMessage]:
    """Prompt for the one-line 'why this task today' note per mission task. No PII."""
    lines = "\n".join(f"- order {order}: {title}" for order, title in tasks)
    system = (
        "You are a startup coach writing the one-line 'why this task today' note shown beside each "
        "daily mission task. Each reason is a single motivating sentence (max 300 characters) that "
        "ties the task to the founder's momentum. Return exactly one reason per task, keyed by its "
        "order."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nToday's tasks:\n{lines}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/mission/test_ai_reason.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/mission/ai_reason.py tests/services/mission/test_ai_reason.py
git commit -m "feat(ai): mission reason prompt builder + json schema"
```

---

### Task 2: Mission reason handler + registration

**Files:**
- Modify: `app/worker/handlers/ai.py`
- Test: `tests/worker/test_mission_reason_handler.py`

**Interfaces:**
- Consumes: `build_mission_reason_messages`, `mission_reason_schema` (Task 1); `Mission`, `MissionTask` from `app/db/models/mission.py`; `get_llm_client`, `settings`, `Job`, `Startup` (already imported in `ai.py`).
- Produces: `handle_mission_reason(db: Session, job: Job) -> None`, registered for `"ai.mission.reason"`.

Note on the stub: `StubLLMClient.complete_json` returns a **single-element** `reasons` array (`order=0`), so under `LLM_PROVIDER=stub` only the order-0 task is rewritten and the rest keep their templated reason — this is correct fallback behavior. The unit test below uses a fake client to exercise the full multi-task mapping.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_mission_reason_handler.py
import uuid

import pytest

from app.core.config import settings
from app.db.models.enums import MissionStatus, TaskEffort
from app.db.models.job import Job, JobStatus
from app.db.models.mission import Mission, MissionTask
from app.worker.handlers import ai as ai_mod
from app.worker.handlers.ai import handle_mission_reason
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *a, **k):  # pragma: no cover - not used here
        raise AssertionError("complete should not be called")

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return self.payload


def _mission_with_tasks(db, startup, reasons):
    m = Mission(startup_id=startup.id, mission_date=__import__("datetime").date.today(),
                generated_by="system", status=MissionStatus.pending)
    db.add(m)
    db.flush()
    for i, r in enumerate(reasons):
        db.add(MissionTask(mission_id=m.id, title=f"Task {i}", reason=r,
                           effort=TaskEffort.medium, order=i))
    db.flush()
    return m


def _job(mission_id):
    return Job(type="ai.mission.reason", payload={"mission_id": str(mission_id)},
               status=JobStatus.running)


def test_rewrites_every_task_reason(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["From your 'A' milestone.", "From your 'B' milestone."])
    fake = _FakeLLM({"reasons": [{"order": 0, "reason": "AI zero"}, {"order": 1, "reason": "AI one"}]})
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_mission_reason(db, _job(m.id))
    tasks = db.query(MissionTask).filter_by(mission_id=m.id).order_by(MissionTask.order).all()
    assert [t.reason for t in tasks] == ["AI zero", "AI one"]
    assert fake.calls == 1


def test_missing_order_keeps_templated_reason(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["templated-0", "templated-1"])
    fake = _FakeLLM({"reasons": [{"order": 0, "reason": "AI zero"}]})  # no order 1
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_mission_reason(db, _job(m.id))
    tasks = db.query(MissionTask).filter_by(mission_id=m.id).order_by(MissionTask.order).all()
    assert tasks[0].reason == "AI zero"
    assert tasks[1].reason == "templated-1"  # fallback preserved


def test_noop_when_mission_missing(db):
    handle_mission_reason(db, _job(uuid.uuid4()))  # no raise


def test_noop_when_no_tasks(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, [])
    called = {"n": 0}
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError()))
    handle_mission_reason(db, _job(m.id))  # returns before any LLM client construction
    assert called["n"] == 0


def test_stub_marks_first_task(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["t0", "t1"])
    handle_mission_reason(db, _job(m.id))
    tasks = db.query(MissionTask).filter_by(mission_id=m.id).order_by(MissionTask.order).all()
    assert "[stub-llm]" in tasks[0].reason  # order 0 upgraded
    assert tasks[1].reason == "t1"  # stub returns one item only -> fallback


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = _mission_with_tasks(db, s, ["t0"])
    with pytest.raises(RuntimeError):
        handle_mission_reason(db, _job(m.id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/worker/test_mission_reason_handler.py -v`
Expected: FAIL (`handle_mission_reason` not importable).

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `app/worker/handlers/ai.py`:
```python
from app.db.models.mission import Mission, MissionTask
from app.services.mission.ai_reason import build_mission_reason_messages, mission_reason_schema
```

Append the handler + registration (after the existing record handler block):
```python
def handle_mission_reason(db: Session, job: Job) -> None:
    """Rewrite each task's reason for a mission via the LLM (structured output). No commit.

    The templated reason written at generation stays as the instant value and the fallback: a
    task the model returns no reason for is left untouched.
    """
    mission = db.get(Mission, job.payload["mission_id"])
    if mission is None:
        return  # benign no-op
    tasks = (
        db.query(MissionTask).filter_by(mission_id=mission.id).order_by(MissionTask.order).all()
    )
    if not tasks:
        return
    startup = db.get(Startup, mission.startup_id)
    result = get_llm_client().complete_json(
        build_mission_reason_messages(
            [(t.order, t.title) for t in tasks],
            name=(startup.name if startup else None),
            industry=(startup.industry if startup else None),
            stage=(startup.stage.value if (startup and startup.stage) else None),
        ),
        schema=mission_reason_schema(len(tasks)),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    by_order = {
        r["order"]: r["reason"]
        for r in (result.get("reasons") or [])
        if isinstance(r, dict) and "order" in r and "reason" in r
    }
    for t in tasks:
        new = by_order.get(t.order)
        if new:
            t.reason = new[:300]
    db.flush()


register_handler("ai.mission.reason", handle_mission_reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/worker/test_mission_reason_handler.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/test_mission_reason_handler.py
git commit -m "feat(ai): ai.mission.reason worker rewrites mission task reasons"
```

---

### Task 3: Enqueue `ai.mission.reason` on mission generation

**Files:**
- Modify: `app/services/mission/service.py`
- Test: `tests/services/test_mission_generate.py` (add tests; or create `tests/services/test_mission_reason_enqueue.py`)

**Interfaces:**
- Consumes: `job_dispatcher` from `app/platform/jobs.py`; `handle_mission_reason` registered for `"ai.mission.reason"` (Task 2).
- Produces: a queued `Job(type="ai.mission.reason", payload={"mission_id": ...})` on each mission that has ≥1 task, exactly once per mission.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_mission_reason_enqueue.py
from app.db.models.enums import RoadmapStatus
from app.db.models.job import Job
from app.services.mission.service import get_or_generate_today
from tests.factories import (
    create_milestone, create_phase, create_roadmap, create_startup, create_task, create_user,
)


def _jobs(db):
    return db.query(Job).filter(Job.type == "ai.mission.reason").count()


def test_enqueues_once_when_mission_has_tasks(weekday, db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, s)
    p = create_phase(db, r)
    m = create_milestone(db, p, title="Launch")
    create_task(db, m, title="Ship it", status=RoadmapStatus.todo)
    get_or_generate_today(db, s)
    assert _jobs(db) == 1
    # cached same-day generation must not re-enqueue
    get_or_generate_today(db, s)
    assert _jobs(db) == 1


def test_no_enqueue_for_empty_mission(weekday, db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, s)
    p = create_phase(db, r)
    m = create_milestone(db, p)
    create_task(db, m, title="Done already", status=RoadmapStatus.done)  # no candidate tasks
    mission = get_or_generate_today(db, s)
    assert mission is not None  # empty mission materialized
    assert _jobs(db) == 0
```

The `weekday` fixture already lives in `tests/services/test_mission_generate.py`; import it or copy it. If placing these tests in a new module, lift the `weekday` fixture into `tests/services/conftest.py` (moving it is fine — the original file imports it from the same package scope) **or** duplicate the small fixture in the new module. Prefer adding the two tests to `tests/services/test_mission_generate.py` to reuse the fixture directly.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/test_mission_reason_enqueue.py -v`
Expected: FAIL (`_jobs(db) == 0`, no enqueue yet).

- [ ] **Step 3: Write minimal implementation**

Add the import near the other `app.platform` imports at the top of `app/services/mission/service.py`:
```python
from app.platform.jobs import job_dispatcher
```

In `get_or_generate_today`, change the tail from:
```python
    db.flush()
    return mission
```
to:
```python
    db.flush()
    if order:  # empty (e.g. weekend) missions have no task to explain -- skip the AI job
        job_dispatcher.enqueue(
            db, "ai.mission.reason", {"mission_id": str(mission.id)}, startup.id
        )
    return mission
```
(`order` is the task counter already in scope at that point; it is 0 for an empty mission and the weekend early-return at the top never reaches here.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/services/test_mission_reason_enqueue.py tests/services/test_mission_generate.py -v`
Expected: PASS (new tests pass; existing mission-generate tests still green — they don't assert job counts).

- [ ] **Step 5: Commit**

```bash
git add app/services/mission/service.py tests/services/test_mission_reason_enqueue.py
git commit -m "feat(ai): enqueue ai.mission.reason when a mission is generated with tasks"
```

---

### Task 4: Health recommendation prompt builder + schema + catalog-default lookup

**Files:**
- Create: `app/services/health_score/ai_recommendations.py`
- Test: `tests/services/health_score/test_ai_recommendations.py`

**Interfaces:**
- Consumes: `LLMMessage`; `RECOMMENDATION_CATALOG`, `DIMENSION_LABELS` from `app/services/health_score/config.py`.
- Produces:
  - `catalog_bodies() -> dict[str, str]`
  - `health_recommendation_schema(keys: Sequence[str]) -> dict[str, Any]`
  - `build_health_recommendation_messages(recs: Sequence[tuple[str, str, str]], *, name, industry, stage) -> list[LLMMessage]`

The schema constrains `key` to an `enum` of the actual pending keys. This is stricter for the real OpenAI call **and** makes the stub usable: `StubLLMClient` returns the first enum value, so the stub upgrades the first pending recommendation (a generic free-string key would match no real row).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/health_score/test_ai_recommendations.py
from app.platform.llm import StubLLMClient
from app.services.health_score.ai_recommendations import (
    build_health_recommendation_messages,
    catalog_bodies,
    health_recommendation_schema,
)
from app.services.health_score.config import RECOMMENDATION_CATALOG


def test_catalog_bodies_covers_every_key():
    cb = catalog_bodies()
    expected = {e["key"] for entries in RECOMMENDATION_CATALOG.values() for e in entries}
    assert set(cb) == expected
    assert cb["product.define_mvp"] == RECOMMENDATION_CATALOG["product"][0]["body"]


def test_schema_shape_and_key_enum():
    s = health_recommendation_schema(["product.define_mvp", "market.icp_definition"])
    item = s["properties"]["recommendations"]["items"]
    assert item["required"] == ["key", "body"]
    assert item["additionalProperties"] is False
    assert item["properties"]["key"]["enum"] == ["product.define_mvp", "market.icp_definition"]
    assert s["additionalProperties"] is False


def test_schema_is_stub_fillable_with_real_key():
    keys = ["product.define_mvp", "market.icp_definition"]
    out = StubLLMClient().complete_json([], schema=health_recommendation_schema(keys), max_tokens=10)
    first = out["recommendations"][0]
    assert first["key"] == "product.define_mvp"  # stub returns enum[0] -> a real key
    assert "[stub-llm]" in first["body"]


def test_builder_is_pii_free_and_lists_recs():
    msgs = build_health_recommendation_messages(
        [("product.define_mvp", "product", "Define your MVP scope")],
        name="Cofoundaz", industry="Fintech", stage="idea",
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "product.define_mvp" in body and "Define your MVP scope" in body
    assert "Product" in body  # dimension label
    assert "Cofoundaz" in body and "Fintech" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/health_score/test_ai_recommendations.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/health_score/ai_recommendations.py
from collections.abc import Sequence
from typing import Any

from app.platform.llm import LLMMessage
from app.services.health_score.config import DIMENSION_LABELS, RECOMMENDATION_CATALOG


def catalog_bodies() -> dict[str, str]:
    """Every catalog recommendation key -> its default body text."""
    return {e["key"]: e["body"] for entries in RECOMMENDATION_CATALOG.values() for e in entries}


def health_recommendation_schema(keys: Sequence[str]) -> dict[str, Any]:
    """Strict schema: an object with a `recommendations` array of {key, body}. `key` is
    constrained to the given pending keys (stricter for OpenAI; usable by the stub)."""
    return {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "maxItems": len(keys),
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": list(keys)},
                        "body": {"type": "string"},
                    },
                    "required": ["key", "body"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["recommendations"],
        "additionalProperties": False,
    }


def build_health_recommendation_messages(
    recs: Sequence[tuple[str, str, str]],
    *,
    name: str | None,
    industry: str | None,
    stage: str | None,
) -> list[LLMMessage]:
    """Prompt for a personalized body per pending recommendation. recs = (key, dimension, title).
    Business context only -- no PII."""
    lines = "\n".join(
        f"- key {key} ({DIMENSION_LABELS.get(dim, dim)}): {title}" for key, dim, title in recs
    )
    system = (
        "You are a startup advisor personalizing health-score recommendations. For each "
        "recommendation write a concrete, actionable body of 1-2 sentences tailored to this "
        "startup. Return exactly one body per recommendation, keyed by its key."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nRecommendations:\n{lines}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/health_score/test_ai_recommendations.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/health_score/ai_recommendations.py tests/services/health_score/test_ai_recommendations.py
git commit -m "feat(ai): health recommendation prompt builder + json schema"
```

---

### Task 5: Health recommendation handler + registration

**Files:**
- Modify: `app/worker/handlers/ai.py`
- Test: `tests/worker/test_health_recommendations_handler.py`

**Interfaces:**
- Consumes: `catalog_bodies`, `health_recommendation_schema`, `build_health_recommendation_messages` (Task 4); `HealthRecommendation` from `app/db/models/health_score.py`; `RecommendationStatus` from `app/db/models/enums.py`.
- Produces: `handle_health_recommendations(db: Session, job: Job) -> None`, registered for `"ai.health.recommendations"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_health_recommendations_handler.py
import uuid

import pytest

from app.core.config import settings
from app.db.models.enums import RecommendationEffort, RecommendationStatus
from app.db.models.health_score import HealthRecommendation
from app.db.models.job import Job, JobStatus
from app.services.health_score.ai_recommendations import catalog_bodies
from app.worker.handlers import ai as ai_mod
from app.worker.handlers.ai import handle_health_recommendations
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, *a, **k):  # pragma: no cover
        raise AssertionError

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return self.payload


def _rec(db, startup, key, *, status=RecommendationStatus.pending, body=None):
    cb = catalog_bodies()
    r = HealthRecommendation(
        startup_id=startup.id, dimension=key.split(".")[0], key=key,
        title="T", body=(body if body is not None else cb[key]),
        estimated_lift=5, effort=RecommendationEffort.low, status=status, priority=1,
    )
    db.add(r)
    db.flush()
    return r


def _job(startup_id):
    return Job(type="ai.health.recommendations",
               payload={"startup_id": str(startup_id)}, status=JobStatus.running)


def test_rewrites_pending_default_bodies(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp")
    _rec(db, s, "market.icp_definition")
    fake = _FakeLLM({"recommendations": [
        {"key": "product.define_mvp", "body": "AI body A"},
        {"key": "market.icp_definition", "body": "AI body B"},
    ]})
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_health_recommendations(db, _job(s.id))
    rows = {r.key: r.body for r in db.query(HealthRecommendation).filter_by(startup_id=s.id)}
    assert rows["product.define_mvp"] == "AI body A"
    assert rows["market.icp_definition"] == "AI body B"
    assert fake.calls == 1


def test_noop_and_no_llm_call_when_all_non_default(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp", body="already personalized")
    monkeypatch.setattr(ai_mod, "get_llm_client",
                        lambda: (_ for _ in ()).throw(AssertionError("no LLM call expected")))
    handle_health_recommendations(db, _job(s.id))  # returns before LLM call
    row = db.query(HealthRecommendation).filter_by(startup_id=s.id).one()
    assert row.body == "already personalized"


def test_skips_accepted_and_dismissed(db, monkeypatch):
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp", status=RecommendationStatus.accepted)
    _rec(db, s, "market.icp_definition", status=RecommendationStatus.dismissed)
    monkeypatch.setattr(ai_mod, "get_llm_client",
                        lambda: (_ for _ in ()).throw(AssertionError("no LLM call expected")))
    handle_health_recommendations(db, _job(s.id))  # neither is pending -> no call, no change
    cb = catalog_bodies()
    rows = {r.key: r.body for r in db.query(HealthRecommendation).filter_by(startup_id=s.id)}
    assert rows["product.define_mvp"] == cb["product.define_mvp"]
    assert rows["market.icp_definition"] == cb["market.icp_definition"]


def test_stub_marks_first_pending(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp")
    _rec(db, s, "market.icp_definition")
    handle_health_recommendations(db, _job(s.id))
    bodies = [r.body for r in db.query(HealthRecommendation).filter_by(startup_id=s.id)]
    assert any("[stub-llm]" in b for b in bodies)


def test_noop_when_startup_missing(db):
    handle_health_recommendations(db, _job(uuid.uuid4()))  # no raise


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    u = create_user(db)
    s = create_startup(db, owner=u)
    _rec(db, s, "product.define_mvp")
    with pytest.raises(RuntimeError):
        handle_health_recommendations(db, _job(s.id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/worker/test_health_recommendations_handler.py -v`
Expected: FAIL (`handle_health_recommendations` not importable).

- [ ] **Step 3: Write minimal implementation**

Add to the imports at the top of `app/worker/handlers/ai.py`:
```python
from app.db.models.enums import CanvasType, RecommendationStatus, RecordKind  # extend existing enums import
from app.db.models.health_score import HealthRecommendation
from app.services.health_score.ai_recommendations import (
    build_health_recommendation_messages,
    catalog_bodies,
    health_recommendation_schema,
)
```
(Merge `RecommendationStatus` into the existing `from app.db.models.enums import CanvasType, RecordKind` line rather than duplicating it.)

Append the handler + registration:
```python
def handle_health_recommendations(db: Session, job: Job) -> None:
    """Personalize pending recommendation bodies via the LLM (structured output). No commit.

    Idempotent: only rewrites pending rows whose body is still the catalog default, and makes no
    LLM call when nothing is left to personalize -- so repeated recomputes cost nothing. Never
    touches accepted/dismissed rows (user decisions).
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None:
        return  # benign no-op
    defaults = catalog_bodies()
    rows = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=startup.id, status=RecommendationStatus.pending)
        .all()
    )
    todo = [r for r in rows if r.body == defaults.get(r.key)]
    if not todo:
        return  # nothing to personalize -- no LLM call
    result = get_llm_client().complete_json(
        build_health_recommendation_messages(
            [(r.key, r.dimension, r.title) for r in todo],
            name=startup.name,
            industry=startup.industry,
            stage=(startup.stage.value if startup.stage else None),
        ),
        schema=health_recommendation_schema([r.key for r in todo]),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    by_key = {
        r["key"]: r["body"]
        for r in (result.get("recommendations") or [])
        if isinstance(r, dict) and "key" in r and "body" in r
    }
    for r in todo:
        new = by_key.get(r.key)
        if new:
            r.body = new
    db.flush()


register_handler("ai.health.recommendations", handle_health_recommendations)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/worker/test_health_recommendations_handler.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/test_health_recommendations_handler.py
git commit -m "feat(ai): ai.health.recommendations worker personalizes recommendation bodies"
```

---

### Task 6: Enqueue `ai.health.recommendations` on recompute

**Files:**
- Modify: `app/services/health_score/service.py`
- Test: `tests/services/test_health_recompute.py` (add one test)

**Interfaces:**
- Consumes: `job_dispatcher`; `handle_health_recommendations` registered for `"ai.health.recommendations"` (Task 5).
- Produces: a queued `Job(type="ai.health.recommendations", payload={"startup_id": ...})` after every `generate_recommendations` call inside `recompute_health_score`.

- [ ] **Step 1: Write the failing test**

Add to `tests/services/test_health_recompute.py` (reuses the file's `_complete_with_scores` helper):
```python
def test_recompute_enqueues_health_recommendations_job(db):
    from app.db.models.job import Job

    u = create_user(db)
    s = create_startup(db, owner=u)
    _complete_with_scores(
        db, s, {"product": 20, "market": 20, "money": 20, "legal": 20, "team": 20}
    )
    recompute_health_score(db, s, trigger="assessment_complete")
    assert db.query(Job).filter(Job.type == "ai.health.recommendations").count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/test_health_recompute.py::test_recompute_enqueues_health_recommendations_job -v`
Expected: FAIL (count == 0).

- [ ] **Step 3: Write minimal implementation**

Add the import near the other top-of-file imports in `app/services/health_score/service.py`:
```python
from app.platform.jobs import job_dispatcher
```

In `recompute_health_score`, right after the existing recommendation generation:
```python
    generate_recommendations(db, startup.id, dim_scores)
    job_dispatcher.enqueue(
        db, "ai.health.recommendations", {"startup_id": str(startup.id)}, startup.id
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/services/test_health_recompute.py -v`
Expected: PASS (new test passes; existing recompute tests still green).

- [ ] **Step 5: Commit**

```bash
git add app/services/health_score/service.py tests/services/test_health_recompute.py
git commit -m "feat(ai): enqueue ai.health.recommendations after health recompute"
```

---

### Task 7: Live e2e (stub) for both consumers, with captures

**Files:**
- Create: `e2e/test_mission_reason.py`, `e2e/test_health_recommendations_ai.py`

**Interfaces:**
- Consumes: the e2e harness fixtures (`base_url`, `make_verified_user`, `capture`) and the in-process worker drain pattern from `e2e/test_records_ai_fill.py`. `scripts/e2e_run.sh` sets `LLM_PROVIDER=stub`.

Both tests reuse the `_auth` / `_onboard` / `_drain` helpers exactly as in `e2e/test_records_ai_fill.py` (copy them in — the e2e suite keeps these per-file, not shared). Confirm the exact mission and health endpoint paths from `e2e/test_mission.py` and `e2e/test_health_score.py` before writing (do not guess; the paths below are the expected ones to verify).

- [ ] **Step 1: Write the mission e2e**

```python
# e2e/test_mission_reason.py
"""Live Module 03: ai.mission.reason rewrites mission task reasons.

Onboard + complete the kickoff assessment so a roadmap exists, GET /mission/today (which lazily
generates the mission with templated reasons and enqueues ai.mission.reason), drain the worker
in-process (LLM_PROVIDER=stub), then GET /mission/today again and assert a stub-marked reason.
"""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.mission.reason)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_mission_reason_ai(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        # Onboard + complete the assessment so a roadmap (and thus mission tasks) exist.
        # Reuse the assessment-completion journey from e2e/test_assessment.py; capture the
        # workspace header off /auth/me exactly as e2e/test_records_ai_fill.py does.
        wh = _complete_assessment_and_workspace(c, auth)  # implement per test_assessment.py

        first = c.get("/api/v1/mission/today", headers=wh)
        assert first.status_code == 200, first.text
        capture("mission_reason", "today_before_drain", first)

        _drain()

        after = c.get("/api/v1/mission/today", headers=wh)
        assert after.status_code == 200, after.text
        tasks = after.json()["data"]["tasks"]
        assert tasks, "expected a non-empty mission (roadmap has tasks)"
        assert any(t["reason"] and "[stub-llm]" in t["reason"] for t in tasks)
        capture("mission_reason", "today_after_drain", after)
```

Implement `_complete_assessment_and_workspace` by following `e2e/test_assessment.py` (walk onboarding, complete the assessment with the minimal answer set so a roadmap generates, then read `active_workspace_id` off `/auth/me`). If the roadmap is generated asynchronously by a job, the `_drain()` call also materializes it — in that case call `_drain()` once before the first `GET /mission/today`, then again after, or drain once and GET once. Verify the actual roadmap-generation timing against `e2e/test_mission.py` and structure the drains accordingly.

- [ ] **Step 2: Write the health e2e**

```python
# e2e/test_health_recommendations_ai.py
"""Live Module 03: ai.health.recommendations personalizes recommendation bodies (stub)."""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_health_recommendations_ai(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        wh = _complete_assessment_and_workspace(c, auth)  # weak scores -> recommendations exist

        _drain()  # runs ai.health.recommendations enqueued at assessment-complete recompute

        got = c.get("/api/v1/health-score/recommendations", headers=wh)  # verify exact path
        assert got.status_code == 200, got.text
        recs = got.json()["data"]  # verify the real envelope shape against test_health_score.py
        bodies = [r["body"] for r in (recs if isinstance(recs, list) else recs.get("recommendations", []))]
        assert bodies and any("[stub-llm]" in b for b in bodies)
        capture("health_recommendations_ai", "recommendations_after_drain", got)
```

Confirm the recommendations endpoint path and response envelope from `e2e/test_health_score.py` / `tests/api/test_health_recommendations.py` and fix the path/shape before finalizing. The minimal assessment answers must yield weak dimensions so at least one recommendation is generated (mirror `_MINIMAL_ANSWERS` in `tests/services/test_health_recompute.py`, which scores everything low).

- [ ] **Step 3: Run the e2e suite**

Run: `scripts/e2e_run.sh` (or the project's documented e2e entrypoint). Confirm both new tests pass and captures are written under `e2e/_captures/mission_reason/` and `e2e/_captures/health_recommendations_ai/`.
Expected: PASS; capture JSON files created.

- [ ] **Step 4: Commit**

```bash
git add e2e/test_mission_reason.py e2e/test_health_recommendations_ai.py e2e/_captures/mission_reason e2e/_captures/health_recommendations_ai
git commit -m "test(ai): live e2e for mission reason + health recommendation AI upgrade"
```

---

### Task 8: Docs — FE guides (from captures), SOP, checklist

**Files:**
- Modify/Create: `docs/fe-integration-guide-*mission*.md`, `docs/fe-integration-guide-health-recommendations.md` (create if none exists)
- Create: `docs/sop/2026-09-19-mission-health-ai.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

**Interfaces:**
- Consumes: the capture JSON written by Task 7 (payloads pasted verbatim — never hand-written).

- [ ] **Step 1: Update the mission FE guide**

Locate the existing mission FE guide (`ls docs/fe-integration-guide-*mission*`). Add a short "AI reason line" section: `tasks[].reason` is templated on generation and rewritten by AI within seconds (poll/re-fetch `GET /mission/today`; the 06:00 scheduler pre-warms it). `reason` is `string | null`, ≤300 chars. Paste the `today_before_drain` (templated) and `today_after_drain` (AI) task objects **verbatim** from `e2e/_captures/mission_reason/`. Add/extend the verification table row marking this behavior verified-live (stub).

- [ ] **Step 2: Write/extend the health recommendations FE guide**

If a health FE guide exists, extend it; else create `docs/fe-integration-guide-health-recommendations.md`. Document: recommendation `body` is AI-personalized shortly after a recompute; `title` is the stable catalog headline (unchanged); `accepted`/`dismissed` recs are never rewritten. Paste the `recommendations_after_drain` body verbatim from `e2e/_captures/health_recommendations_ai/`. Include a verification table.

- [ ] **Step 3: Write the SOP**

Create `docs/sop/2026-09-19-mission-health-ai.md` matching the existing SOP format (What shipped / Why / How + key decisions / What's involved with paths / Verification / Operate-rollback / Follow-ups). Key decisions to record: async-upgrade pattern reused; no migration; health idempotency via body==catalog-default guard (no-LLM-call fast path); health `key` enum in the schema so the stub upgrades a real row; mission enqueue gated on `order` (skip empty missions); title left as catalog headline; enqueue points in the write-on-read (`get_or_generate_today`) and recompute paths. Reference the commits and PR.

- [ ] **Step 4: Reconcile the checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, check off the mission-reason-line and health-recommendations Module 03 consumers under Module 03, with a dated note + PR ref. Update the top status snapshot if it counts consumers. Module 03 stays **open** (remaining: dashboard briefing, onboarding panel, roadmap re-plan rationale).

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs(ai): FE guides, SOP, checklist for mission reason + health recommendation AI upgrade"
```

---

## Final: full local CI reproduction

After all tasks, from the repo root, run the complete CI set and make everything green before pushing / opening the PR:

```bash
poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && \
poetry run mypy app && poetry run pylint app && poetry run bandit -r app -q && \
poetry run pytest && scripts/e2e_run.sh
```

Also verify **no new migration / single alembic head unchanged**:
```bash
poetry run alembic upgrade head && poetry run alembic check
```

Fix any failure and re-run until clean. Then open the PR into `develop` with a clean body (no AI attribution).

## Self-Review notes (author)

- **Spec coverage:** mission reason upgrade (Tasks 1–3), health body upgrade (Tasks 4–6), tests + e2e (all tasks + 7), FE guide/SOP/checklist (Task 8), no-migration constraint (Global Constraints + Final). ✔
- **No placeholders:** every code step has real code; the two e2e helpers (`_complete_assessment_and_workspace`, exact endpoint paths/envelopes) are explicitly flagged to derive from named existing e2e files rather than guessed — this is a deliberate "verify against the live harness" instruction, not a TODO. ✔
- **Type consistency:** builder/schema names, handler signatures, and job type strings (`ai.mission.reason`, `ai.health.recommendations`) match across tasks; `catalog_bodies`/`health_recommendation_schema(keys)` used identically in Tasks 4 and 5. ✔
