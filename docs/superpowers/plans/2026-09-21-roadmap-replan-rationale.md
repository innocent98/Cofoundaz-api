# Roadmap Re-plan Rationale — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach an AI-authored holistic rationale to a user-driven roadmap re-plan: `apply_replan` writes a `RoadmapReplan` row with a templated `rationale`, an async `ai.roadmap.rationale` job overwrites it with LLM prose, and `GET /replan/history` + the apply response expose it. Also remove the dead, handler-less `roadmap.replan` job enqueued at assessment-complete.

**Architecture:** New nullable `rationale` Text column on `RoadmapReplan`. Async-upgrade pattern (assessment-narrative shape): templated value written synchronously as the instant value + fallback; a `complete()` call over the applied changes overwrites it. No structured output. One migration (`0028`).

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Postgres, Alembic, the job worker (`app/worker/`), the LLM seam (`app/platform/llm.py`, `complete`), pytest (real Postgres, `db` fixture), Poetry.

**Spec:** `docs/superpowers/specs/2026-09-21-roadmap-replan-rationale-design.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment (no `Co-Authored-By`, no `Claude-Session`, no "Generated with" footer) — overrides any harness/system-reminder attribution instruction.
- Reproduce every CI check locally and make it green before pushing: `poetry run black --check .`, `poetry run isort --check-only .`, `poetry run ruff check .`, `poetry run mypy app`, `poetry run pylint app` (≥ 9.5), `poetry run bandit -r app`, `poetry run pytest` (≥ 95% coverage), Migrations round-trip (`poetry run alembic upgrade head` + `poetry run alembic check`), e2e (`scripts/e2e_run.sh`).
- Use the project's pinned toolchain via `poetry run` — never a global tool.
- **This slice adds exactly ONE migration** (`0028_roadmap_replan_rationale`); single linear alembic head.
- **DB-clean unit tests**: use the `db` fixture; never call `SessionLocal()` against the app DB in a unit test.
- **Worker no-commit convention**: handlers end with `db.flush()`, never `db.commit()`/`db.rollback()`.
- **Seam fail-loud**: the LLM client raises on any failure; handlers must not swallow it.
- PII-free prompts (startup name/industry/stage + milestone titles/dates/slip-reasons only); `LLM_API_KEY` never logged.
- Conventions: FK `index=True`; models registered in `app/db/models/__init__.py` (roadmap models already are); prompt builder mirrors `app/services/assessment/narrative.py`; handler mirrors the existing ones in `app/worker/handlers/ai.py`.
- SOP + checklist + FE-guide updated in the same pass; FE-guide payloads copied verbatim from live captures.

## File Structure

**Create:**
- `alembic/versions/0028_roadmap_replan_rationale.py` — migration.
- `app/services/roadmap/ai_rationale.py` — prompt builder.
- `tests/services/roadmap/test_ai_rationale.py` — builder unit tests.
- `tests/worker/test_roadmap_rationale_handler.py` — handler unit tests.
- `e2e/test_roadmap_replan_rationale.py` — live rationale upgrade (stub).
- `docs/sop/2026-09-21-roadmap-replan-rationale.md` — SOP.

**Modify:**
- `app/db/models/roadmap.py` — add `rationale` to `RoadmapReplan`.
- `app/services/roadmap/replan.py` — `_templated_rationale`, set `rationale`, enqueue `ai.roadmap.rationale`, add `rationale` to the returned dict.
- `app/api/v1/endpoints/roadmap.py` — add `rationale` to the `replan_history` serialization.
- `app/worker/handlers/ai.py` — `handle_roadmap_rationale` + registration.
- `app/services/assessment/service.py` — remove the dead `roadmap.replan` enqueue.
- `tests/services/assessment/test_complete_concurrency.py`, `tests/api/assessment/test_complete.py` — drop the `roadmap.replan` assertions.
- `docs/fe-integration-guide-roadmap.md` — §9 `rationale` field (verbatim captures).
- `docs/checklist/PROJECT_CHECKLIST.md` — check off the roadmap-rationale consumer.
- `tests/services/test_roadmap_replan_apply.py` — extend for the enqueue + rationale (Task 3).

---

### Task 1: `rationale` column on `RoadmapReplan` + migration

**Files:**
- Modify: `app/db/models/roadmap.py`
- Create: `alembic/versions/0028_roadmap_replan_rationale.py`
- Test: `tests/db/test_roadmap_models.py` (add one test; the file already exists)

**Interfaces:**
- Produces: `RoadmapReplan.rationale: Mapped[str | None]` (Text, nullable); migration `0028_roadmap_replan_rationale` (down_revision `0027_daily_briefings`).

- [ ] **Step 1: Add the column**

In `app/db/models/roadmap.py`, in `class RoadmapReplan`, after `summary`:
```python
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
```
(`Text` is already imported in this module — confirm; if not, add it to the `sqlalchemy` import.)

- [ ] **Step 2: Autogenerate the migration**

Run: `poetry run alembic revision --autogenerate -m "roadmap_replan_rationale"`
Rename the generated file to `alembic/versions/0028_roadmap_replan_rationale.py` and set
`revision = "0028_roadmap_replan_rationale"`, `down_revision = "0027_daily_briefings"`. It should be a
single `op.add_column("roadmap_replans", sa.Column("rationale", sa.Text(), nullable=True))` with a
matching `op.drop_column` downgrade. Edit only the filename + revision ids; keep autogenerate's ops.

- [ ] **Step 3: Verify round-trip + drift + single head**

Run: `poetry run alembic upgrade head` then `poetry run alembic check` (→ "No new upgrade operations detected.") and `poetry run alembic heads` (→ single head `0028_roadmap_replan_rationale`).

- [ ] **Step 4: Write the model test**

Add to `tests/db/test_roadmap_models.py`:
```python
def test_roadmap_replan_rationale_nullable(db):
    from app.db.models.roadmap import RoadmapReplan
    from tests.factories import create_roadmap, create_startup, create_user

    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, s)
    replan = RoadmapReplan(
        roadmap_id=r.id, applied_by=u.id, change_count=1, changes=[], summary="Re-planned 1 milestone",
    )
    db.add(replan)
    db.flush()
    assert replan.rationale is None  # nullable, defaults to NULL
    replan.rationale = "because dates slipped"
    db.flush()
    got = db.query(RoadmapReplan).filter_by(id=replan.id).one()
    assert got.rationale == "because dates slipped"
```
(Confirm `create_roadmap`/`create_startup`/`create_user` factory signatures — see `tests/factories.py`.)

- [ ] **Step 5: Run tests + lint**

Run: `poetry run pytest tests/db/test_roadmap_models.py -v` (PASS) then `poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && poetry run mypy app` clean.

- [ ] **Step 6: Commit**

```bash
git add app/db/models/roadmap.py alembic/versions/0028_roadmap_replan_rationale.py tests/db/test_roadmap_models.py
git commit -m "feat(roadmap): rationale column on roadmap_replans + migration"
```

---

### Task 2: Roadmap rationale prompt builder

**Files:**
- Create: `app/services/roadmap/ai_rationale.py`
- Test: `tests/services/roadmap/test_ai_rationale.py`

**Interfaces:**
- Consumes: `LLMMessage` from `app/platform/llm.py`.
- Produces: `build_roadmap_rationale_messages(*, stage: str | None, name: str | None, industry: str | None, changes: list[dict]) -> list[LLMMessage]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/roadmap/test_ai_rationale.py
from app.services.roadmap.ai_rationale import build_roadmap_rationale_messages


def test_builder_is_pii_free_and_lists_changes():
    changes = [
        {"milestone_id": "x", "title": "Validate demand", "old_due": "2026-08-17",
         "new_due": "2026-09-03", "reason": "10 days overdue and not yet done."},
    ]
    msgs = build_roadmap_rationale_messages(
        stage="idea", name="Cofoundaz", industry="Fintech", changes=changes,
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "Validate demand" in body
    assert "2026-08-17" in body and "2026-09-03" in body
    assert "Cofoundaz" in body and "Fintech" in body and "idea" in body


def test_builder_handles_empty_changes():
    msgs = build_roadmap_rationale_messages(stage=None, name=None, industry=None, changes=[])
    assert len(msgs) == 2  # no crash on empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/roadmap/test_ai_rationale.py -v` → FAIL (module missing). Create `tests/services/roadmap/__init__.py` if the project's test packages use one (check `tests/services/dashboard/`).

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/roadmap/ai_rationale.py
from app.platform.llm import LLMMessage


def build_roadmap_rationale_messages(
    *,
    stage: str | None,
    name: str | None,
    industry: str | None,
    changes: list[dict],
) -> list[LLMMessage]:
    """Prompt for a holistic 'why we re-planned' rationale. Business context only -- no PII."""
    lines = "\n".join(
        f"- {c.get('title', 'a milestone')}: {c.get('old_due', '?')} -> {c.get('new_due', '?')} "
        f"({c.get('reason', '')})"
        for c in changes
    )
    system = (
        "You are a startup coach explaining a roadmap re-plan to a founder. Write a short (2-3 "
        "sentence) reassuring but honest rationale: why these milestone dates moved and what it "
        "means for momentum. No preamble, no headings, no bullet list."
    )
    user = (
        f"Startup: {name or 'unnamed'}. Industry: {industry or 'unspecified'}. "
        f"Stage: {stage or 'unspecified'}.\nShifted milestones:\n{lines or '(none)'}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/roadmap/test_ai_rationale.py -v` → PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/roadmap/ai_rationale.py tests/services/roadmap/test_ai_rationale.py
git commit -m "feat(roadmap): re-plan rationale prompt builder"
```

---

### Task 3: `apply_replan` writes templated rationale + enqueues job; expose `rationale`

**Files:**
- Modify: `app/services/roadmap/replan.py`, `app/api/v1/endpoints/roadmap.py`
- Test: `tests/services/test_roadmap_replan_apply.py` (extend)

**Interfaces:**
- Consumes: `job_dispatcher` (`app/platform/jobs.py`); `RoadmapReplan` (already used in this file).
- Produces: `_templated_rationale(summary: str, snapshot: list[dict]) -> str`; `apply_replan` now sets `rationale`, enqueues `ai.roadmap.rationale {replan_id}`, and returns `rationale` in its dict; `replan_history` serialization includes `rationale`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_roadmap_replan_apply.py` (reuse the file's existing drift-setup helpers; inspect how it builds a roadmap with a slipped milestone and calls `apply_replan`):
```python
def test_apply_writes_rationale_and_enqueues(db):
    # ... build a roadmap with one drifted milestone (mirror the file's existing apply test setup) ...
    from app.db.models.job import Job
    from app.db.models.roadmap import RoadmapReplan

    result = apply_replan(db, roadmap, actor, [change_id])  # the one drifted change_id
    assert result["applied"]
    assert result["rationale"]  # non-null templated fallback in the response
    replan = db.query(RoadmapReplan).filter_by(roadmap_id=roadmap.id).one()
    assert replan.rationale  # persisted, non-null
    assert db.query(Job).filter(Job.type == "ai.roadmap.rationale").count() == 1


def test_apply_empty_enqueues_nothing(db):
    from app.db.models.job import Job
    # apply with a change_id that isn't in the proposal (all-stale) -> no row, no job
    result = apply_replan(db, roadmap, actor, [uuid.uuid4()])
    assert result["applied"] == []
    assert result["replan_id"] is None
    assert db.query(Job).filter(Job.type == "ai.roadmap.rationale").count() == 0
```
Fill the setup exactly as the existing apply test in this file does (do not invent — reuse its helpers/fixtures).

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/services/test_roadmap_replan_apply.py -v` → FAIL (`rationale` missing / no job).

- [ ] **Step 3: Implement**

Add the import at the top of `app/services/roadmap/replan.py`:
```python
from app.platform.jobs import job_dispatcher
```

Add the helper (near the top-level functions):
```python
def _templated_rationale(summary: str, snapshot: list[dict]) -> str:
    """Non-null instant fallback shown until the AI job overwrites it."""
    if not snapshot:
        return summary
    titles = ", ".join(c["title"] for c in snapshot[:3])
    more = "" if len(snapshot) <= 3 else f", and {len(snapshot) - 3} more"
    return (
        f"{summary}: adjusted the dates for {titles}{more} to keep your roadmap realistic "
        "after recent slips."
    )
```

In `apply_replan`, change the `RoadmapReplan(...)` construction + the block after it:
```python
        replan = RoadmapReplan(
            roadmap_id=roadmap.id,
            applied_by=actor.id,
            change_count=n,
            changes=snapshot,
            summary=summary,
            rationale=_templated_rationale(summary, snapshot),
        )
        db.add(replan)
        db.flush()
        replan_id = str(replan.id)
        job_dispatcher.enqueue(
            db, "ai.roadmap.rationale", {"replan_id": replan_id}, roadmap.startup_id
        )
        event_bus.publish(  # unchanged
            ...
        )
```
And add `rationale` to the returned dict:
```python
    return {
        "applied": applied,
        "skipped": skipped,
        "replan_id": replan_id,
        "summary": summary,
        "rationale": replan.rationale if applied else None,
    }
```
(Bind `replan` only inside the `if applied:` branch — reference it in the return via a local you set to
`None` up top, e.g. `rationale: str | None = None` set inside the branch, to satisfy mypy. Mirror the
existing `replan_id`/`summary` Optional handling in this function.)

In `app/api/v1/endpoints/roadmap.py`, in `replan_history`'s serialization dict, add:
```python
                "rationale": r.rationale,
```
(next to `"summary": r.summary,`).

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/services/test_roadmap_replan_apply.py tests/api/test_roadmap.py -q` → all pass (new + existing). Then `poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && poetry run mypy app` clean.

- [ ] **Step 5: Commit**

```bash
git add app/services/roadmap/replan.py app/api/v1/endpoints/roadmap.py tests/services/test_roadmap_replan_apply.py
git commit -m "feat(roadmap): apply_replan writes templated rationale + enqueues ai.roadmap.rationale"
```

---

### Task 4: Worker handler `handle_roadmap_rationale`

**Files:**
- Modify: `app/worker/handlers/ai.py`
- Test: `tests/worker/test_roadmap_rationale_handler.py`

**Interfaces:**
- Consumes: `build_roadmap_rationale_messages` (Task 2); `RoadmapReplan`, `Roadmap` (`app/db/models/roadmap.py`); `get_llm_client`, `settings`, `Job`, `Startup` (already imported in `ai.py`).
- Produces: `handle_roadmap_rationale(db, job) -> None`, registered for `"ai.roadmap.rationale"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_roadmap_rationale_handler.py
import uuid

import pytest

from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.db.models.roadmap import RoadmapReplan
from app.worker.handlers import ai as ai_mod
from app.worker.handlers.ai import handle_roadmap_rationale
from tests.factories import create_roadmap, create_startup, create_user


class _FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, *a, **k):  # pragma: no cover
        raise AssertionError


def _replan(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    r = create_roadmap(db, s)
    replan = RoadmapReplan(
        roadmap_id=r.id, applied_by=u.id, change_count=1,
        changes=[{"title": "Validate demand", "old_due": "2026-08-17",
                  "new_due": "2026-09-03", "reason": "10 days overdue and not yet done."}],
        summary="Re-planned 1 milestone", rationale="templated fallback",
    )
    db.add(replan)
    db.flush()
    return replan


def _job(replan_id):
    return Job(type="ai.roadmap.rationale", payload={"replan_id": str(replan_id)},
               status=JobStatus.running)


def test_overwrites_rationale(db, monkeypatch):
    replan = _replan(db)
    fake = _FakeLLM("Your roadmap shifted because demand validation slipped.")
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_roadmap_rationale(db, _job(replan.id))
    db.refresh(replan)
    assert replan.rationale == "Your roadmap shifted because demand validation slipped."
    assert fake.calls == 1


def test_noop_when_replan_missing(db):
    handle_roadmap_rationale(db, _job(uuid.uuid4()))  # no raise


def test_stub_marks_rationale(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    replan = _replan(db)
    handle_roadmap_rationale(db, _job(replan.id))
    db.refresh(replan)
    assert "[stub-llm]" in replan.rationale


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    replan = _replan(db)
    with pytest.raises(RuntimeError):
        handle_roadmap_rationale(db, _job(replan.id))
```
Note: `StubLLMClient.complete` returns `"[stub-llm] AI-generated assessment narrative."` (a fixed
string containing `[stub-llm]`), so `test_stub_marks_rationale` passes on the substring.

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/worker/test_roadmap_rationale_handler.py -v` → FAIL (`handle_roadmap_rationale` not importable).

- [ ] **Step 3: Implement**

Add imports at the top of `app/worker/handlers/ai.py`:
```python
from app.db.models.roadmap import Roadmap, RoadmapReplan
from app.services.roadmap.ai_rationale import build_roadmap_rationale_messages
```

Append the handler + registration:
```python
def handle_roadmap_rationale(db: Session, job: Job) -> None:
    """Overwrite a roadmap re-plan's rationale with the LLM (prose). No commit.

    The templated rationale written at apply time stays as the instant value and fallback.
    """
    replan = db.get(RoadmapReplan, job.payload["replan_id"])
    if replan is None:
        return  # benign no-op
    roadmap = db.get(Roadmap, replan.roadmap_id)
    startup = db.get(Startup, roadmap.startup_id) if roadmap else None
    messages = build_roadmap_rationale_messages(
        stage=(startup.stage.value if (startup and startup.stage) else None),
        name=(startup.name if startup else None),
        industry=(startup.industry if startup else None),
        changes=list(replan.changes or []),
    )
    text = get_llm_client().complete(messages, max_tokens=settings.LLM_MAX_TOKENS)
    replan.rationale = text.strip()
    db.flush()


register_handler("ai.roadmap.rationale", handle_roadmap_rationale)
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/worker/test_roadmap_rationale_handler.py -v` → PASS (4). Then `poetry run pytest tests/worker/ -q` (existing handlers green) and `poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && poetry run mypy app` clean.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/test_roadmap_rationale_handler.py
git commit -m "feat(roadmap): ai.roadmap.rationale worker overwrites re-plan rationale"
```

---

### Task 5: Remove the dead `roadmap.replan` job enqueue

**Files:**
- Modify: `app/services/assessment/service.py`, `tests/services/assessment/test_complete_concurrency.py`, `tests/api/assessment/test_complete.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: assessment completion no longer enqueues `roadmap.replan`; the two tests' expectations updated.

- [ ] **Step 1: Update the tests first (they encode the current behavior)**

In `tests/api/assessment/test_complete.py` (around lines 89–98), remove the `roadmap.replan` assertions and its payload check:
```python
    # (delete) assert "roadmap.replan" in types
    # (delete) the `for j in jobs: if j.type == "roadmap.replan": ...` block
```
Keep `assert "healthscore.recalculate" not in types`. Update the comment above it that says
"roadmap.replan is still enqueued for Module 05 to consume" — replace with a note that the dead
`roadmap.replan` job was removed (no handler ever consumed it). If you want positive coverage, assert
the completion job set is exactly `{"ai.assessment.narrative", "ai.health.recommendations"}` for a
newly-flushed assessment (use the same `db.query(Job).filter(status==queued)` set).

In `tests/services/assessment/test_complete_concurrency.py` (around line 130–136), drop
`"roadmap.replan"` from the expected sorted list:
```python
            assert types == [
                "ai.assessment.narrative",
                "ai.health.recommendations",
            ], f"expected exactly one of each completion job (not one per racer), got {types}"
```
Also update the module docstring lines that mention enqueuing "the roadmap.replan job EXACTLY ONCE"
and "two roadmap.replan jobs" (lines ~13, ~17) to reflect that the completion jobs are now the
narrative + health-recommendations jobs (the concurrency invariant is unchanged — still exactly one of
each, not one per racer).

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/api/assessment/test_complete.py tests/services/assessment/test_complete_concurrency.py -q` → FAIL (behavior still enqueues `roadmap.replan`).

- [ ] **Step 3: Remove the enqueue**

In `app/services/assessment/service.py`, delete the line:
```python
    job_dispatcher.enqueue(db, "roadmap.replan", job_payload, startup.id)
```
Leave the `ai.assessment.narrative` enqueue and the `job_payload` (still used by it). Confirm
`job_payload` and `job_dispatcher` remain referenced (they do, by the narrative enqueue).

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/api/assessment/ tests/services/assessment/ -q` → all pass. Then `poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && poetry run mypy app` clean.

- [ ] **Step 5: Commit**

```bash
git add app/services/assessment/service.py tests/services/assessment/test_complete_concurrency.py tests/api/assessment/test_complete.py
git commit -m "chore(assessment): remove dead roadmap.replan job enqueue (no handler)"
```

---

### Task 6: Live e2e (stub) + captures

**Files:**
- Create: `e2e/test_roadmap_replan_rationale.py`

**Interfaces:**
- Consumes: the e2e harness fixtures + the in-process worker drain pattern. `scripts/e2e_run.sh` sets `LLM_PROVIDER=stub`.

Read `e2e/test_roadmap_replan.py` first for the exact journey that forces a milestone slip and calls
`POST /replan/preview` + `POST /replan/apply` (copy its onboarding + slip-forcing + preview/apply
helpers). Copy `_drain()` from `e2e/test_records_ai_fill.py`.

- [ ] **Step 1: Write the e2e**

```python
# e2e/test_roadmap_replan_rationale.py
"""Live Module 03: ai.roadmap.rationale AI-authors a re-plan's rationale.

Onboard → force a milestone slip → POST /replan/preview → POST /replan/apply (rationale is the
templated fallback + a job is enqueued) → drain the worker (LLM_PROVIDER=stub) → GET /replan/history
and assert the newest re-plan's `rationale` carries the stub marker.
"""

import httpx
# copy helpers from e2e/test_roadmap_replan.py (onboard, force slip, preview, apply) and _drain
# from e2e/test_records_ai_fill.py


def test_roadmap_replan_rationale(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # ... onboard, force a slip, preview ...
        apply_resp = c.post("/api/v1/roadmap/replan/apply", headers=wh, json={"change_ids": [cid]})
        assert apply_resp.status_code == 200, apply_resp.text
        assert apply_resp.json()["data"]["rationale"]  # templated fallback present
        capture("roadmap_replan_rationale", "apply", apply_resp)

        _drain()

        hist = c.get("/api/v1/roadmap/replan/history", headers=wh)
        assert hist.status_code == 200, hist.text
        newest = hist.json()["data"][0]
        assert "[stub-llm]" in newest["rationale"]
        capture("roadmap_replan_rationale", "history_after_drain", hist)
```
Verify the exact preview/apply request/response shapes and the history response path
(`data[0].rationale`) against `e2e/test_roadmap_replan.py` before finalizing. Make the test genuinely
pass; do not weaken the `[stub-llm]` assertion.

- [ ] **Step 2: Run the e2e**

Run: `scripts/e2e_run.sh` — the new test and existing roadmap/assessment e2e all pass; captures written under `e2e/_captures/roadmap_replan_rationale/`.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_roadmap_replan_rationale.py e2e/_captures/roadmap_replan_rationale
git commit -m "test(roadmap): live e2e for ai.roadmap.rationale"
```

---

### Task 7: Docs — FE guide, SOP, checklist

**Files:**
- Modify: `docs/fe-integration-guide-roadmap.md`, `docs/checklist/PROJECT_CHECKLIST.md`
- Create: `docs/sop/2026-09-21-roadmap-replan-rationale.md`

- [ ] **Step 1: Update the roadmap FE guide (§9)**

Document that `GET /replan/history` items and the `POST /replan/apply` response now carry `rationale`
(string | null): a holistic AI-authored "why we re-planned" narrative, written templated instantly and
AI-upgraded within seconds (re-fetch history to pick it up); render as opaque prose (as the guide
already advises for `reason`). Paste the `apply` and `history_after_drain` bodies **verbatim** from
`e2e/_captures/roadmap_replan_rationale/`. Note that `rationale` is `null` on pre-existing history rows
(created before this feature). Update the guide's Follow-ups/verification note that reserved
"AI-authored rationale" to mark it delivered. If the guide references the `roadmap.replan` job, note it
was removed.

- [ ] **Step 2: Write the SOP**

Create `docs/sop/2026-09-21-roadmap-replan-rationale.md` matching the existing SOP format (What shipped
/ Why / How + key decisions / What's involved with paths / Verification / Operate-rollback /
Follow-ups). Key decisions: new nullable `rationale` Text column + migration `0028`; async-upgrade on
`apply_replan` with a templated fallback; `complete()` (prose) not structured; removed the dead
`roadmap.replan` enqueue (no handler) and reconciled the two assessment tests; PII-free prompt.
Reference the commits and PR.

- [ ] **Step 3: Reconcile the checklist**

Check off the roadmap-re-plan-rationale Module 03 consumer with a dated (2026-09-21) note + PR ref.
Update the current-facing lines enumerating remaining Module-03 consumers so only the **onboarding AI
panel** remains. Module 03 stays **open** (one consumer left). Reconcile every current-facing line.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(roadmap): FE guide, SOP, checklist for re-plan rationale"
```

---

## Final: full local CI reproduction

After all tasks, from the repo root:
```bash
poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && \
poetry run mypy app && poetry run pylint app && poetry run bandit -r app -q && \
poetry run pytest && poetry run alembic upgrade head && poetry run alembic check && \
scripts/e2e_run.sh
```
Confirm single alembic head (`poetry run alembic heads` → `0028_roadmap_replan_rationale`). Fix any failure and re-run until clean. Then open the PR into `develop` with a clean body (no AI attribution).

## Self-Review notes (author)

- **Spec coverage:** column+migration (Task 1), builder (Task 2), enqueue+expose (Task 3), handler (Task 4), dead-job removal (Task 5), e2e (Task 6), docs (Task 7). ✔
- **No placeholders:** every code step has real code; the apply-test and e2e steps defer their setup to named existing test files (verify-against-harness), not TODOs. ✔
- **Type consistency:** `RoadmapReplan.rationale`, `build_roadmap_rationale_messages` kwargs, `_templated_rationale` signature, job type string `ai.roadmap.rationale` consistent across Tasks 2–6; handler reads `replan.changes`/`roadmap.startup_id`/`startup.stage`. ✔
- **Ordering:** Task 5 (remove dead enqueue) precedes Task 6 (e2e drain) so the drain never meets an unhandled job type. ✔
