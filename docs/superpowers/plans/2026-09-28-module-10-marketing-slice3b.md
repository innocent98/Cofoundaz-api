# Module 10 Marketing Hub — Slice 3b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an AI channel-plan recommender and a per-channel AI fit-note generator to the Marketing Hub on the existing async AI seam, and constrain plan-week's `channel` to the `ChannelKey` enum.

**Architecture:** Two new `marketing_ai_generations` kinds (`channel_plan`, `channel_fit`) on the Slice-3a `POST → 202 → poll` pattern, metered via `llm_budget`. `channel_plan` returns a normalized `{channel_mix, rationale}`; `channel_fit` runs one LLM call for all 8 channels and persists each note onto its `MarketingChannel` row (two new nullable columns, migration `0036`). Plan-week's schema gains a `channel` enum (the stub client already honors `enum`).

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2.0 (typed `Mapped`), Alembic, Postgres, Redis, Poetry, pytest.

**Spec:** docs/superpowers/specs/2026-09-28-module-10-marketing-slice3b-design.md

## Global Constraints

- **Async AI pattern:** POST returns **202 ACCEPTED** `{id, status:"generating"}` after `db.flush()` + `job_dispatcher.enqueue(...)`; the **endpoint** calls `db.commit()`. Worker `_load` guards `status != generating → return`; `metered_complete_json` → on `None` (over budget) `_fail_over_budget` (terminal `failed`, `error="over_budget"`) + return; else write `output` + `status="ready"`; `db.flush()` only (no commit). Register via `register_handler(...)`; import the handler module in `app/worker/__main__.py`.
- **RBAC:** every member route behind `_marketing = require_role(MembershipRole.founder, MembershipRole.team_member)`; other roles → 403 before any row lookup.
- **Tenancy:** `get_generation` scopes by `id + startup_id + kind` (cross-tenant / kind-mismatch → `NotFound`). `startup_id`, stage, industry come from the membership/startup, never the request body.
- **Transactions:** services `db.flush()` only; endpoints `db.commit()`; worker handlers own their unit.
- **Metering:** `metered_complete_json(db, startup_id, messages, schema=..., max_tokens=settings.LLM_MAX_TOKENS)` returns `None` on over-budget.
- **`AppError.http_status`** (not `.status_code`); `get_db` does **not** auto-commit.
- **CodeQL (required check):** never put a mutating call (`client.post/patch/delete`) inside an `assert` — bind to a variable first; no implicit string concatenation inside a list literal.
- **No AI attribution** in any commit message.
- Migration id ≤ 32 chars (this plan uses `0036_channel_fit_notes`).

## Review Focus

- **channel_plan normalization:** model returns percentages not summing to 100, an invalid key, or all-zeros → output still sums to 100 over valid `ChannelKey`s (even-split fallback when nothing valid). — Task 4.
- **channel_fit invalid/omitted keys:** a returned key outside `ChannelKey` is dropped, not written; a `ChannelKey` the model omits keeps its prior `ai_fit_note` rather than being wiped. — Task 4.
- **channel_fit lazy-seed:** a startup with no channel rows yet gets all 8 seeded before notes are written. — Task 4.
- **kind-mismatch / tenancy:** a `channel_plan` id polled via the `channel_fit` route → 404, and vice-versa; other tenant's id → 404. — Task 5.
- **plan-week enum under stub:** stub plan-week `output.entries` is now non-empty and every entry's `channel` is a valid `ChannelKey`. — Task 2 (schema) + Task 6 (capture).

---

## Task 1: Enums + channel columns + migration 0036

**Files:**
- Modify: `app/db/models/enums.py` (add two `MarketingGenerationKind` members)
- Modify: `app/db/models/marketing.py` (`MarketingChannel`: two new columns)
- Create: `alembic/versions/0036_channel_fit_notes.py`
- Test: `tests/test_marketing_slice3b_migration.py`, `tests/db/test_marketing_channel_fit_columns.py`

**Interfaces:**
- Produces: `MarketingGenerationKind.channel_plan` (`"channel_plan"`, 12 chars), `MarketingGenerationKind.channel_fit` (`"channel_fit"`, 11 chars); `MarketingChannel.ai_fit_note: Mapped[str | None]`, `MarketingChannel.fit_note_generated_at: Mapped[datetime | None]`.

- [ ] **Step 1: Write the failing model column test**

```python
# tests/db/test_marketing_channel_fit_columns.py
from datetime import UTC, datetime

from app.db.models.enums import ChannelKey, ChannelStatus, MarketingGenerationKind
from app.db.models.marketing import MarketingChannel
from tests.factories import create_startup, create_user


def test_channel_fit_note_columns_persist(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = MarketingChannel(
        startup_id=s.id,
        key=ChannelKey.search,
        status=ChannelStatus.not_started,
        ai_fit_note="High fit: your buyers research heavily.",
        fit_note_generated_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.ai_fit_note == "High fit: your buyers research heavily."
    assert row.fit_note_generated_at is not None


def test_new_generation_kinds_exist():
    assert MarketingGenerationKind.channel_plan.value == "channel_plan"
    assert MarketingGenerationKind.channel_fit.value == "channel_fit"
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/db/test_marketing_channel_fit_columns.py -v`
Expected: FAIL (`AttributeError`/`TypeError` on `ai_fit_note`; `channel_plan` missing).

- [ ] **Step 3: Add the enum members**

In `app/db/models/enums.py`, `class MarketingGenerationKind`:

```python
class MarketingGenerationKind(enum.StrEnum):
    copy = "copy"
    plan_week = "plan_week"
    channel_plan = "channel_plan"
    channel_fit = "channel_fit"
```

- [ ] **Step 4: Add the model columns**

In `app/db/models/marketing.py`, `class MarketingChannel`, after `notes`:

```python
    ai_fit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_note_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

Ensure `from datetime import datetime` and `from sqlalchemy import DateTime, Text` are imported (Text already is; add `DateTime` if missing).

- [ ] **Step 5: Create the migration**

`alembic/versions/0036_channel_fit_notes.py`:

```python
"""marketing channel fit notes

Revision ID: 0036_channel_fit_notes
Revises: 0035_marketing_ai_generations
Create Date: 2026-09-28

Adds two nullable columns to marketing_channels for the per-channel AI fit note
(Module 10 Slice 3b): ai_fit_note (the note text) and fit_note_generated_at (when
it was last generated). Both nullable, no backfill — brief ADD COLUMN only.
"""

from alembic import op
import sqlalchemy as sa

revision = "0036_channel_fit_notes"
down_revision = "0035_marketing_ai_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketing_channels", sa.Column("ai_fit_note", sa.Text(), nullable=True))
    op.add_column(
        "marketing_channels",
        sa.Column("fit_note_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("marketing_channels", "fit_note_generated_at")
    op.drop_column("marketing_channels", "ai_fit_note")
```

- [ ] **Step 6: Write the migration round-trip + single-head test**

```python
# tests/test_marketing_slice3b_migration.py
import subprocess


def _alembic(*args: str) -> subprocess.CompletedProcess:
    cmd = ["poetry", "run", "alembic", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def test_single_head_is_0036():
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    assert "0036_channel_fit_notes" in result.stdout
    assert result.stdout.count("(head)") == 1


def test_upgrade_then_downgrade_round_trips():
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "0035_marketing_ai_generations")
    assert down.returncode == 0, down.stderr
    reup = _alembic("upgrade", "head")
    assert reup.returncode == 0, reup.stderr
```

- [ ] **Step 7: Run tests + alembic drift check**

Run: `poetry run pytest tests/db/test_marketing_channel_fit_columns.py tests/test_marketing_slice3b_migration.py -v && poetry run alembic upgrade head && poetry run alembic check`
Expected: PASS; `alembic check` reports "No new upgrade operations detected."

- [ ] **Step 8: Commit**

```bash
git add app/db/models/enums.py app/db/models/marketing.py alembic/versions/0036_channel_fit_notes.py tests/test_marketing_slice3b_migration.py tests/db/test_marketing_channel_fit_columns.py
git commit -m "feat(marketing): channel fit-note columns + channel_plan/channel_fit kinds (migration 0036)"
```

---

## Task 2: Prompt + schema builders (+ plan-week enum fix)

**Files:**
- Modify: `app/services/marketing/ai_prompts.py`
- Test: `tests/services/marketing/test_ai_prompts.py` (extend)

**Interfaces:**
- Consumes: `LLMMessage` (already imported); `ChannelKey` values.
- Produces:
  - `channel_plan_schema() -> dict` — object `{channel_mix: {8 ChannelKey int properties}, rationale: string}`.
  - `build_channel_plan_messages(*, objective: str, stage: str | None, industry: str | None, budget: int | None) -> list[LLMMessage]`.
  - `channel_fit_schema() -> dict` — object `{notes: {8 ChannelKey string properties}}`.
  - `build_channel_fit_messages(*, stage: str | None, industry: str | None, statuses: dict[str, str]) -> list[LLMMessage]` (`statuses` maps ChannelKey → current ChannelStatus value).
  - `plan_week_schema()` — `channel` field gains `"enum"` of the 8 ChannelKey values.
- `CHANNEL_KEYS: list[str]` module constant (the 8 values) to avoid repetition.

- [ ] **Step 1: Write failing tests**

```python
# add to tests/services/marketing/test_ai_prompts.py
from app.db.models.enums import ChannelKey
from app.services.marketing.ai_prompts import (
    build_channel_fit_messages,
    build_channel_plan_messages,
    channel_fit_schema,
    channel_plan_schema,
    plan_week_schema,
)

_KEYS = [c.value for c in ChannelKey]


def test_channel_plan_schema_enumerates_the_eight_channels():
    schema = channel_plan_schema()
    mix = schema["properties"]["channel_mix"]["properties"]
    assert sorted(mix.keys()) == sorted(_KEYS)
    assert all(v["type"] == "integer" for v in mix.values())
    assert "rationale" in schema["properties"]


def test_channel_fit_schema_enumerates_the_eight_channels():
    notes = channel_fit_schema()["properties"]["notes"]["properties"]
    assert sorted(notes.keys()) == sorted(_KEYS)


def test_plan_week_channel_is_enum_constrained():
    item = plan_week_schema()["properties"]["entries"]["items"]
    assert sorted(item["properties"]["channel"]["enum"]) == sorted(_KEYS)


def test_channel_plan_messages_carry_objective_and_stage():
    msgs = build_channel_plan_messages(
        objective="leads", stage="mvp", industry="fintech", budget=50000
    )
    joined = " ".join(m.content for m in msgs)
    assert "leads" in joined and "mvp" in joined


def test_channel_fit_messages_carry_statuses():
    msgs = build_channel_fit_messages(
        stage="mvp", industry="fintech", statuses={"search": "active"}
    )
    joined = " ".join(m.content for m in msgs)
    assert "search" in joined and "active" in joined
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/marketing/test_ai_prompts.py -v`
Expected: FAIL (ImportError on the new builders; plan-week `channel` has no `enum`).

- [ ] **Step 3: Implement the builders + enum fix**

In `app/services/marketing/ai_prompts.py`, add near the top after imports:

```python
from app.db.models.enums import ChannelKey

CHANNEL_KEYS = [c.value for c in ChannelKey]
```

Change `plan_week_schema()`'s `channel` property from `{"type": "string"}` to:

```python
                        "channel": {"type": "string", "enum": CHANNEL_KEYS},
```

Add:

```python
def channel_plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "channel_mix": {
                "type": "object",
                "properties": {k: {"type": "integer"} for k in CHANNEL_KEYS},
                "required": CHANNEL_KEYS,
                "additionalProperties": False,
            },
            "rationale": {"type": "string"},
        },
        "required": ["channel_mix", "rationale"],
        "additionalProperties": False,
    }


def build_channel_plan_messages(
    *, objective: str, stage: str | None, industry: str | None, budget: int | None
) -> list[LLMMessage]:
    system = (
        "You are a startup growth strategist. Recommend how to split marketing effort across "
        "these eight channels: " + ", ".join(CHANNEL_KEYS) + ". Return an integer percentage for "
        "every channel (0 allowed); the percentages should sum to about 100. Also give a short "
        "rationale a founder can act on. Weight the split for the campaign objective and stage."
    )
    budget_line = f" Monthly budget (cents): {budget}." if budget is not None else ""
    user = (
        f"Objective: {objective}. Stage: {stage or 'early'}. "
        f"Industry: {industry or 'unspecified'}.{budget_line}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def channel_fit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "notes": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in CHANNEL_KEYS},
                "required": CHANNEL_KEYS,
                "additionalProperties": False,
            }
        },
        "required": ["notes"],
        "additionalProperties": False,
    }


def build_channel_fit_messages(
    *, stage: str | None, industry: str | None, statuses: dict[str, str]
) -> list[LLMMessage]:
    system = (
        "You are a marketing channel advisor. For each of these eight channels, write ONE short "
        "fit note (one or two sentences) telling this founder how well the channel fits their "
        "business and why. Channels: " + ", ".join(CHANNEL_KEYS) + "."
    )
    status_line = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())) or "all not_started"
    user = (
        f"Stage: {stage or 'early'}. Industry: {industry or 'unspecified'}. "
        f"Current channel statuses: {status_line}."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/services/marketing/test_ai_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/marketing/ai_prompts.py tests/services/marketing/test_ai_prompts.py
git commit -m "feat(marketing): channel_plan + channel_fit prompt/schema builders; enum-constrain plan-week channel"
```

---

## Task 3: Service — create generations + schemas + channel serialization

**Files:**
- Modify: `app/services/marketing/ai_content.py`
- Modify: `app/schemas/marketing.py` (`ChannelPlanRequest`; extend `ChannelResponse`)
- Test: `tests/services/marketing/test_ai_content_service.py` (extend)

**Interfaces:**
- Consumes: `MarketingGenerationKind.channel_plan|channel_fit`; `job_dispatcher.enqueue`; `MarketingAiGeneration`.
- Produces:
  - `ChannelPlanRequest(objective: CampaignObjective, budget: int | None = None)`.
  - `ChannelResponse` gains `ai_fit_note: str | None = None`, `fit_note_generated_at: datetime | None = None`.
  - `create_channel_plan_generation(db, *, startup_id, created_by, data: ChannelPlanRequest) -> MarketingAiGeneration` (enqueues `ai.marketing.channel_plan`).
  - `create_channel_fit_generation(db, *, startup_id, created_by) -> MarketingAiGeneration` (enqueues `ai.marketing.channel_fit`).

- [ ] **Step 1: Write failing service tests**

```python
# add to tests/services/marketing/test_ai_content_service.py
from app.db.models.enums import CampaignObjective, MarketingGenerationKind, MarketingGenerationStatus
from app.schemas.marketing import ChannelPlanRequest
from app.services.marketing.ai_content import (
    create_channel_fit_generation,
    create_channel_plan_generation,
    get_generation,
)
from tests.factories import create_startup, create_user


def test_create_channel_plan_generation_enqueues_and_scopes(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = create_channel_plan_generation(
        db, startup_id=s.id, created_by=u.id,
        data=ChannelPlanRequest(objective=CampaignObjective.leads, budget=50000),
    )
    db.flush()
    assert g.kind == MarketingGenerationKind.channel_plan
    assert g.status == MarketingGenerationStatus.generating
    assert g.inputs["objective"] == "leads"
    assert g.inputs["budget"] == 50000
    got = get_generation(
        db, startup_id=s.id, generation_id=g.id, kind=MarketingGenerationKind.channel_plan
    )
    assert got.id == g.id


def test_create_channel_fit_generation(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = create_channel_fit_generation(db, startup_id=s.id, created_by=u.id)
    db.flush()
    assert g.kind == MarketingGenerationKind.channel_fit
    assert g.inputs == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/services/marketing/test_ai_content_service.py -v`
Expected: FAIL (ImportError on the new functions / `ChannelPlanRequest`).

- [ ] **Step 3: Add the schemas**

In `app/schemas/marketing.py`, add (near `CopyGenerateRequest`):

```python
class ChannelPlanRequest(BaseModel):
    objective: CampaignObjective
    budget: int | None = Field(default=None, ge=0)
```

Extend `ChannelResponse`:

```python
class ChannelResponse(BaseModel):
    id: uuid.UUID
    key: ChannelKey
    status: ChannelStatus
    notes: str | None
    ai_fit_note: str | None = None
    fit_note_generated_at: datetime | None = None
```

Ensure `CampaignObjective` is imported in `app/schemas/marketing.py` (it is used by campaign schemas already; add to the enums import if missing) and `datetime` is imported.

- [ ] **Step 4: Add the service functions**

In `app/services/marketing/ai_content.py`, add `ChannelPlanRequest` to the `app.schemas.marketing` import, then:

```python
def create_channel_plan_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID, data: ChannelPlanRequest
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=MarketingGenerationKind.channel_plan,
        inputs=data.model_dump(mode="json"),
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.marketing.channel_plan",
        {"generation_id": str(g.id), "startup_id": str(startup_id)},
        startup_id,
    )
    return g


def create_channel_fit_generation(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID
) -> MarketingAiGeneration:
    g = MarketingAiGeneration(
        startup_id=startup_id,
        created_by=created_by,
        kind=MarketingGenerationKind.channel_fit,
        inputs={},
        status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job_dispatcher.enqueue(
        db,
        "ai.marketing.channel_fit",
        {"generation_id": str(g.id), "startup_id": str(startup_id)},
        startup_id,
    )
    return g
```

- [ ] **Step 5: Run to verify it passes**

Run: `poetry run pytest tests/services/marketing/test_ai_content_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/marketing/ai_content.py app/schemas/marketing.py tests/services/marketing/test_ai_content_service.py
git commit -m "feat(marketing): channel-plan/channel-fit generation services + schemas"
```

---

## Task 4: Worker handlers (+ percentage normalization)

**Files:**
- Modify: `app/worker/handlers/marketing_ai.py`
- Modify: `app/worker/__main__.py` (import already covers this module — verify)
- Test: `tests/worker/test_marketing_ai_handlers.py` (extend)

**Interfaces:**
- Consumes: `_load`, `_fail_over_budget` (already in the module); `build_channel_plan_messages`, `channel_plan_schema`, `build_channel_fit_messages`, `channel_fit_schema`; `list_channels`; `ChannelKey`, `Startup`, `MarketingChannel`.
- Produces: `handle_marketing_channel_plan`, `handle_marketing_channel_fit`, `normalize_channel_mix(raw: dict) -> dict[str, int]` (module-level, pure). Registers `ai.marketing.channel_plan` + `ai.marketing.channel_fit`.

- [ ] **Step 1: Write failing normalization + handler tests**

```python
# add to tests/worker/test_marketing_ai_handlers.py
from datetime import UTC, datetime

from app.db.models.enums import (
    ChannelKey,
    ChannelStatus,
    MarketingGenerationKind,
    MarketingGenerationStatus,
)
from app.db.models.marketing import MarketingAiGeneration, MarketingChannel
from app.worker.handlers.marketing_ai import (
    handle_marketing_channel_fit,
    handle_marketing_channel_plan,
    normalize_channel_mix,
)
from tests.factories import create_startup, create_user
# reuse the file's existing helpers for building a Job + draining; mirror the copy/plan_week tests.


def test_normalize_scales_to_100_and_drops_invalid_keys():
    out = normalize_channel_mix({"search": 30, "email": 30, "not_a_channel": 40})
    assert set(out) <= {c.value for c in ChannelKey}
    assert sum(out.values()) == 100
    assert "not_a_channel" not in out


def test_normalize_even_split_when_nothing_valid():
    out = normalize_channel_mix({})
    assert sum(out.values()) == 100
    assert len(out) == 8


def test_channel_fit_writes_notes_onto_rows_and_seeds(db):
    # stub provider returns "[stub-llm] <key>" for each notes.<key> string.
    u = create_user(db)
    s = create_startup(db, owner=u)
    g = MarketingAiGeneration(
        startup_id=s.id, created_by=u.id, kind=MarketingGenerationKind.channel_fit,
        inputs={}, status=MarketingGenerationStatus.generating,
    )
    db.add(g)
    db.flush()
    job = _make_job(db, "ai.marketing.channel_fit", {"generation_id": str(g.id), "startup_id": str(s.id)}, s.id)
    handle_marketing_channel_fit(db, job)
    db.flush()
    rows = {c.key: c for c in db.query(MarketingChannel).filter_by(startup_id=s.id).all()}
    assert len(rows) == 8  # lazy-seeded
    assert rows[ChannelKey.search].ai_fit_note is not None
    assert rows[ChannelKey.search].fit_note_generated_at is not None
    assert g.status == MarketingGenerationStatus.ready
```

(Use the file's real `_make_job`/drain helper — copy the exact form the existing copy/plan_week handler tests use in this file; do not invent a new one. If they build the `Job` inline, do the same.)

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/worker/test_marketing_ai_handlers.py -v`
Expected: FAIL (ImportError on the new handlers / `normalize_channel_mix`).

- [ ] **Step 3: Implement the handlers + normalization**

In `app/worker/handlers/marketing_ai.py`, extend the imports:

```python
from datetime import UTC, datetime

from app.db.models.marketing import AudienceSegment, MarketingAiGeneration, MarketingChannel
from app.services.marketing.ai_prompts import (
    build_channel_fit_messages,
    build_channel_plan_messages,
    build_copy_messages,
    build_plan_week_messages,
    channel_fit_schema,
    channel_plan_schema,
    copy_schema,
    plan_week_schema,
)
from app.services.marketing.service import list_channels
```

Add the pure helper and two handlers:

```python
def normalize_channel_mix(raw: dict) -> dict[str, int]:
    """Keep only valid ChannelKeys, coerce to non-negative ints, and scale to sum 100
    (largest-remainder rounding). Even split across all 8 channels if nothing valid."""
    valid = [c.value for c in ChannelKey]
    cleaned = {}
    for k in valid:
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            continue
        cleaned[k] = float(v)
    total = sum(cleaned.values())
    if total <= 0:
        base, extra = divmod(100, len(valid))
        return {k: base + (1 if i < extra else 0) for i, k in enumerate(valid)}
    scaled = {k: (v / total) * 100 for k, v in cleaned.items()}
    floored = {k: int(v) for k, v in scaled.items()}
    remainder = 100 - sum(floored.values())
    # hand the remaining points to the largest fractional parts
    order = sorted(scaled, key=lambda k: scaled[k] - floored[k], reverse=True)
    for k in order[:remainder]:
        floored[k] += 1
    return floored


def handle_marketing_channel_plan(db: Session, job: Job) -> None:
    """Recommend a channel mix + rationale for an objective/stage. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    inp = g.inputs
    result = metered_complete_json(
        db,
        g.startup_id,
        build_channel_plan_messages(
            objective=inp.get("objective", ""),
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
            budget=inp.get("budget"),
        ),
        schema=channel_plan_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        _fail_over_budget(db, g)
        return
    g.output = {
        "channel_mix": normalize_channel_mix(result.get("channel_mix") or {}),
        "rationale": str(result.get("rationale") or ""),
    }
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.channel_plan", handle_marketing_channel_plan)


def handle_marketing_channel_fit(db: Session, job: Job) -> None:
    """Generate one fit note per channel and persist onto the channel rows. No commit."""
    g = _load(db, job)
    if g is None:
        return
    startup = db.get(Startup, g.startup_id)
    if startup is None:
        return
    channels = list_channels(db, startup_id=g.startup_id)  # lazy-seeds all 8
    statuses = {c.key.value: c.status.value for c in channels}
    result = metered_complete_json(
        db,
        g.startup_id,
        build_channel_fit_messages(
            stage=(startup.stage.value if startup.stage else None),
            industry=startup.industry,
            statuses=statuses,
        ),
        schema=channel_fit_schema(),
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    if result is None:
        _fail_over_budget(db, g)
        return
    valid = {c.value for c in ChannelKey}
    notes = {k: str(v) for k, v in (result.get("notes") or {}).items() if k in valid}
    now = datetime.now(UTC)
    by_key = {c.key.value: c for c in channels}
    for key, note in notes.items():
        row = by_key.get(key)
        if row is not None:
            row.ai_fit_note = note
            row.fit_note_generated_at = now
    g.output = {"notes": notes}
    g.status = MarketingGenerationStatus.ready
    db.flush()


register_handler("ai.marketing.channel_fit", handle_marketing_channel_fit)
```

- [ ] **Step 4: Verify worker registration import**

Confirm `app/worker/__main__.py` imports `app.worker.handlers.marketing_ai` (it does for 3a; the new `register_handler` calls run on that same import — no change needed). If it imports specific names, ensure the module is imported.

- [ ] **Step 5: Run to verify it passes**

Run: `poetry run pytest tests/worker/test_marketing_ai_handlers.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/worker/handlers/marketing_ai.py tests/worker/test_marketing_ai_handlers.py
git commit -m "feat(marketing): ai.marketing.channel_plan + channel_fit worker handlers (normalize + persist notes)"
```

---

## Task 5: Endpoints (4 routes) + channels serializer

**Files:**
- Modify: `app/api/v1/endpoints/marketing.py`
- Test: `tests/api/test_marketing_ai.py` (extend) — new tests in this file
- Test: `tests/api/test_marketing.py` (channels GET now returns the two fields)

**Interfaces:**
- Consumes: `create_channel_plan_generation`, `create_channel_fit_generation`, `get_generation`, `serialize_generation`; `ChannelPlanRequest`; `MarketingGenerationKind`.
- Produces routes: `POST /marketing/channel-plan/recommend` (202), `GET /marketing/channel-plan/recommendations/{generation_id}`, `POST /marketing/channels/fit-notes/generate` (202), `GET /marketing/channels/fit-notes/{generation_id}`. `GET /marketing/channels` now returns `ai_fit_note` + `fit_note_generated_at` (via the extended `ChannelResponse`).

- [ ] **Step 1: Write failing endpoint tests**

```python
# add to tests/api/test_marketing_ai.py (reuse the file's _member/_headers/NON_MARKETING_ROLES)
def test_channel_plan_recommend_is_202_and_pollable(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/channel-plan/recommend", json={"objective": "leads"}, headers=h)
    assert r.status_code == 202, r.text
    gid = r.json()["data"]["id"]
    got = client.get(f"{BASE}/channel-plan/recommendations/{gid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["kind"] == "channel_plan"


def test_fit_notes_generate_is_202_and_pollable(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/channels/fit-notes/generate", headers=h)
    assert r.status_code == 202, r.text
    gid = r.json()["data"]["id"]
    got = client.get(f"{BASE}/channels/fit-notes/{gid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["kind"] == "channel_fit"


def test_channel_plan_kind_mismatch_404(client, db):
    _u, _s, h = _member(db)
    r = client.post(f"{BASE}/channels/fit-notes/generate", headers=h)
    gid = r.json()["data"]["id"]  # a channel_fit id
    wrong = client.get(f"{BASE}/channel-plan/recommendations/{gid}", headers=h)
    assert wrong.status_code == 404, wrong.text


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_channel_ai_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    fake = "00000000-0000-0000-0000-000000000000"
    posted = client.post(f"{BASE}/channel-plan/recommend", json={"objective": "leads"}, headers=h)
    assert posted.status_code == 403, posted.text
    got = client.get(f"{BASE}/channel-plan/recommendations/{fake}", headers=h)
    assert got.status_code == 403, got.text
    gen = client.post(f"{BASE}/channels/fit-notes/generate", headers=h)
    assert gen.status_code == 403, gen.text
    fit = client.get(f"{BASE}/channels/fit-notes/{fake}", headers=h)
    assert fit.status_code == 403, fit.text
```

Add to `tests/api/test_marketing.py` a check that `GET /channels` includes the new keys:

```python
def test_channels_list_includes_fit_note_fields(client, db):
    _u, _s, h = _member(db)  # reuse this file's member helper
    resp = client.get("/api/v1/marketing/channels", headers=h)
    assert resp.status_code == 200, resp.text
    card = resp.json()["data"][0]
    assert "ai_fit_note" in card and "fit_note_generated_at" in card
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/api/test_marketing_ai.py tests/api/test_marketing.py -v -k "channel_plan or fit_note or channel_ai"`
Expected: FAIL (routes 404 / missing keys).

- [ ] **Step 3: Implement the routes**

In `app/api/v1/endpoints/marketing.py`, add `ChannelPlanRequest` to the `app.schemas.marketing` import and add these routes (mirror the copy/plan-week blocks exactly):

```python
@router.post(
    "/channel-plan/recommend", status_code=status.HTTP_202_ACCEPTED, response_model=dict[str, Any]
)
def recommend_channel_plan(
    payload: ChannelPlanRequest,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.create_channel_plan_generation(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    db.commit()
    return success_response({"id": str(g.id), "status": g.status.value})


@router.get("/channel-plan/recommendations/{generation_id}", response_model=dict[str, Any])
def get_channel_plan(
    generation_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.get_generation(
        db,
        startup_id=membership.startup_id,
        generation_id=generation_id,
        kind=MarketingGenerationKind.channel_plan,
    )
    return success_response(ai_content_svc.serialize_generation(g).model_dump())


@router.post(
    "/channels/fit-notes/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict[str, Any],
)
def generate_fit_notes(
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.create_channel_fit_generation(
        db, startup_id=membership.startup_id, created_by=user.id
    )
    db.commit()
    return success_response({"id": str(g.id), "status": g.status.value})


@router.get("/channels/fit-notes/{generation_id}", response_model=dict[str, Any])
def get_fit_notes(
    generation_id: uuid.UUID,
    membership: Membership = Depends(_marketing),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    g = ai_content_svc.get_generation(
        db,
        startup_id=membership.startup_id,
        generation_id=generation_id,
        kind=MarketingGenerationKind.channel_fit,
    )
    return success_response(ai_content_svc.serialize_generation(g).model_dump())
```

**Route-ordering note:** register `GET /channels/fit-notes/{generation_id}` **before** the existing `PATCH /channels/{key}` is fine (different methods), but ensure the literal path `/channels/fit-notes/generate` and `/channels/fit-notes/{generation_id}` are declared — a `{key}` GET does not exist, so no shadowing. The `ChannelResponse` change (Task 3) makes `GET /channels` return the new fields with no endpoint edit.

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/api/test_marketing_ai.py tests/api/test_marketing.py -v`
Expected: PASS. Confirm no mutating call sits inside an `assert` (every `client.post` is bound first).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/endpoints/marketing.py tests/api/test_marketing_ai.py tests/api/test_marketing.py
git commit -m "feat(marketing): channel-plan recommend + fit-notes endpoints (202 + poll) + RBAC"
```

---

## Task 6: E2E journey + captures (+ refreshed plan-week capture)

**Files:**
- Modify: `e2e/test_marketing.py`
- Produces/updates: `e2e/_captures/marketing/` — new channel-plan + fit-note captures; refreshed `plan_week_ready.json` (now non-empty).

**Interfaces:**
- Consumes: the file's real request helpers (`c.post`/`c.get`, `_auth_header`/`_onboard_steps`, the `capture(group, name, resp)` fixture) and the `_drain()` worker-drain helper — copy the exact form the Slice-3a AI journey (`test_marketing_ai_generation_journey`) already uses in this file.

- [ ] **Step 1: Extend the live journey**

Add a `test_marketing_channel_ai_journey` modeled on `test_marketing_ai_generation_journey`:

```python
    plan = post(c, "/api/v1/marketing/channel-plan/recommend", {"objective": "leads"})
    capture("marketing", "channel_plan_accepted", plan)
    pid = plan["data"]["id"]
    _drain()
    plan_ready = get(c, f"/api/v1/marketing/channel-plan/recommendations/{pid}")
    assert plan_ready["data"]["status"] in ("ready", "failed")
    capture("marketing", "channel_plan_ready", plan_ready)

    fit = post(c, "/api/v1/marketing/channels/fit-notes/generate", {})
    capture("marketing", "fit_notes_accepted", fit)
    fid = fit["data"]["id"]
    _drain()
    fit_ready = get(c, f"/api/v1/marketing/channels/fit-notes/{fid}")
    capture("marketing", "fit_notes_ready", fit_ready)
    channels = get(c, "/api/v1/marketing/channels")
    capture("marketing", "channels_with_fit_notes", channels)
```

Use the file's real `c`/helper names (adapt `post`/`get` to the actual helper style in the file). Keep mutating calls out of `assert`.

- [ ] **Step 2: Run e2e**

Run: `bash scripts/e2e_run.sh`
Expected: the channel-AI journey passes; new captures written; the existing plan-week capture (`plan_week_ready.json`) is regenerated **non-empty** now that plan-week's `channel` is enum-constrained. Ensure Homebrew `postgresql@17`/`redis` are stopped (Docker owns 5432/6379).

- [ ] **Step 3: Commit (only marketing captures + the test)**

```bash
git add e2e/test_marketing.py e2e/_captures/marketing/
git commit -m "test(e2e): marketing channel-plan + fit-notes journey with captures; refresh plan-week capture"
```

(Restore any other module's capture churn with `git checkout -- e2e/_captures/<other>` before committing.)

---

## Task 7: Docs — FE guide + SOP + checklist

**Files:**
- Create: `docs/fe-integration-guide-marketing-channel-ai.md`
- Modify: `docs/fe-integration-guide-marketing-copy.md` (drop the plan-week "empty under stub" caveat; entries are now populated)
- Create: `docs/sop/2026-09-28-marketing-slice3b.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`

- [ ] **Step 1: FE guide** — `docs/fe-integration-guide-marketing-channel-ai.md`: the two POST→202→poll flows (`channel-plan/recommend` → `{channel_mix (8 ChannelKeys→percent, sums 100), rationale}`; `channels/fit-notes/generate` → per-channel notes, also surfaced inline on `GET /marketing/channels` as `ai_fit_note`/`fit_note_generated_at`), the `generating/ready/failed` states + `over_budget` failure (cross-ref `docs/fe-integration-guide-ai-status.md`), auth (founder/team_member → 403), kind-mismatch 404. **Payloads verbatim from the Task-6 captures** (`channel_plan_ready.json`, `fit_notes_ready.json`, `channels_with_fit_notes.json`); stub `[stub-llm]` values labelled. Verification table at the end.

- [ ] **Step 2: Update the copy guide** — in `docs/fe-integration-guide-marketing-copy.md`, update the plan-week section + verification-table row: `output.entries` is now populated under the stub (each `channel` a valid `ChannelKey`); remove the "empty under stub" caveat and point at the refreshed `plan_week_ready.json`.

- [ ] **Step 3: SOP** — `docs/sop/2026-09-28-marketing-slice3b.md`: what shipped (channel-plan recommender + per-channel fit notes, migration `0036`, two `ai.marketing.channel_*` workers, plan-week enum fix), why (Module 10 Slice 3b — second half of the AI layer; closes the 3a plan-week enum follow-up), how (standalone recommend, one-job fit notes persisted onto channel rows, normalize-to-100, business.plan.generate 202/poll pattern, metered), files/migrations touched, verification (unit + e2e), follow-ups (Slice 3b leaves `ai_content_ideas` + SEO + analytics to 3c/4/5). Match the existing SOP style.

- [ ] **Step 4: Checklist** — in `docs/checklist/PROJECT_CHECKLIST.md`, update the Module 10 section: mark **Slice 3b complete**; note the plan-week enum follow-up as resolved; keep Slice 4/5 planned; move the plan-week "empty under stub" deferred bullet to resolved.

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs(marketing): FE guide + SOP + checklist for Module 10 Slice 3b"
```

---

## Final verification (before opening the PR)

- [ ] **Full local CI parity, all green** (ensure Homebrew pg/redis stopped, Docker up):

```bash
poetry run black --check app tests && poetry run isort --check-only app tests && \
poetry run ruff check app tests && poetry run mypy app && \
poetry run pylint app --fail-under=9.5 && poetry run bandit -q -r app && \
poetry run pytest --cov=app --cov-fail-under=95 && \
poetry run alembic upgrade head && poetry run alembic check && poetry run alembic heads && \
bash scripts/e2e_run.sh
```

Expected: every check passes; `alembic heads` shows the single head `0036_channel_fit_notes`. (The 5 Resend-429 `auth`/`onboarding` tests are a known external-quota flake, green in CI.)

- [ ] **No AI attribution:** `git log develop..HEAD --format='%an <%ae>%n%b'` shows none.
- [ ] **CodeQL clean:** no mutating call inside an `assert`, no implicit string concat in a list literal, anywhere new.

---

## Self-Review

**Spec coverage:** channel-plan recommender → T1(kind)+T2(schema/prompt)+T3(service)+T4(worker)+T5(endpoint); fit notes → T1(columns/kind)+T2+T3(service/schema)+T4(worker persists)+T5(endpoint+channels serializer); plan-week enum fix → T2(schema)+T6(capture)+T7(docs); migration 0036 → T1; docs → T7. All spec decisions D1–D5 covered; `ai_content_ideas`/SEO/analytics correctly out of scope.

**Placeholder scan:** every code step carries real code; the only "use the file's real helper" notes are for T4's `_make_job`/drain and T6's request helpers, flagged as "copy the existing form," matching how Slice 3a's plan handled them.

**Type consistency:** `create_channel_plan_generation`/`create_channel_fit_generation`/`get_generation(...,kind)`, `ChannelPlanRequest(objective, budget)`, `normalize_channel_mix`, `build_channel_plan_messages(*, objective, stage, industry, budget)`, `channel_plan_schema`/`channel_fit_schema`, `build_channel_fit_messages(*, stage, industry, statuses)` are consistent across producing (T2/T3) and consuming (T4/T5) tasks. Job types `ai.marketing.channel_plan`/`ai.marketing.channel_fit` + payload `generation_id` match enqueue (T3), handler (T4), register (T4). `ChannelResponse` fields `ai_fit_note`/`fit_note_generated_at` match the model columns (T1) and endpoint (T5).

**Review Focus:** normalization (T4), invalid/omitted fit-note keys (T4), lazy-seed (T4), kind-mismatch/tenancy (T5), plan-week enum under stub (T2+T6) — each has an owning task's test.
