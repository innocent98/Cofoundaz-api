# Module 10 — Marketing Hub, Slice 3a: AI content generation (design)

**Status:** approved-for-planning
**Date:** 2026-09-24
**Module:** 10 (Marketing Hub) — Slice 3a of 6 (the AI layer split into 3a content-generation + 3b advisory). Backend API only.
**Depends on:** the Module 03 LLM seam (`app/platform/llm.py`), the async job worker (`job_dispatcher` + `register_handler`), `llm_budget` metering, and Slices 1–2 (the marketing module, `ChannelKey`, `audience_segments`, `content_calendar`). All merged. Nothing here is blocked.

## Goal

Ship on-demand AI content generation for the Marketing Hub: an **AI Copy Generator** (3 variants per request, with history) and a **Plan-my-week** starter-calendar generator, both as async jobs on the `business.plan.generate` pattern. **Write-with-AI** in the calendar drawer reuses the copy generator (no new backend).

## Why

Third slice of Module 10 (after the CRUD spine + campaigns/segments). The two PRD-named AI jobs (`POST /marketing/copy/generate`, and Plan-my-week) are the "write it" half of the marketing AI layer; the "advise" half (channel-plan recommend + channel fit notes) is Slice 3b. Both jobs follow the established async-generation pattern, so this slice adds no new infrastructure — one table, two workers, a handful of endpoints.

## Decisions settled in brainstorming

| Decision | Choice | Consequence |
| --- | --- | --- |
| Generation storage | **One unified `marketing_ai_generations` table**, `kind`-discriminated (`copy` \| `plan_week`) | One `generating/ready/failed` status machine + JSONB `inputs`/`output`; easy to extend (e.g. S5 weekly-readout). |
| Over-budget behavior | **Terminal `failed` + `error="over_budget"`** | On-demand generation has no templated fallback; a clear terminal state beats an indefinite `generating`. `GET /ai/status` confirms `over_budget`. |
| Write-with-AI | **Reuse the copy generator** (no new endpoint) | The entry drawer calls `copy/generate` prefilled with the entry's channel; a chosen variant is saved via the existing `PATCH /calendar-entries/{id}`. |
| Refine (inline chat) | **Deferred** to a follow-up | Core is 3 variants + history; iterative refine is a chat protocol left for later. |
| asset_type / tone | **Validated enums** | `AssetType` (ad/social_post/email/landing_headline/product_description), `CopyTone` (bold/friendly/expert/playful). |
| `audience_segment_id` in copy inputs | **Optional, validated to belong to the startup** | Ties copy to an `audience_segments` row (S2); 422 if foreign/unknown. |

## Scope

### In scope
1. Enums: `MarketingGenerationKind`, `MarketingGenerationStatus`, `AssetType`, `CopyTone` (`app/db/models/enums.py`).
2. Model + migration `0035`: `MarketingAiGeneration` (`marketing_ai_generations`).
3. Schemas (extend `app/schemas/marketing.py`): copy-generate request, plan-week request, generation response.
4. Service `app/services/marketing/ai_content.py` (create generation + enqueue, get/list, tenancy) + prompt builders `app/services/marketing/ai_prompts.py` (copy + plan-week message/schema builders).
5. Worker handlers `app/worker/handlers/marketing_ai.py` (`ai.marketing.copy`, `ai.marketing.plan_week`) + import in `app/worker/__main__.py`.
6. Endpoints (extend `app/api/v1/endpoints/marketing.py`): copy generate/list/get, plan-week generate/get.
7. Tests (unit + e2e), FE guide, SOP, checklist.

### Out of scope (later slices / deferred)
- **Channel-plan recommend + channel fit notes → Slice 3b.**
- **Campaign weekly readout → Slice 5** (needs `marketing_metrics`).
- Inline **Refine** chat; A/B "ship two" tracking; char-count enforcement (the FE shows counters; the backend does not hard-truncate variants).
- Save-to-calendar / Plan-week "Add all" materialization — the FE uses the existing `POST /marketing/calendar-entries`; no new backend.

## Architecture

### Enums — `app/db/models/enums.py`
```python
class MarketingGenerationKind(enum.StrEnum):
    copy = "copy"
    plan_week = "plan_week"

class MarketingGenerationStatus(enum.StrEnum):
    generating = "generating"
    ready = "ready"
    failed = "failed"

class AssetType(enum.StrEnum):
    ad = "ad"
    social_post = "social_post"
    email = "email"
    landing_headline = "landing_headline"
    product_description = "product_description"

class CopyTone(enum.StrEnum):
    bold = "bold"
    friendly = "friendly"
    expert = "expert"
    playful = "playful"
```

### Data model — `app/db/models/marketing.py` (extend) + migration `0035`

`MarketingAiGeneration(UUIDMixin, TimestampMixin, Base)` → `marketing_ai_generations`:
- `startup_id` FK `startups.id` CASCADE, `index=True`.
- `created_by` FK `users.id` `ondelete="SET NULL"`, nullable, `index=True`.
- `kind` `Enum(MarketingGenerationKind, native_enum=False, length=12)`, non-null, `index=True` (history queries filter by kind).
- `inputs` `JSONB`, non-null, `default=dict` (the request params).
- `status` `Enum(MarketingGenerationStatus, native_enum=False, length=12)`, non-null, `default=generating`.
- `output` `JSONB`, non-null, `default=dict` (`{"variants": [...]}` for copy; `{"entries": [...]}` for plan_week).
- `error` `String(200)`, nullable (reason on `failed`, e.g. `"over_budget"`).

Registered in `app/db/models/__init__.py`. Migration `0035_marketing_ai_generations` (`down_revision="0034_campaigns_segments"`, single linear head).

### Prompt builders — `app/services/marketing/ai_prompts.py` (new)
- `copy_schema() -> dict` — strict JSON schema `{"variants": {type array, minItems 3, maxItems 3, items string}}`.
- `build_copy_messages(*, asset_type, channel, tone, key_message, cta, segment_name) -> list[LLMMessage]` — a system + user message producing 3 distinct on-brand variants for the asset type/channel/tone.
- `plan_week_schema() -> dict` — `{"entries": array (max ~7) of {title, channel (∈ ChannelKey), body, day_offset int 0–6}}`.
- `build_plan_week_messages(*, stage, industry, name) -> list[LLMMessage]` — a system + user message producing a 7-day starter calendar grounded in the startup's stage/industry.

### Service — `app/services/marketing/ai_content.py` (new)
- `create_copy_generation(db, *, startup_id, created_by, data: CopyGenerateRequest) -> MarketingAiGeneration` — validates `audience_segment_id` (if any) belongs to the startup (else 422); creates a `kind=copy`, `status=generating` row with `inputs=data.model_dump(mode="json")`; enqueues `ai.marketing.copy` `{generation_id, startup_id}`.
- `create_plan_week_generation(db, *, startup_id, created_by, data: PlanWeekRequest) -> MarketingAiGeneration` — creates a `kind=plan_week` row; enqueues `ai.marketing.plan_week`.
- `get_generation(db, *, startup_id, generation_id, kind) -> MarketingAiGeneration` — tenancy-scoped; `NotFound` on miss or `kind` mismatch (so the copy poll can't fetch a plan-week row).
- `list_copy_generations(db, *, startup_id) -> list[MarketingAiGeneration]` — `kind=copy`, newest first (the History tab).

### Worker handlers — `app/worker/handlers/marketing_ai.py` (new); import added to `app/worker/__main__.py`
Both follow the `business.plan.generate` shape: load the row; guard `status != generating → return`; call `metered_complete_json`; on `None` (over budget) set `status=failed`, `error="over_budget"`, `db.flush()`, return; else write `output`, `status=ready`, `db.flush()`. Real LLM errors propagate (fail-loud → the runner marks the job failed + bounded retries).

```
handle_marketing_copy: metered_complete_json(db, startup_id, build_copy_messages(**inputs_ctx),
    schema=copy_schema(), max_tokens=...) -> {"variants":[...]} ; store output={"variants": v[:3]}
handle_marketing_plan_week: metered_complete_json(..., schema=plan_week_schema()) ->
    {"entries":[...]} ; validate each entry's channel ∈ ChannelKey (drop invalid); store output
```
Registered: `register_handler("ai.marketing.copy", ...)`, `register_handler("ai.marketing.plan_week", ...)`.

### Endpoints — `app/api/v1/endpoints/marketing.py` (extend), `require_role(founder, team_member)`, commit before returning

| Method & path | Purpose |
| --- | --- |
| `POST /marketing/copy/generate` (202) | Start a copy generation → `{id, status}` |
| `GET /marketing/copy/generations` | History (kind=copy, newest first) |
| `GET /marketing/copy/generations/{id}` | Poll one copy generation |
| `POST /marketing/calendar/plan-week` (202) | Start a plan-week generation → `{id, status}` |
| `GET /marketing/calendar/plan-week/{id}` | Poll a plan-week generation |

Both `POST`s: create the row + `db.flush()` + `job_dispatcher.enqueue(...)` + `db.commit()` + return 202 `{id, status}` (mirrors `generate_plan`). Both `GET/{id}`s fetch via `get_generation(..., kind=...)`.

## Data flow / FE contract
- **Poll shape** (`GET …/{id}`): `{id, kind, status, inputs, output, error, created_at, updated_at}`. `status: generating` → keep polling; `ready` → render `output.variants` (copy) or `output.entries` (plan_week); `failed` → show `error` (`over_budget` → "AI budget reached; try again later — see `GET /ai/status`").
- **Copy `output.variants`**: exactly 3 strings. **Plan-week `output.entries`**: up to 7 `{title, channel, body, day_offset}`.
- **Save-to-calendar / Add-all**: the FE creates entries via `POST /marketing/calendar-entries` from a variant/proposal (existing S1 endpoint).
- **Write-with-AI**: the FE runs `copy/generate` from the entry drawer and saves a variant with `PATCH /marketing/calendar-entries/{id}` (existing S1 endpoint).

## Error handling / concurrency
- Tenancy on every read/write via `startup_id`; cross-tenant → `NotFound`. `kind` mismatch on a poll → `NotFound`.
- `audience_segment_id` foreign/unknown → `422` at create.
- Idempotent worker: a re-drained job finds `status != generating` and no-ops.
- Over budget → `status=failed`, `error="over_budget"` (no LLM call, via `metered_complete_json` returning `None`). Real LLM error → propagates (fail-loud), job retried per the runner.
- Endpoints `db.commit()`; service `db.flush()` only; worker no commit.

## Testing
- **Unit — model/migration:** columns + `kind` index; migration `0035` round-trip + drift-clean; single head.
- **Unit — service:** create copy/plan-week rows (status generating, inputs stored, one job enqueued of the right type); `audience_segment_id` foreign → 422; `get_generation` tenancy + kind-mismatch → NotFound; `list_copy_generations` returns only kind=copy newest-first.
- **Unit — prompt builders:** `copy_schema`/`plan_week_schema` shapes; messages include the inputs.
- **Unit — workers:** happy path (stub client → `output` filled, `status=ready`); over-budget (budget exhausted) → `status=failed`, `error="over_budget"`, no output; idempotency (status!=generating → no-op); plan-week drops entries with an invalid channel.
- **Unit — endpoints:** RBAC (mentor/investor → 403 on all 5 routes); 202 on the two POSTs; poll returns the shape; kind-mismatch poll → 404.
- **E2E (live, stub provider):** `POST copy/generate` → poll until `ready`, assert 3 variants (stub markers) → `GET generations` history; `POST plan-week` → poll → assert entries. Capture bodies to `e2e/_captures/marketing/`.
- Coverage ≥ 95%; DB-clean unit tests.
- **CodeQL test-hygiene:** no mutating call inside an `assert` (extract first); no implicit string concatenation in a list literal.

## Security & privacy
- Workspace/role-scoped (founder/team_member). Inputs are marketing copy briefs (no PII beyond what the founder types). Metering unchanged; `LLM_API_KEY` never logged. Generated variants are stored per-workspace.

## FE impact (integration guide)
New `docs/fe-integration-guide-marketing-copy.md`: the two POST-then-poll flows (202 + `GET/{id}` cadence), the `generating/ready/failed` states and the `over_budget` failure (cross-ref `docs/fe-integration-guide-ai-status.md`), the copy inputs (`AssetType`/`CopyTone`/channel/segment), the History list, the plan-week `entries` shape, and the "materialize via `POST /calendar-entries`" + "Write-with-AI = copy-gen + `PATCH` entry" compositions. Payloads verbatim from live captures (stub `[stub-llm]` values labelled).

## Global constraints (carried into the plan)
- **No AI attribution** anywhere.
- **Reproduce CI locally & green before push:** black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%, **Migrations (round-trip + drift) — one new migration `0035`, single head**, e2e, **CodeQL** (avoid the two flagged test patterns).
- Enum style `enum.StrEnum` + `Enum(Cls, native_enum=False, length=N)`; FK columns `index=True`; services `db.flush()` only, endpoints commit; worker handlers `db.flush()` only, fail-loud on real LLM errors (only budget-skip returns `None` → mapped to `failed`).
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads verbatim from live captures.

## Follow-ups
Slice 3b (channel-plan recommend + fit notes); inline Refine chat; Save-to-calendar convenience endpoint if the FE wants one server call; char-limit-aware variants; Slice 5 weekly-readout (reuses this generation table with a new `kind`).
