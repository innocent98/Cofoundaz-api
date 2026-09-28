# Module 10 — Marketing Hub, Slice 3b: AI channel-plan recommender + channel fit notes

**Status:** design approved (2026-09-28), ready for implementation planning.

## Context

Module 10 (Marketing Hub) is being built in slices. Merged to `develop`:

- **Slice 1** — Content Calendar + Channels + Overview CRUD spine (migration `0033`).
- **Slice 2** — Campaigns + Audience Segments (migration `0034`).
- **Slice 3a** — the first half of the AI layer: AI **copy** generation + **plan-week**, on the
  `marketing_ai_generations` table (migration `0035`), the async `POST → 202 → poll` job pattern,
  metered via `llm_budget` (`error: "over_budget"` on exhaustion). Kinds `copy` / `plan_week`.

**Slice 3b** is the second half of the AI layer:

1. **AI channel-plan recommender** (PRD 10.3, campaign create wizard step 3 — *"For {objective} at
   your stage, I'd put {split} — here's why."*).
2. **Per-channel AI fit notes** (PRD 10.5, Channels page — one fit note per channel card).
3. **Deferred fix from the Slice 3a whole-branch review:** constrain plan-week's `channel` field
   to the `ChannelKey` enum so a real provider's entries can't be silently dropped and the stub
   provider stops returning empty `entries`.

Deferred out of this slice (unchanged): the Overview `ai_content_ideas` count (its meaning is the
least-defined AI surface; a later tiny follow-up), SEO Tools (Slice 4), Performance Analytics +
the campaign `metrics`/`top_channel_by_conversions`/`ai_content_ideas` fills (Slice 5).

## Goal

Ship two new async AI features on the existing Slice-3a seam — a standalone channel-mix
recommender and a one-shot per-channel fit-note generator that persists onto channel rows — plus
the one-line plan-week schema hardening, with the same tenancy, RBAC, transaction, and metering
guarantees as every other AI consumer in this API.

## Global constraints (carried, verbatim where they bind)

- **Async AI pattern:** `POST` returns **202 ACCEPTED** `{id, status:"generating"}` after
  `db.flush()` + `job_dispatcher.enqueue(...)` + `db.commit()` (the endpoint commits). The worker
  `db.get(row)`, guards `status != generating → return`, calls `metered_complete_json`, and on
  `None` (over budget) sets terminal `status="failed"`, `error="over_budget"`, `output={}` and
  returns; otherwise writes `output` + `status="ready"`. Handlers registered via
  `register_handler(...)` and imported in `app/worker/__main__.py`.
- **RBAC:** every member route behind `_marketing = require_role(MembershipRole.founder,
  MembershipRole.team_member)`; other roles → 403 before any row lookup.
- **Tenancy:** generations scoped by `startup_id` **and** `kind` (cross-tenant / kind-mismatch →
  `NotFound`, never a leak). `startup_id`, stage, industry are read from the caller's membership /
  startup, never from the request body.
- **Transactions:** services `db.flush()` only; endpoints `db.commit()`; worker handlers own their
  unit (the runner wraps each job in a SAVEPOINT).
- **Metering:** `metered_complete_json` (returns `None` on over-budget); `settings.LLM_MAX_TOKENS`.
- **`AppError` exposes `.http_status`** (not `.status_code`); `get_db` does **not** auto-commit.
- **CodeQL (required check):** no mutating call inside an `assert` (bind to a variable first); no
  implicit string concatenation inside a list literal.
- **No AI attribution** in any commit message, PR body, or review comment.

## Design

### 1. Channel-plan recommender

**Endpoint (member):**

- `POST /marketing/channel-plan/recommend` → **202** `{id, status:"generating"}`.
  Body `ChannelPlanRequest`: `objective: CampaignObjective` (required), `budget: int | None`
  (optional, cents; only flavors the rationale). Stage + industry read from the startup.
- `GET /marketing/channel-plan/recommendations/{id}` → poll; returns the full generation
  (`GenerationResponse` shape from 3a). No history/list endpoint (YAGNI — the FE applies the split
  to the draft campaign's sliders and moves on).

**Job:** `ai.marketing.channel_plan`, handler `handle_marketing_channel_plan`.

- Prompt built from objective + stage + industry (+ budget if given).
- `channel_plan_schema()`: `{"channel_mix": {object of the 8 ChannelKey → integer percent},
  "rationale": string}`. The `channel_mix` keys are enumerated in the schema so the stub emits
  valid keys.
- Worker post-processing: keep only entries whose key is a valid `ChannelKey`; **normalize the
  kept percentages to sum to 100** (integer largest-remainder rounding; if the model returns all
  zeros / nothing valid, fall back to an even split across the 8 channels). Output:
  `{"channel_mix": {ChannelKey: percent}, "rationale": str}` where the percents sum to 100.
- Over-budget → terminal `failed` / `over_budget` (shared `_fail_over_budget` helper from 3a).

### 2. Per-channel fit notes

**Endpoint (member):**

- `POST /marketing/channels/fit-notes/generate` → **202** `{id, status}`. No body.
- `GET /marketing/channels/fit-notes/{id}` → poll the `channel_fit` generation.
- The existing **`GET /marketing/channels`** now returns `ai_fit_note` + `fit_note_generated_at`
  inline on each channel card (both `null` until a run completes).

**Job:** `ai.marketing.channel_fit`, handler `handle_marketing_channel_fit`.

- One LLM call produces all 8 notes: `channel_fit_schema()` → `{"notes": {ChannelKey → string}}`
  (keys enumerated). Grounded in stage + industry + each channel's current `status`.
- Worker: lazy-seed the 8 channels first (reuse `list_channels`' SAVEPOINT seeding), then for each
  returned key that is a valid `ChannelKey`, set that `MarketingChannel.ai_fit_note` = note and
  `fit_note_generated_at = now()`. Invalid keys dropped. The generation `output` also stores
  `{"notes": {ChannelKey: note}}` for the poll response.
- Over-budget → terminal `failed` / `over_budget`.

**Storage — migration `0036_marketing_channel_fit_notes`:** add to `marketing_channels`:
`ai_fit_note` (`Text`, nullable) and `fit_note_generated_at` (`timestamptz`, nullable). No lock
beyond the brief `ALTER TABLE ADD COLUMN` (nullable, no default backfill). Reversible downgrade
drops both columns. This is the single new head off `0035_marketing_ai_generations`.

### 3. Plan-week enum fix (Slice 3a review follow-up)

- `plan_week_schema()`: the `channel` field gains `"enum": [<the 8 ChannelKey values>]`.
- **No `StubLLMClient` change needed** — `_stub_value` already returns `node["enum"][0]` when a
  field declares an `enum`. With the enum present, the stub emits a valid `ChannelKey`
  (`organic_social`), so plan-week entries now survive the worker's ChannelKey filter and
  `output.entries` is non-empty under the stub.
- Update the 3a FE guide (`docs/fe-integration-guide-marketing-copy.md`) and the affected e2e
  capture (`plan_week_ready.json`) to reflect the now-populated entries; drop the "empty under
  stub" caveat.

### New enums

`MarketingGenerationKind` gains `channel_plan` (12 chars) and `channel_fit` (11 chars). Both fit
the existing `kind` `Enum(native_enum=False, length=12)` column — **no enum-length migration**.
`MarketingGenerationStatus` (generating/ready/failed) is reused unchanged.

## Data flow, tenancy, transactions

Identical to Slice 3a. `create_channel_plan_generation` / `create_channel_fit_generation` in the
service layer `flush` + enqueue; the endpoints `commit`. `get_generation(..., kind=channel_plan |
channel_fit)` scopes by `id + startup_id + kind`. The `channel_fit` worker writes channel rows
within its own job transaction (the runner's SAVEPOINT). No add-then-select-in-same-transaction
patterns (the only pre-write read, channel seeding, precedes the writes).

## Components / files

- `app/db/models/enums.py` — two new `MarketingGenerationKind` members.
- `app/db/models/marketing.py` — two new `MarketingChannel` columns.
- `alembic/versions/0036_marketing_channel_fit_notes.py` — new migration (single head).
- `app/services/marketing/ai_prompts.py` — `channel_plan_schema()` +
  `build_channel_plan_messages(...)`; `channel_fit_schema()` + `build_channel_fit_messages(...)`;
  add the `enum` to `plan_week_schema()`.
- `app/services/marketing/ai_content.py` — `create_channel_plan_generation`,
  `create_channel_fit_generation`; reuse `get_generation`, `serialize_generation`.
- `app/services/marketing/service.py` — `list_channels` serializer/model already returns rows;
  extend `serialize_channel` (or the endpoint serializer) to include the two new fields; a
  `_normalize_percentages(...)` helper (or place it in the worker).
- `app/schemas/marketing.py` — `ChannelPlanRequest`; extend the channel response schema with
  `ai_fit_note` + `fit_note_generated_at`.
- `app/worker/handlers/marketing_ai.py` — `handle_marketing_channel_plan` +
  `handle_marketing_channel_fit`; register both; reuse `_load` + `_fail_over_budget`.
- `app/api/v1/endpoints/marketing.py` — the 4 new routes (2 POST 202, 2 GET poll); channels GET
  serializer includes the new fields.
- Docs: FE guide `docs/fe-integration-guide-marketing-channel-ai.md` (new; the two flows, verbatim
  from captures); update `docs/fe-integration-guide-marketing-copy.md` + the plan-week capture;
  SOP `docs/sop/2026-09-28-marketing-slice3b.md`; `docs/checklist/PROJECT_CHECKLIST.md`.

## Review focus (inputs the spec implies but a task's happy-path tests may miss)

- **channel_plan normalization:** model returns percentages that don't sum to 100, or include an
  invalid key, or all zeros → output must still sum to 100 over valid `ChannelKey`s (even-split
  fallback when nothing valid). Owning task tests this directly.
- **channel_fit invalid/missing keys:** a returned key outside `ChannelKey` is dropped, not
  written; a `ChannelKey` the model omits keeps its previous `ai_fit_note` (or stays null) rather
  than being wiped. Re-running overwrites with a fresh `fit_note_generated_at`.
- **channel_fit lazy-seed:** generating fit notes for a startup with no channel rows yet seeds all
  8 first, then writes.
- **Tenancy/kind-mismatch:** a `channel_plan` id polled via the `channel_fit` route → 404, and
  vice-versa; cross-tenant id → 404.
- **RBAC:** 403 for mentor/investor on all 4 new routes.
- **Over-budget:** both workers → terminal `failed` / `over_budget`, `output={}`.
- **plan-week enum:** under the stub provider, plan-week `output.entries` is now non-empty and each
  entry's `channel` is a valid `ChannelKey`.

## Decisions (rulings settled during brainstorming)

- **D1 — recommend is standalone** (objective in body), not tied to a `campaign_id`: works
  mid-wizard before the draft is saved; the FE applies the split to the sliders.
- **D2 — fit notes: one job, all 8, persisted on channel rows** (new columns), pollable via a
  `channel_fit` generation; `GET /channels` returns them inline. One LLM call = cheaper + coherent
  cross-channel reasoning.
- **D3 — reuse `marketing_ai_generations`** with two new kinds; no new generations table.
- **D4 — enum fix is schema-only** (StubLLMClient already honors `enum`).
- **D5 — `ai_content_ideas` deferred**; the pre-existing `active_campaigns: None` gap in the
  Overview builder is a Slice-2 leftover, noted but out of scope for 3b.

## Out of scope

`ai_content_ideas` count; SEO Tools; Performance Analytics; campaign `metrics`; recommend history
list; a server-side link between a recommendation and the campaign it informs (FE applies the
split client-side); inline "Refine" on a generation.
