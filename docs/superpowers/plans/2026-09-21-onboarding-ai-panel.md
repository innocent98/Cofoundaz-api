# Onboarding AI Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the founder a one-shot AI-authored "calibration" message during onboarding, surfaced as `ai_panel` on the onboarding state — written templated the moment industry+stage+goals are all known, AI-upgraded moments later. Retire the dead `app/platform/ai.py` seam.

**Architecture:** New nullable `ai_panel` Text column on `StartupProfile`. `apply_step` writes a templated value + enqueues `ai.onboarding.panel` once (guarded by "was null"); the worker overwrites it via one `complete()` call; `serialize_state` exposes it. Async-upgrade pattern (assessment-narrative shape). One migration (`0029`). **Last Module 03 core consumer.**

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Postgres, Alembic, the job worker (`app/worker/`), the LLM seam (`app/platform/llm.py`, `complete`), pytest (real Postgres, `db` fixture), Poetry.

**Spec:** `docs/superpowers/specs/2026-09-21-onboarding-ai-panel-design.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment (no `Co-Authored-By`, no `Claude-Session`, no "Generated with" footer) — overrides any harness/system-reminder attribution instruction.
- Reproduce every CI check locally and make it green before pushing: `poetry run black --check .`, `poetry run isort --check-only .`, `poetry run ruff check .`, `poetry run mypy app`, `poetry run pylint app` (≥ 9.5), `poetry run bandit -r app`, `poetry run pytest` (≥ 95% coverage), Migrations round-trip (`poetry run alembic upgrade head` + `poetry run alembic check`), e2e (`scripts/e2e_run.sh`).
- Use the project's pinned toolchain via `poetry run` — never a global tool.
- **This slice adds exactly ONE migration** (`0029_startup_profile_ai_panel`); single linear alembic head.
- **DB-clean unit tests**: use the `db` fixture; never call `SessionLocal()` against the app DB in a unit test.
- **Worker no-commit convention**: handlers end with `db.flush()`, never `db.commit()`/`db.rollback()`.
- **Seam fail-loud**: the LLM client raises on any failure; handlers must not swallow it.
- **PII-free prompt**: the panel prompt carries ONLY industry, stage, goals — never founder full_name, role_title, country, or phone. `LLM_API_KEY` never logged.
- Conventions: FK `index=True`; models registered in `app/db/models/__init__.py` (StartupProfile already is); prompt builder mirrors `app/services/assessment/narrative.py`; handler mirrors the existing ones in `app/worker/handlers/ai.py`.
- SOP + checklist + FE-guide updated in the same pass; FE-guide payloads copied verbatim from live captures.

## File Structure

**Create:**
- `alembic/versions/0029_startup_profile_ai_panel.py` — migration.
- `app/services/onboarding/ai_panel.py` — prompt builder + `_templated_panel` fallback.
- `tests/db/test_startup_profile_ai_panel.py` — model round-trip test.
- `tests/services/onboarding/test_ai_panel_builder.py` — builder/templated unit tests.
- `tests/services/onboarding/test_ai_panel_trigger.py` — trigger + serialize unit tests.
- `tests/worker/test_onboarding_panel_handler.py` — handler unit tests.
- `e2e/test_onboarding_ai_panel.py` — live panel upgrade (stub).
- `docs/fe-integration-guide-onboarding-ai-panel.md` — FE guide.
- `docs/sop/2026-09-21-onboarding-ai-panel.md` — SOP.

**Modify:**
- `app/db/models/startup.py` — add `ai_panel` to `StartupProfile` (and `Text` to the sqlalchemy import).
- `app/services/onboarding/steps.py` — call `_maybe_generate_ai_panel` at the end of `apply_step`.
- `app/services/onboarding/workspace.py` — add `ai_panel` to `serialize_state`.
- `app/worker/handlers/ai.py` — `handle_onboarding_panel` + registration.
- `docs/checklist/PROJECT_CHECKLIST.md` — check off the onboarding-panel consumer (closes the Module 03 consumer set).

**Delete (Task 5):**
- `app/platform/ai.py`, `tests/platform/test_ai.py`.

---

### Task 1: `ai_panel` column on `StartupProfile` + migration

**Files:**
- Modify: `app/db/models/startup.py`
- Create: `alembic/versions/0029_startup_profile_ai_panel.py`
- Test: `tests/db/test_startup_profile_ai_panel.py`

**Interfaces:**
- Produces: `StartupProfile.ai_panel: Mapped[str | None]` (Text, nullable); migration `0029_startup_profile_ai_panel` (down_revision `0028_roadmap_replan_rationale`).

- [ ] **Step 1: Add the column**

In `app/db/models/startup.py`: add `Text` to the `from sqlalchemy import ...` line (currently imports `Boolean, DateTime, Enum, ForeignKey, Integer, String, text` — add `Text`, keeping alphabetical/existing order). In `class StartupProfile`, after an existing text field (e.g. after `goals`/`notes`), add:
```python
    ai_panel: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Autogenerate the migration**

Run: `poetry run alembic revision --autogenerate -m "startup_profile_ai_panel"`
Rename the generated file to `alembic/versions/0029_startup_profile_ai_panel.py` and set
`revision = "0029_startup_profile_ai_panel"`, `down_revision = "0028_roadmap_replan_rationale"`. It
should be a single `op.add_column("startup_profiles", sa.Column("ai_panel", sa.Text(), nullable=True))`
with a matching `op.drop_column` downgrade. Edit only the filename + revision ids; keep autogenerate's ops.

- [ ] **Step 3: Verify round-trip + drift + single head**

Run: `poetry run alembic upgrade head` then `poetry run alembic check` (→ "No new upgrade operations detected.") and `poetry run alembic heads` (→ single head `0029_startup_profile_ai_panel`).

- [ ] **Step 4: Write the model test**

```python
# tests/db/test_startup_profile_ai_panel.py
from app.db.models.startup import StartupProfile
from tests.factories import create_startup, create_user


def test_startup_profile_ai_panel_nullable(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    assert s.profile.ai_panel is None  # nullable, defaults to NULL
    s.profile.ai_panel = "Got it — a fintech startup."
    db.flush()
    got = db.query(StartupProfile).filter_by(startup_id=s.id).one()
    assert got.ai_panel == "Got it — a fintech startup."
```
(`create_startup` builds a `StartupProfile` for the startup — confirm the `StartupProfile` PK/lookup field: it is `startup_id`; adjust the `filter_by` if the model uses a different key.)

- [ ] **Step 5: Run tests + lint**

Run: `poetry run pytest tests/db/test_startup_profile_ai_panel.py -v` (PASS) then `poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && poetry run mypy app` clean.

- [ ] **Step 6: Commit**

```bash
git add app/db/models/startup.py alembic/versions/0029_startup_profile_ai_panel.py tests/db/test_startup_profile_ai_panel.py
git commit -m "feat(onboarding): ai_panel column on startup_profiles + migration"
```

---

### Task 2: Onboarding panel prompt builder + templated fallback

**Files:**
- Create: `app/services/onboarding/ai_panel.py`
- Test: `tests/services/onboarding/test_ai_panel_builder.py`

**Interfaces:**
- Consumes: `LLMMessage` from `app/platform/llm.py`.
- Produces:
  - `build_onboarding_panel_messages(*, industry: str | None, stage: str | None, goals: list[str]) -> list[LLMMessage]`
  - `_templated_panel(industry: str, stage: str) -> str` (the instant fallback; reuses the retired stub's wording)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/onboarding/test_ai_panel_builder.py
from app.services.onboarding.ai_panel import _templated_panel, build_onboarding_panel_messages


def test_templated_panel_wording():
    assert _templated_panel("fintech", "idea") == (
        "Got it — a fintech startup at the idea stage. Let's calibrate your workspace."
    )


def test_builder_is_pii_free_and_includes_signals():
    msgs = build_onboarding_panel_messages(
        industry="Fintech", stage="idea", goals=["Get first customers", "Raise a pre-seed"],
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "Fintech" in body and "idea" in body
    assert "Get first customers" in body and "Raise a pre-seed" in body


def test_builder_handles_missing_signals():
    msgs = build_onboarding_panel_messages(industry=None, stage=None, goals=[])
    assert len(msgs) == 2  # no crash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/services/onboarding/test_ai_panel_builder.py -v` → FAIL (module missing). Create `tests/services/onboarding/__init__.py` only if the package doesn't already exist (it does — `tests/services/onboarding/test_workspace.py` is there).

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/onboarding/ai_panel.py
from app.platform.llm import LLMMessage


def _templated_panel(industry: str, stage: str) -> str:
    """Instant fallback shown until the AI job overwrites it (reuses the retired stub's wording)."""
    return f"Got it — a {industry} startup at the {stage} stage. Let's calibrate your workspace."


def build_onboarding_panel_messages(
    *,
    industry: str | None,
    stage: str | None,
    goals: list[str],
) -> list[LLMMessage]:
    """Prompt for the onboarding calibration panel. Business signals only -- no PII."""
    goal_lines = "; ".join(goals) if goals else "(not specified)"
    system = (
        "You are an AI co-founder greeting a founder as they finish onboarding. Write a short "
        "(2-3 sentence) warm, concrete calibration message: reflect back what you understand about "
        "their startup and set expectations for how you'll help. No preamble, no headings."
    )
    user = (
        f"Industry: {industry or 'unspecified'}. Stage: {stage or 'unspecified'}. "
        f"Goals: {goal_lines}. Write the calibration message."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/services/onboarding/test_ai_panel_builder.py -v` → PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/onboarding/ai_panel.py tests/services/onboarding/test_ai_panel_builder.py
git commit -m "feat(onboarding): AI panel prompt builder + templated fallback"
```

---

### Task 3: Trigger in `apply_step` + expose `ai_panel` in `serialize_state`

**Files:**
- Modify: `app/services/onboarding/steps.py`, `app/services/onboarding/workspace.py`
- Test: `tests/services/onboarding/test_ai_panel_trigger.py`

**Interfaces:**
- Consumes: `job_dispatcher` (`app/platform/jobs.py`); `_templated_panel` (Task 2).
- Produces: `_maybe_generate_ai_panel(db: Session, startup: Startup) -> None` (called at the end of `apply_step`); `serialize_state` now returns `ai_panel`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/onboarding/test_ai_panel_trigger.py
from app.db.models.enums import StartupStage
from app.db.models.job import Job
from app.services.onboarding.steps import _maybe_generate_ai_panel
from app.services.onboarding.workspace import serialize_state
from tests.factories import create_startup, create_user


def _jobs(db):
    return db.query(Job).filter(Job.type == "ai.onboarding.panel").count()


def _ready_startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)
    s.profile.goals = ["Get first customers"]
    db.flush()
    return u, s


def test_generates_and_enqueues_once_when_signals_complete(db):
    u, s = _ready_startup(db)
    _maybe_generate_ai_panel(db, s)
    assert s.profile.ai_panel is not None  # templated instant value
    assert _jobs(db) == 1
    _maybe_generate_ai_panel(db, s)  # already set -> no re-enqueue
    assert _jobs(db) == 1


def test_no_enqueue_when_signals_incomplete(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)  # no goals
    _maybe_generate_ai_panel(db, s)
    assert s.profile.ai_panel is None
    assert _jobs(db) == 0


def test_serialize_state_includes_ai_panel(db):
    u, s = _ready_startup(db)
    body = serialize_state(db, s, u)
    assert "ai_panel" in body
    assert body["ai_panel"] is None  # not generated yet
    _maybe_generate_ai_panel(db, s)
    body = serialize_state(db, s, u)
    assert body["ai_panel"] == s.profile.ai_panel
```
(Confirm `StartupStage.idea` is a real enum member — check `app/db/models/enums.py`. Confirm `create_startup(..., industry=..., stage=...)` sets those on the `Startup` via `**kw`.)

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/services/onboarding/test_ai_panel_trigger.py -v` → FAIL (`_maybe_generate_ai_panel` missing / no `ai_panel` key).

- [ ] **Step 3: Implement**

In `app/services/onboarding/steps.py`, add the import (isort order):
```python
from app.platform.jobs import job_dispatcher
from app.services.onboarding.ai_panel import _templated_panel
```
Add the helper and call it at the end of `apply_step` (after the existing `db.flush()`):
```python
def _maybe_generate_ai_panel(db: Session, startup: Startup) -> None:
    """Generate the onboarding AI panel exactly once, when industry + stage + goals are all set.

    Writes a templated instant value (so a later PATCH won't re-enqueue) then enqueues the AI job.
    """
    profile = startup.profile
    if profile.ai_panel is not None:
        return
    if not (startup.industry and startup.stage and profile.goals):
        return
    profile.ai_panel = _templated_panel(startup.industry, startup.stage.value)
    db.flush()
    job_dispatcher.enqueue(db, "ai.onboarding.panel", {"startup_id": str(startup.id)}, startup.id)
```
And, at the end of `apply_step`, after `db.flush()`:
```python
    _maybe_generate_ai_panel(db, startup)
```

In `app/services/onboarding/workspace.py`, add to the `serialize_state` returned dict (next to `"goals"`):
```python
        "ai_panel": profile.ai_panel,
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/services/onboarding/ tests/api/onboarding/ -q` → all pass (new + existing). Then `poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && poetry run mypy app` clean. If `apply_step`/`serialize_state` trips ruff C901, extract minimally (behavior identical).

- [ ] **Step 5: Commit**

```bash
git add app/services/onboarding/steps.py app/services/onboarding/workspace.py tests/services/onboarding/test_ai_panel_trigger.py
git commit -m "feat(onboarding): generate AI panel on signals-complete + expose in state"
```

---

### Task 4: Worker handler `handle_onboarding_panel`

**Files:**
- Modify: `app/worker/handlers/ai.py`
- Test: `tests/worker/test_onboarding_panel_handler.py`

**Interfaces:**
- Consumes: `build_onboarding_panel_messages` (Task 2); `Startup` (already imported in `ai.py`); `get_llm_client`, `settings`, `Job` (already imported).
- Produces: `handle_onboarding_panel(db, job) -> None`, registered for `"ai.onboarding.panel"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/worker/test_onboarding_panel_handler.py
import uuid

import pytest

from app.core.config import settings
from app.db.models.enums import StartupStage
from app.db.models.job import Job, JobStatus
from app.worker.handlers import ai as ai_mod
from app.worker.handlers.ai import handle_onboarding_panel
from tests.factories import create_startup, create_user


class _FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, *a, **k):  # pragma: no cover
        raise AssertionError


def _startup(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)
    s.profile.goals = ["Get first customers"]
    s.profile.ai_panel = "templated"
    db.flush()
    return s


def _job(startup_id):
    return Job(type="ai.onboarding.panel", payload={"startup_id": str(startup_id)},
               status=JobStatus.running)


def test_overwrites_ai_panel(db, monkeypatch):
    s = _startup(db)
    fake = _FakeLLM("Welcome — a fintech founder at the idea stage.")
    monkeypatch.setattr(ai_mod, "get_llm_client", lambda: fake)
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert s.profile.ai_panel == "Welcome — a fintech founder at the idea stage."
    assert fake.calls == 1


def test_noop_when_startup_missing(db):
    handle_onboarding_panel(db, _job(uuid.uuid4()))  # no raise


def test_stub_marks_panel(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")
    s = _startup(db)
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert "[stub-llm]" in s.profile.ai_panel


def test_fails_loud_on_llm_error(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    s = _startup(db)
    with pytest.raises(RuntimeError):
        handle_onboarding_panel(db, _job(s.id))
```

- [ ] **Step 2: Run to verify failure**

Run: `poetry run pytest tests/worker/test_onboarding_panel_handler.py -v` → FAIL (`handle_onboarding_panel` not importable).

- [ ] **Step 3: Implement**

Add the import at the top of `app/worker/handlers/ai.py` (isort order):
```python
from app.services.onboarding.ai_panel import build_onboarding_panel_messages
```
Append the handler + registration:
```python
def handle_onboarding_panel(db: Session, job: Job) -> None:
    """Overwrite a startup's onboarding AI panel with the LLM (prose). No commit.

    The templated panel written at signals-complete stays as the instant value and fallback.
    """
    startup = db.get(Startup, job.payload["startup_id"])
    if startup is None or startup.profile is None:
        return  # benign no-op
    messages = build_onboarding_panel_messages(
        industry=startup.industry,
        stage=(startup.stage.value if startup.stage else None),
        goals=(startup.profile.goals or []),
    )
    text = get_llm_client().complete(messages, max_tokens=settings.LLM_MAX_TOKENS)
    startup.profile.ai_panel = text.strip()
    db.flush()


register_handler("ai.onboarding.panel", handle_onboarding_panel)
```

- [ ] **Step 4: Run tests**

Run: `poetry run pytest tests/worker/test_onboarding_panel_handler.py -v` → PASS (4). Then `poetry run pytest tests/worker/ -q` (existing handlers green) and `poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && poetry run mypy app` clean.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/test_onboarding_panel_handler.py
git commit -m "feat(onboarding): ai.onboarding.panel worker fills the calibration panel"
```

---

### Task 5: Retire the dead `app/platform/ai.py` seam

**Files:**
- Delete: `app/platform/ai.py`, `tests/platform/test_ai.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the dead `AIPanel`/`StubAIPanel` seam removed (its wording preserved in Task 2's `_templated_panel`).

- [ ] **Step 1: Confirm no consumers**

Run: `grep -rnE 'platform.ai|AIPanel|StubAIPanel|ai_panel:' app tests e2e | grep -v 'app/platform/ai.py' | grep -v 'tests/platform/test_ai.py'`
Expected: no hit references `app.platform.ai` (the new `StartupProfile.ai_panel` column and `ai.onboarding.panel` job are unrelated names — confirm none import from `app.platform.ai`). If any real consumer exists, STOP and report it.

- [ ] **Step 2: Delete the files**

```bash
git rm app/platform/ai.py tests/platform/test_ai.py
```

- [ ] **Step 3: Verify nothing broke**

Run: `poetry run pytest tests/platform/ tests/worker/ tests/services/onboarding/ -q` (no import errors) and `poetry run mypy app && poetry run ruff check .` clean.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(platform): remove dead unused AIPanel/StubAIPanel seam"
```

---

### Task 6: Live e2e (stub) + captures

**Files:**
- Create: `e2e/test_onboarding_ai_panel.py`

**Interfaces:**
- Consumes: the e2e harness fixtures + the in-process worker drain pattern. `scripts/e2e_run.sh` sets `LLM_PROVIDER=stub`.

Read `e2e/test_onboarding.py` first for the exact wizard walk (step 1 full_name → step 2 name → step 3
industry/business_model/stage → step 4 goals) and `e2e/conftest.py` for `base_url`/`make_verified_user`.
Copy `_drain()` verbatim from `e2e/test_records_ai_fill.py`.

- [ ] **Step 1: Write the e2e**

```python
# e2e/test_onboarding_ai_panel.py
"""Live Module 03: ai.onboarding.panel AI-authors the onboarding calibration panel.

Walk onboarding through the goals step (industry + stage + goals set) → GET /onboarding/state shows a
templated `ai_panel` + a job is enqueued → drain the worker (LLM_PROVIDER=stub) → GET /onboarding/state
shows the `[stub-llm]` panel.
"""

import httpx
# copy the wizard-walk helper from e2e/test_onboarding.py and _drain from e2e/test_records_ai_fill.py


def test_onboarding_ai_panel(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # register + login, then PATCH steps 1..4 (the step-4 goals patch triggers the panel):
        # ... walk steps exactly as e2e/test_onboarding.py does ...
        state1 = c.get("/api/v1/onboarding/state", headers=h)
        assert state1.status_code == 200, state1.text
        assert state1.json()["data"]["ai_panel"]  # templated (non-stub) present after step 4
        assert "[stub-llm]" not in state1.json()["data"]["ai_panel"]
        capture("onboarding_ai_panel", "state_templated", state1)

        _drain()

        state2 = c.get("/api/v1/onboarding/state", headers=h)
        assert state2.status_code == 200, state2.text
        assert "[stub-llm]" in state2.json()["data"]["ai_panel"]
        capture("onboarding_ai_panel", "state_after_drain", state2)
```
Verify the exact PATCH bodies and the `data.ai_panel` path against `e2e/test_onboarding.py` before
finalizing. Make the test genuinely pass; do NOT weaken the `[stub-llm]` assertion.

- [ ] **Step 2: Run the e2e**

Run: `scripts/e2e_run.sh` — the new test and existing onboarding e2e all pass; captures written under `e2e/_captures/onboarding_ai_panel/`.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_onboarding_ai_panel.py e2e/_captures/onboarding_ai_panel
git commit -m "test(onboarding): live e2e for ai.onboarding.panel"
```

---

### Task 7: Docs — FE guide, SOP, checklist

**Files:**
- Create: `docs/fe-integration-guide-onboarding-ai-panel.md`, `docs/sop/2026-09-21-onboarding-ai-panel.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: Write the FE guide**

Create `docs/fe-integration-guide-onboarding-ai-panel.md` (no onboarding FE guide exists yet — a focused
one for this field). Document: `GET/PATCH /onboarding/state` now returns `ai_panel` (string | null) — a
short AI-authored calibration message shown once the founder's industry, stage, and goals are set;
templated instantly, AI-upgraded within seconds (re-fetch state to pick it up); `null` before the
signals are complete and on pre-existing workspaces; render as opaque prose. Paste the `state_templated`
and `state_after_drain` `data` bodies **verbatim** from `e2e/_captures/onboarding_ai_panel/`. Include a
short verification table.

- [ ] **Step 2: Write the SOP**

Create `docs/sop/2026-09-21-onboarding-ai-panel.md` matching the existing SOP format (What shipped / Why
/ How + key decisions / What's involved with paths / Verification / Operate-rollback / Follow-ups). Key
decisions: new nullable `ai_panel` Text column + migration `0029`; one-shot generate-once trigger in
`apply_step` (write templated → enqueue, guarded by "was null"); `complete()` prose not structured;
retired the dead `app/platform/ai.py` seam; PII-free prompt (industry/stage/goals only). **Note this
closes the Module 03 AI-consumer set.** Reference the commits and PR.

- [ ] **Step 3: Reconcile the checklist**

Check off the onboarding-AI-panel Module 03 consumer with a dated (2026-09-21) note. Update the
current-facing lines: **all five/six Module-03 core consumers are now shipped**; Module 03's remaining
scope is only any non-OpenAI provider impl / infra, not consumers. Decide and state Module 03's status
honestly (the consumer set is complete; if provider work remains it stays "open" for that, else it can
be marked complete — reconcile consistently with how the checklist frames Module 03's total scope).
Reconcile every current-facing line; leave frozen "_Previously:_" historical blocks as-is.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(onboarding): FE guide, SOP, checklist for AI panel"
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
Confirm single alembic head (`poetry run alembic heads` → `0029_startup_profile_ai_panel`). Fix any failure and re-run until clean. Then open the PR into `develop` with a clean body (no AI attribution).

## Self-Review notes (author)

- **Spec coverage:** column+migration (Task 1), builder+templated (Task 2), trigger+serialize (Task 3), handler (Task 4), dead-seam removal (Task 5), e2e (Task 6), docs (Task 7). ✔
- **No placeholders:** every code step has real code; the e2e wizard walk and the Task 3 `StartupStage.idea`/factory-kwarg usage defer to named existing files/tests to verify against reality, not TODOs. ✔
- **Type consistency:** `StartupProfile.ai_panel`, `_templated_panel(industry, stage)`, `build_onboarding_panel_messages(*, industry, stage, goals)`, job type `ai.onboarding.panel` consistent across Tasks 2–6; handler reads `startup.profile.goals`/`startup.industry`/`startup.stage`. ✔
- **Ordering:** Task 2 (creates `_templated_panel`) precedes Task 3 (imports it) and Task 5 (removes the old stub whose wording Task 2 preserved). ✔
