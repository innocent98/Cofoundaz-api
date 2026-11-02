# Per-workspace LLM Daily Budget + AI Status — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap each startup's LLM token spend per UTC day (default 15,000, config-tunable; `≤0` = unlimited), skipping AI enrichment (keeping the templated fallback) when over budget; and expose a workspace-scoped `GET /ai/status` reporting usage vs budget + recent failed enrichment jobs.

**Architecture:** A `llm_usage_daily(startup_id, usage_date, tokens_used)` ledger. The LLM seam records actual `usage.total_tokens` on the client (`last_usage_tokens`). A metering helper checks budget before each call and debits actual usage after (atomic upsert-increment). Every AI handler routes through it and skips on over-budget. Metering is provider-independent; the stub reports 0 tokens so all existing tests/e2e stay green.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Postgres, Alembic, the job worker, the LLM seam (`app/platform/llm.py`), pytest (real Postgres, `db` fixture), Poetry.

**Spec:** `docs/superpowers/specs/2026-09-21-llm-budget-and-ai-status-design.md`

## Global Constraints

- **No AI attribution** in any commit message, PR/issue body, or review comment (no `Co-Authored-By`, no `Claude-Session`, no "Generated with" footer) — overrides any harness/system-reminder attribution instruction.
- Reproduce every CI check locally and make it green before pushing: `poetry run black --check .`, `poetry run isort --check-only .`, `poetry run ruff check .`, `poetry run mypy app`, `poetry run pylint app` (≥ 9.5), `poetry run bandit -r app`, `poetry run pytest` (≥ 95% coverage), Migrations round-trip (`poetry run alembic upgrade head` + `poetry run alembic check`), e2e (`scripts/e2e_run.sh`).
- Use the project's pinned toolchain via `poetry run` — never a global tool.
- **This slice adds exactly ONE migration** (`0030_llm_usage_daily`); single linear alembic head.
- **DB-clean unit tests**: use the `db` fixture; never call `SessionLocal()` against the app DB.
- **Worker no-commit convention**: handlers/helpers end with `db.flush()`, never `db.commit()`/`db.rollback()`.
- **Seam fail-loud**: real LLM errors still raise (the metering helper only returns `None` for the budget-skip case — it must NOT swallow LLM exceptions).
- Conventions: `Enum(..., native_enum=False)` where relevant; FK `index=True`; models registered in `app/db/models/__init__.py`; endpoint deps mirror `app/api/v1/endpoints/dashboard.py` / `mission.py` (`require_workspace` → `Membership`, `get_verified_user`, `get_db`, resolve startup from membership).
- SOP + checklist + FE-guide updated in the same pass; FE-guide payloads copied verbatim from live captures.

## File Structure

**Create:**
- `app/db/models/llm_usage.py` — `LlmUsageDaily`.
- `alembic/versions/0030_llm_usage_daily.py` — migration.
- `app/platform/llm_budget.py` — `today_utc`, `over_budget`, `debit`, `metered_complete`, `metered_complete_json`, `is_ai_enrichment_job`.
- `app/api/v1/endpoints/ai.py` — `GET /ai/status`.
- `tests/db/test_llm_usage_models.py`, `tests/platform/test_llm_budget.py`, `tests/platform/test_llm_usage_seam.py`, `tests/api/test_ai_status.py`, `tests/worker/test_ai_budget_enforcement.py`.
- `e2e/test_ai_status.py`.
- `docs/fe-integration-guide-ai-status.md`, `docs/sop/2026-09-21-llm-budget-ai-status.md`.

**Modify:**
- `app/core/config.py` — add `LLM_DAILY_TOKEN_BUDGET`.
- `app/db/models/__init__.py` — register `LlmUsageDaily`.
- `app/platform/llm.py` — `last_usage_tokens` on both clients.
- `app/worker/handlers/ai.py` — route the 8 handlers through `metered_complete*` + skip-on-`None`.
- `app/worker/handlers/plan.py` — check-once budget guard + per-section `debit`.
- `app/api/v1/api.py` — mount the `ai` router.
- `docs/checklist/PROJECT_CHECKLIST.md` — mark the two infra items done; drop provider item as won't-do.

---

### Task 1: `LlmUsageDaily` model + migration + config

**Files:**
- Modify: `app/core/config.py`, `app/db/models/__init__.py`
- Create: `app/db/models/llm_usage.py`, `alembic/versions/0030_llm_usage_daily.py`
- Test: `tests/db/test_llm_usage_models.py`

**Interfaces:**
- Produces: `LlmUsageDaily` (columns `startup_id`, `usage_date`, `tokens_used`; `UniqueConstraint("startup_id","usage_date", name="uq_llm_usage_startup_date")`); `settings.LLM_DAILY_TOKEN_BUDGET: int = 15000`; migration `0030_llm_usage_daily` (down_revision `0029_startup_profile_ai_panel`).

- [ ] **Step 1: Config**

In `app/core/config.py`, near the other `LLM_*` settings, add:
```python
    LLM_DAILY_TOKEN_BUDGET: int = 15000  # per-startup per-UTC-day token cap; <= 0 = unlimited
```

- [ ] **Step 2: Model**

```python
# app/db/models/llm_usage.py
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class LlmUsageDaily(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "llm_usage_daily"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("startup_id", "usage_date", name="uq_llm_usage_startup_date"),
    )
```

- [ ] **Step 3: Register the model**

Add to `app/db/models/__init__.py` (alphabetical, near `learning`/`membership`):
```python
from app.db.models.llm_usage import LlmUsageDaily  # noqa: F401
```

- [ ] **Step 4: Migration**

Run `poetry run alembic revision --autogenerate -m "llm_usage_daily"`. Rename to
`alembic/versions/0030_llm_usage_daily.py`; set `revision = "0030_llm_usage_daily"`,
`down_revision = "0029_startup_profile_ai_panel"`. It should create `llm_usage_daily` (columns + the
`uq_llm_usage_startup_date` unique constraint + the `startup_id` index); downgrade drops it. Edit only
filename/revision ids. Verify `poetry run alembic upgrade head`, `poetry run alembic check` ("No new
upgrade operations detected."), `poetry run alembic heads` (single `0030_llm_usage_daily`).

- [ ] **Step 5: Model test**

```python
# tests/db/test_llm_usage_models.py
import pytest
from datetime import date

from sqlalchemy.exc import IntegrityError

from app.db.models.llm_usage import LlmUsageDaily
from tests.factories import create_startup, create_user


def _row(db, s, **kw):
    r = LlmUsageDaily(startup_id=s.id, usage_date=kw.get("usage_date", date.today()),
                      tokens_used=kw.get("tokens_used", 0))
    db.add(r)
    db.flush()
    return r


def test_round_trip_and_default(db):
    u = create_user(db); s = create_startup(db, owner=u)
    r = _row(db, s, tokens_used=123)
    got = db.query(LlmUsageDaily).filter_by(id=r.id).one()
    assert got.tokens_used == 123


def test_unique_per_startup_day(db):
    u = create_user(db); s = create_startup(db, owner=u)
    _row(db, s)
    with pytest.raises(IntegrityError):
        _row(db, s)  # same (startup, today)
```

- [ ] **Step 6: Run + lint**

`poetry run pytest tests/db/test_llm_usage_models.py -v` (PASS) then black/isort/ruff/mypy clean.

- [ ] **Step 7: Commit**

```bash
git add app/db/models/llm_usage.py app/db/models/__init__.py app/core/config.py alembic/versions/0030_llm_usage_daily.py tests/db/test_llm_usage_models.py
git commit -m "feat(llm): llm_usage_daily ledger + LLM_DAILY_TOKEN_BUDGET config + migration"
```

---

### Task 2: Seam usage plumbing — `last_usage_tokens`

**Files:**
- Modify: `app/platform/llm.py`
- Test: `tests/platform/test_llm_usage_seam.py`

**Interfaces:**
- Produces: `OpenAILLMClient.last_usage_tokens: int` (set from `usage.total_tokens` after each call); `StubLLMClient.last_usage_tokens: int = 0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/platform/test_llm_usage_seam.py
from app.platform.llm import OpenAILLMClient, StubLLMClient


def test_stub_reports_zero_usage():
    c = StubLLMClient()
    c.complete([], max_tokens=10)
    assert c.last_usage_tokens == 0


def test_openai_parses_usage(monkeypatch):
    c = OpenAILLMClient()
    fake_body = {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 512}}
    monkeypatch.setattr(c, "_post_chat", lambda payload: fake_body)
    out = c.complete([], max_tokens=10)
    assert out == "hi"
    assert c.last_usage_tokens == 512


def test_openai_usage_defaults_zero_when_absent(monkeypatch):
    c = OpenAILLMClient()
    monkeypatch.setattr(c, "_post_chat", lambda payload: {"choices": [{"message": {"content": "x"}}]})
    c.complete([], max_tokens=10)
    assert c.last_usage_tokens == 0
```

- [ ] **Step 2: Run to verify failure**

`poetry run pytest tests/platform/test_llm_usage_seam.py -v` → FAIL (`last_usage_tokens` missing).

- [ ] **Step 3: Implement**

In `app/platform/llm.py`:
- `StubLLMClient`: add a class attribute `last_usage_tokens: int = 0`.
- `OpenAILLMClient`: initialize `self.last_usage_tokens: int = 0` (add an `__init__`, or set it in each method before use — prefer a small `__init__`). In BOTH `complete` and `complete_json`, immediately after `body = self._post_chat(...)`, add:
  ```python
  self.last_usage_tokens = int((body.get("usage") or {}).get("total_tokens", 0) or 0)
  ```
  (Do not otherwise change the existing content/parse/raise logic.)

- [ ] **Step 4: Run to verify pass**

`poetry run pytest tests/platform/test_llm_usage_seam.py -v` → PASS (3). black/isort/ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add app/platform/llm.py tests/platform/test_llm_usage_seam.py
git commit -m "feat(llm): surface last_usage_tokens from the LLM seam"
```

---

### Task 3: Metering helper — `app/platform/llm_budget.py`

**Files:**
- Create: `app/platform/llm_budget.py`
- Test: `tests/platform/test_llm_budget.py`

**Interfaces:**
- Consumes: `LlmUsageDaily` (Task 1); `get_llm_client` + `last_usage_tokens` (Task 2); `settings`.
- Produces:
  - `today_utc() -> date`
  - `over_budget(db, startup_id: uuid.UUID) -> bool`
  - `debit(db, startup_id: uuid.UUID, tokens: int) -> None`
  - `metered_complete(db, startup_id, messages, *, max_tokens) -> str | None`
  - `metered_complete_json(db, startup_id, messages, *, schema, max_tokens) -> dict | None`
  - `is_ai_enrichment_job(job_type: str) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/platform/test_llm_budget.py
from datetime import date

from app.core.config import settings
from app.db.models.llm_usage import LlmUsageDaily
from app.platform import llm_budget as lb
from tests.factories import create_startup, create_user


class _FakeClient:
    def __init__(self, text, tokens):
        self.text = text
        self.last_usage_tokens = tokens
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls += 1
        return {"ok": True}


def _used(db, s):
    r = db.query(LlmUsageDaily).filter_by(startup_id=s.id, usage_date=date.today()).one_or_none()
    return r.tokens_used if r else 0


def test_debit_upserts_and_increments(db):
    u = create_user(db); s = create_startup(db, owner=u)
    lb.debit(db, s.id, 100)
    lb.debit(db, s.id, 50)
    assert _used(db, s) == 150


def test_over_budget_threshold(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 200)
    u = create_user(db); s = create_startup(db, owner=u)
    assert lb.over_budget(db, s.id) is False
    lb.debit(db, s.id, 200)
    assert lb.over_budget(db, s.id) is True


def test_unlimited_when_budget_non_positive(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 0)
    u = create_user(db); s = create_startup(db, owner=u)
    lb.debit(db, s.id, 10_000)
    assert lb.over_budget(db, s.id) is False


def test_metered_complete_debits_actual(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 1000)
    fake = _FakeClient("hello", 321)
    monkeypatch.setattr(lb, "get_llm_client", lambda: fake)
    u = create_user(db); s = create_startup(db, owner=u)
    out = lb.metered_complete(db, s.id, [], max_tokens=100)
    assert out == "hello" and fake.calls == 1
    assert _used(db, s) == 321


def test_metered_complete_skips_when_over_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    fake = _FakeClient("hello", 321)
    monkeypatch.setattr(lb, "get_llm_client", lambda: fake)
    u = create_user(db); s = create_startup(db, owner=u)
    lb.debit(db, s.id, 100)  # at budget
    out = lb.metered_complete(db, s.id, [], max_tokens=100)
    assert out is None and fake.calls == 0  # no LLM call
    assert _used(db, s) == 100  # no debit


def test_is_ai_enrichment_job():
    assert lb.is_ai_enrichment_job("ai.dashboard.briefing")
    assert lb.is_ai_enrichment_job("business.persona.ai_fill")
    assert lb.is_ai_enrichment_job("business.plan.generate")
    assert not lb.is_ai_enrichment_job("roadmap.replanned")
    assert not lb.is_ai_enrichment_job("email.notification")
```

- [ ] **Step 2: Run to verify failure**

`poetry run pytest tests/platform/test_llm_budget.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# app/platform/llm_budget.py
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.llm_usage import LlmUsageDaily
from app.platform.llm import LLMMessage, get_llm_client


def today_utc() -> date:
    return datetime.now(UTC).date()


def over_budget(db: Session, startup_id: uuid.UUID) -> bool:
    budget = settings.LLM_DAILY_TOKEN_BUDGET
    if budget <= 0:
        return False  # unlimited
    used = db.execute(
        select(LlmUsageDaily.tokens_used).where(
            LlmUsageDaily.startup_id == startup_id, LlmUsageDaily.usage_date == today_utc()
        )
    ).scalar_one_or_none()
    return (used or 0) >= budget


def debit(db: Session, startup_id: uuid.UUID, tokens: int) -> None:
    if tokens <= 0:
        return
    stmt = (
        pg_insert(LlmUsageDaily)
        .values(startup_id=startup_id, usage_date=today_utc(), tokens_used=tokens)
        .on_conflict_do_update(
            index_elements=["startup_id", "usage_date"],
            set_={"tokens_used": LlmUsageDaily.tokens_used + tokens},
        )
    )
    db.execute(stmt)
    db.flush()


def metered_complete(
    db: Session, startup_id: uuid.UUID, messages: list[LLMMessage], *, max_tokens: int
) -> str | None:
    if over_budget(db, startup_id):
        return None
    client = get_llm_client()
    text = client.complete(messages, max_tokens=max_tokens)
    debit(db, startup_id, getattr(client, "last_usage_tokens", 0))
    return text


def metered_complete_json(
    db: Session, startup_id: uuid.UUID, messages: list[LLMMessage], *, schema: dict, max_tokens: int
) -> dict | None:
    if over_budget(db, startup_id):
        return None
    client = get_llm_client()
    result = client.complete_json(messages, schema=schema, max_tokens=max_tokens)
    debit(db, startup_id, getattr(client, "last_usage_tokens", 0))
    return result


def is_ai_enrichment_job(job_type: str) -> bool:
    return (
        job_type.startswith("ai.")
        or job_type.endswith(".ai_fill")
        or job_type == "business.plan.generate"
    )
```

- [ ] **Step 4: Run to verify pass**

`poetry run pytest tests/platform/test_llm_budget.py -v` → PASS (7). black/isort/ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add app/platform/llm_budget.py tests/platform/test_llm_budget.py
git commit -m "feat(llm): per-workspace daily token budget metering helper"
```

---

### Task 4: Enforce the budget in the 8 `ai.py` handlers (batch)

**Files:**
- Modify: `app/worker/handlers/ai.py`
- Test: `tests/worker/test_ai_budget_enforcement.py`

**Interfaces:**
- Consumes: `metered_complete`, `metered_complete_json` (Task 3).

This is ONE batched change: every LLM call in the 8 handlers routes through the metering helper and
skips on `None`. Import at the top of `ai.py` (isort order):
```python
from app.platform.llm_budget import metered_complete, metered_complete_json
```
Then, per handler (the `startup_id`/`startup` is already in scope as noted):

| Handler | Replace | With (skip-on-None) |
|---|---|---|
| `handle_assessment_narrative` | `text = get_llm_client().complete(messages, max_tokens=…)` | `text = metered_complete(db, startup_id, messages, max_tokens=…)`; then `if text is None: return` before `result.narrative = text.strip()` |
| `handle_canvas_ai_fill` | `filled = get_llm_client().complete_json(messages, schema=…, max_tokens=…)` | `filled = metered_complete_json(db, startup.id, messages, schema=…, max_tokens=…)`; `if filled is None: return` |
| `handle_record_ai_fill` | `result = get_llm_client().complete_json(build_record_fill_messages(…), schema=…, max_tokens=…)` | `result = metered_complete_json(db, startup.id, …)`; `if result is None: return` |
| `handle_mission_reason` | `result = get_llm_client().complete_json(…)` | `result = metered_complete_json(db, mission.startup_id, …)`; `if result is None: return` |
| `handle_health_recommendations` | `result = get_llm_client().complete_json(…)` | `result = metered_complete_json(db, startup.id, …)`; `if result is None: return` |
| `handle_dashboard_briefing` | `result = get_llm_client().complete_json(…)` | `result = metered_complete_json(db, startup.id, …)`; `if result is None: return` |
| `handle_roadmap_rationale` | `text = get_llm_client().complete(messages, max_tokens=…)` | `text = metered_complete(db, roadmap.startup_id, messages, max_tokens=…)`; `if text is None: return` (guard: `roadmap` is already null-checked above; use `roadmap.startup_id`) |
| `handle_onboarding_panel` | `text = get_llm_client().complete(messages, max_tokens=…)` | `text = metered_complete(db, startup.id, messages, max_tokens=…)`; `if text is None: return` |

Each `if <result> is None: return` skips the enrichment — the templated value written earlier stays, no
error, no status flip. Do NOT otherwise change the handlers. `get_llm_client` may remain imported (still
used by the fail-loud tests via monkeypatch of the handler module) — leave the import.

- [ ] **Step 1: Write the failing tests**

```python
# tests/worker/test_ai_budget_enforcement.py
from datetime import date

from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.db.models.llm_usage import LlmUsageDaily
from app.platform import llm_budget
from app.worker.handlers.ai import handle_onboarding_panel
from tests.factories import create_startup, create_user
from app.db.models.enums import StartupStage


class _FakeClient:
    def __init__(self, text, tokens):
        self.text = text
        self.last_usage_tokens = tokens
        self.calls = 0

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.calls += 1
        return self.text

    def complete_json(self, *a, **k):  # pragma: no cover
        raise AssertionError


def _ready(db):
    u = create_user(db)
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)
    s.profile.goals = ["Get customers"]
    s.profile.ai_panel = "templated"
    db.flush()
    return s


def _job(sid):
    return Job(type="ai.onboarding.panel", payload={"startup_id": str(sid)}, status=JobStatus.running)


def test_handler_enriches_and_debits_under_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 10_000)
    fake = _FakeClient("AI panel text", 250)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    s = _ready(db)
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert s.profile.ai_panel == "AI panel text"
    row = db.query(LlmUsageDaily).filter_by(startup_id=s.id, usage_date=date.today()).one()
    assert row.tokens_used == 250


def test_handler_skips_when_over_budget(db, monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    fake = _FakeClient("AI panel text", 250)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: fake)
    s = _ready(db)
    llm_budget.debit(db, s.id, 100)  # at budget
    handle_onboarding_panel(db, _job(s.id))
    db.refresh(s.profile)
    assert s.profile.ai_panel == "templated"  # skipped — templated kept
    assert fake.calls == 0
```

- [ ] **Step 2: Run to verify failure**

`poetry run pytest tests/worker/test_ai_budget_enforcement.py -v` → FAIL (handler still calls the client directly, debits nothing / doesn't skip).

- [ ] **Step 3: Implement the table of edits above.**

- [ ] **Step 4: Run tests**

`poetry run pytest tests/worker/test_ai_budget_enforcement.py -v` → PASS. Then run the FULL existing AI handler suites to confirm no regression (they monkeypatch `ai_mod.get_llm_client`; now the call goes through `llm_budget.get_llm_client` — SEE THE NOTE BELOW):
`poetry run pytest tests/worker/ -q`.

**IMPORTANT compatibility note for the implementer:** the existing per-handler tests
(`test_onboarding_panel_handler.py`, `test_roadmap_rationale_handler.py`, `test_ai_handler.py`, etc.)
monkeypatch `app.worker.handlers.ai.get_llm_client`. After this change the handlers call
`metered_complete*`, which calls `app.platform.llm_budget.get_llm_client` — so those monkeypatches no
longer intercept the call, and those tests will break. You MUST update the affected existing tests to
(a) monkeypatch `app.platform.llm_budget.get_llm_client` instead (and set `LLM_DAILY_TOKEN_BUDGET` high
so they aren't skipped), keeping their assertions; the fail-loud tests (which set `LLM_PROVIDER=openai`
+ empty key) still work because `metered_complete` calls the real client which raises. Run
`poetry run pytest tests/worker/ -q` until green. This test-reconciliation is part of Task 4.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/ai.py tests/worker/
git commit -m "feat(llm): enforce per-workspace token budget in the 8 AI handlers"
```

---

### Task 5: Enforce the budget in the business-plan handler

**Files:**
- Modify: `app/worker/handlers/plan.py`
- Test: `tests/worker/test_plan_handler.py` (extend)

**Interfaces:**
- Consumes: `over_budget`, `debit` (Task 3).

The plan handler makes 10 per-section `complete` calls. To keep plans whole, check budget ONCE up
front; if over, no-op (retry later — no partial plan). If under, generate all sections with the raw
client but `debit` actual usage per section. Do NOT use `metered_complete` per section (it would skip
mid-plan and leave empty tail sections).

- [ ] **Step 1: Write the failing tests**

Add to `tests/worker/test_plan_handler.py` (reuse its existing plan/startup setup — read the file):
```python
def test_plan_skips_when_over_budget(db, monkeypatch):
    from app.core.config import settings
    from app.db.models.enums import BusinessPlanStatus
    from app.platform import llm_budget
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100)
    # ... build a `generating` BusinessPlan + startup as the file's other tests do ...
    llm_budget.debit(db, startup.id, 100)  # over budget
    handle_plan_generate(db, job)
    db.refresh(plan)
    assert plan.status == BusinessPlanStatus.generating  # untouched — no partial plan
    assert plan.document_id is None


def test_plan_generates_and_debits_under_budget(db, monkeypatch):
    from app.core.config import settings
    from app.db.models.enums import BusinessPlanStatus
    from app.db.models.llm_usage import LlmUsageDaily
    from app.worker.handlers import plan as plan_mod
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100_000)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "stub")  # stub debits 0 → fine
    # ... build generating plan + startup ...
    handle_plan_generate(db, job)
    db.refresh(plan)
    assert plan.status == BusinessPlanStatus.complete
```
Match the file's real setup (BusinessPlan/startup construction, the `job` shape). The exact per-section
debit amount under stub is 0 — assert the plan completes; a nonzero-debit assertion needs a fake client
(optional). Keep it to the two behaviors: over-budget → skip; under-budget → completes.

- [ ] **Step 2: Run to verify failure**

`poetry run pytest tests/worker/test_plan_handler.py -v` → FAIL (over-budget still generates).

- [ ] **Step 3: Implement**

In `app/worker/handlers/plan.py`, add imports:
```python
from app.platform.llm_budget import debit, over_budget
```
After `startup` is resolved (and before building sections):
```python
    if over_budget(db, startup.id):
        return  # skip — keep the plan `generating` for a later retry; no partial plan
```
Change the section loop to debit per section (keep the raw client, since the up-front check guards it):
```python
    client = get_llm_client()
    sections = []
    for s in PLAN_SECTIONS:
        body = client.complete(build_section_messages(s, context), max_tokens=settings.LLM_MAX_TOKENS)
        debit(db, startup.id, getattr(client, "last_usage_tokens", 0))
        sections.append({"heading": s.heading, "body": body.strip()})
```
(Leave the rest — `create_document`, status flip, event — unchanged.)

- [ ] **Step 4: Run tests**

`poetry run pytest tests/worker/test_plan_handler.py -v` → PASS. black/isort/ruff/mypy clean; `poetry run pytest tests/worker/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add app/worker/handlers/plan.py tests/worker/test_plan_handler.py
git commit -m "feat(llm): budget-guard the business-plan generator (check-once + per-section debit)"
```

---

### Task 6: `GET /ai/status` endpoint

**Files:**
- Create: `app/api/v1/endpoints/ai.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/test_ai_status.py`

**Interfaces:**
- Consumes: `over_budget`, `today_utc`, `is_ai_enrichment_job` (Task 3); `LlmUsageDaily`; `Job`.
- Produces: `GET /api/v1/ai/status`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_ai_status.py
# Mirror an existing tenant-scoped API test's auth/workspace setup (see tests/api/test_dashboard.py
# or tests/api/test_health_score.py) — a verified user + active workspace + X-Workspace-Id header.
from app.core.config import settings
from app.db.models.job import Job, JobStatus
from app.platform import llm_budget


def test_ai_status_shape_and_over_budget(client, founder_ctx, db):  # adapt to the real fixtures
    startup = founder_ctx.startup
    monkeypatch_budget = ...  # set settings.LLM_DAILY_TOKEN_BUDGET = 1000 (use monkeypatch fixture)
    llm_budget.debit(db, startup.id, 400)
    db.add(Job(type="ai.dashboard.briefing", payload={}, startup_id=startup.id, status=JobStatus.failed))
    db.add(Job(type="email.notification", payload={}, startup_id=startup.id, status=JobStatus.failed))
    db.commit()
    resp = client.get("/api/v1/ai/status", headers=founder_ctx.headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tokens_used_today"] == 400
    assert data["daily_budget"] == 1000
    assert data["over_budget"] is False
    assert "resets_at" in data
    types = [f["type"] for f in data["recent_enrichment_failures"]]
    assert "ai.dashboard.briefing" in types
    assert "email.notification" not in types  # non-AI failure excluded
```
Adapt fixtures/headers to the project's real API-test harness (read `tests/api/test_dashboard.py`). If
the suite has no `client`+workspace fixture, follow whatever `tests/api/test_health_score.py` uses.

- [ ] **Step 2: Run to verify failure**

`poetry run pytest tests/api/test_ai_status.py -v` → FAIL (404 / no route).

- [ ] **Step 3: Implement**

```python
# app/api/v1/endpoints/ai.py
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user, require_workspace   # confirm actual dep names/paths
from app.core.config import settings
from app.core.envelope import success_response
from app.db.models.job import Job, JobStatus
from app.db.models.llm_usage import LlmUsageDaily
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.platform.llm_budget import is_ai_enrichment_job, over_budget, today_utc

router = APIRouter()


@router.get("/status")
def ai_status(
    membership: Membership = Depends(require_workspace),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    startup_id = membership.startup_id
    used = db.execute(
        select(LlmUsageDaily.tokens_used).where(
            LlmUsageDaily.startup_id == startup_id, LlmUsageDaily.usage_date == today_utc()
        )
    ).scalar_one_or_none() or 0
    budget = settings.LLM_DAILY_TOKEN_BUDGET
    resets_at = datetime.combine(today_utc() + timedelta(days=1), time.min, tzinfo=UTC)
    failed = (
        db.query(Job)
        .filter(Job.startup_id == startup_id, Job.status == JobStatus.failed)
        .order_by(Job.created_at.desc())
        .limit(50)
        .all()
    )
    failures = [
        {"type": j.type, "failed_at": j.updated_at.isoformat() if j.updated_at else None}
        for j in failed
        if is_ai_enrichment_job(j.type)
    ][:20]
    return success_response(
        {
            "tokens_used_today": used,
            "daily_budget": budget if budget > 0 else None,
            "over_budget": over_budget(db, startup_id),
            "resets_at": resets_at.isoformat(),
            "recent_enrichment_failures": failures,
        }
    )
```
Confirm the exact dependency names (`require_workspace`, `get_verified_user`, `get_db`) and the
`Membership.startup_id` attribute against `app/api/v1/endpoints/dashboard.py` — mirror it exactly.

In `app/api/v1/api.py`: import the `ai` endpoints module and add
`api_router.include_router(ai.router, prefix="/ai", tags=["ai"])` alongside the other includes.

- [ ] **Step 4: Run tests**

`poetry run pytest tests/api/test_ai_status.py -v` → PASS. black/isort/ruff/mypy clean.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/ai.py app/api/v1/api.py tests/api/test_ai_status.py
git commit -m "feat(ai): GET /ai/status — workspace token usage vs budget + failed enrichments"
```

---

### Task 7: Live e2e (stub) + capture

**Files:**
- Create: `e2e/test_ai_status.py`

Read an existing e2e that onboards a workspace and reads a tenant endpoint (e.g. `e2e/test_dashboard.py`
or `e2e/test_health_score.py`) for the onboard + workspace-header pattern; reuse it.

- [ ] **Step 1: Write the e2e**

```python
# e2e/test_ai_status.py
"""Live: GET /ai/status returns the workspace's token usage vs budget (stub accrues 0)."""

import httpx
# copy the onboard + workspace-header helper from e2e/test_dashboard.py


def test_ai_status(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # onboard to get a workspace + X-Workspace-Id header (wh) ...
        resp = c.get("/api/v1/ai/status", headers=wh)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["tokens_used_today"] == 0          # stub debits 0
        assert data["over_budget"] is False
        assert "daily_budget" in data and "resets_at" in data
        assert data["recent_enrichment_failures"] == [] or isinstance(data["recent_enrichment_failures"], list)
        capture("ai_status", "status", resp)
```
Verify the onboard flow + header against `e2e/test_dashboard.py`. Make it genuinely pass.

- [ ] **Step 2: Run the e2e**

`scripts/e2e_run.sh` — the new test passes and all existing AI e2e stay green (stub accrues 0 tokens, so no enrichment is ever skipped). Capture written under `e2e/_captures/ai_status/`.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_ai_status.py e2e/_captures/ai_status
git commit -m "test(ai): live e2e for GET /ai/status"
```

---

### Task 8: Docs — FE guide, SOP, checklist

**Files:**
- Create: `docs/fe-integration-guide-ai-status.md`, `docs/sop/2026-09-21-llm-budget-ai-status.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: FE guide**

Create `docs/fe-integration-guide-ai-status.md`: document `GET /api/v1/ai/status` (auth: bearer +
X-Workspace-Id), paste the `status` capture body **verbatim**, explain each field, and state: when
`over_budget` is true, AI personalization is paused until `resets_at` and new enrichments show the
templated fallback (existing AI text unaffected); `recent_enrichment_failures` is observability;
`daily_budget` is a server config (`null` = unlimited). Verification table.

- [ ] **Step 2: SOP**

Create `docs/sop/2026-09-21-llm-budget-ai-status.md` matching the existing SOP format. Key decisions:
`llm_usage_daily` ledger + migration `0030`; `LLM_DAILY_TOKEN_BUDGET` default 15000 (`≤0`=unlimited);
actual usage via `last_usage_tokens`; metering helper; skip-and-keep-templated on over-budget;
check-once + per-section debit for the plan generator; workspace-level `/ai/status` (no per-field
status); stub accrues 0 so tests/e2e unaffected. Note it **closes 2 of the 3 Slice-1 infra items**;
the 3rd (non-OpenAI provider) is **dropped won't-do (OpenAI-only)**. Reference commits + PR.

- [ ] **Step 3: Checklist**

In `docs/checklist/PROJECT_CHECKLIST.md`, update the Module 03 deferred-follow-ups bullet:
- Mark **per-workspace budget/rate-limiting** and the **enrichment-status signal** as ✅ done
  (this slice), with the PR ref.
- Mark the **non-OpenAI/Anthropic provider implementation** as **won't-do (OpenAI-only, 2026-09-21)** —
  strike it / move it to a "won't-do" note, not a pending follow-up.
- Module 03 was already marked complete; keep counts consistent. Add a dated note that its last two
  deferred infra items are now shipped and the provider item is dropped, so Module 03 has **no
  remaining open follow-ups**. Reconcile every current-facing line; leave frozen "_Previously:_" blocks.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs(llm): FE guide, SOP, checklist for LLM budget + AI status"
```

---

## Final: full local CI reproduction

```bash
poetry run black --check . && poetry run isort --check-only . && poetry run ruff check . && \
poetry run mypy app && poetry run pylint app && poetry run bandit -r app -q && \
poetry run pytest && poetry run alembic upgrade head && poetry run alembic check && \
scripts/e2e_run.sh
```
Confirm single alembic head (`poetry run alembic heads` → `0030_llm_usage_daily`). Fix any failure and re-run until clean. Then open the PR into `develop` with a clean body (no AI attribution).

## Self-Review notes (author)

- **Spec coverage:** ledger+config+migration (T1), seam usage (T2), metering helper (T3), enforce 8 handlers (T4), plan handler (T5), `/ai/status` (T6), e2e (T7), docs (T8). ✔
- **No placeholders:** code steps have real code; T4's existing-test reconciliation and T6's fixture/dep names are flagged as verify-against-real-harness (named files), not TODOs. ✔
- **Cross-task consistency:** `metered_complete*`, `over_budget`, `debit`, `is_ai_enrichment_job`, `LlmUsageDaily`, `LLM_DAILY_TOKEN_BUDGET`, `last_usage_tokens` names used identically across T2–T6. The key integration risk (existing handler tests monkeypatch `ai.get_llm_client`, now bypassed) is explicitly called out in T4. ✔
- **Ordering:** T2 (usage attr) before T3 (helper reads it); T3 before T4/T5/T6; T5 uses raw client+debit (not metered_complete) to avoid partial plans. ✔
