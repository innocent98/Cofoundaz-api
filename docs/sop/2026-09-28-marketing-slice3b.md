# SOP — Module 10 Marketing Hub, Slice 3b: Channel-Plan Recommender + Channel Fit Notes

**What shipped** — the second half of Module 10's AI layer: an **AI channel-mix recommender**
(objective + stage → an 8-channel percentage split that always sums to 100, plus a rationale) for
the campaign-create wizard, and **per-channel AI fit notes** (one LLM call → a short fit note for
each of the 8 fixed channels, persisted onto the channel rows and surfaced inline on `GET
/marketing/channels`). Both reuse Slice 3a's `marketing_ai_generations` table (two new `kind`
values, migration `0036`) and its identical async `POST → 202 → poll` pattern, metered against the
same `llm_budget` guard. Also closes a Slice 3a review follow-up: `plan_week_schema()`'s `channel`
field now enum-constrains to `ChannelKey`, so plan-week entries (and the stub-provider capture) are
no longer silently empty.

Commits (branch `feat/module-10-marketing-slice3b`, off Slice 3a's merge point (PR #102, base
`d7dd84e`); not yet merged, no PR opened yet), oldest to newest:
`754044a` (design spec) → `e053177` (implementation plan) →
`39eb2d3` (`channel_plan`/`channel_fit` kinds + `marketing_channels.ai_fit_note`/
`fit_note_generated_at` columns, migration `0036_channel_fit_notes`) →
`fc689e3` (`channel_plan`/`channel_fit` prompt/schema builders; enum-constrain plan-week's
`channel` field) →
`3f8f552` (`create_channel_plan_generation`/`create_channel_fit_generation` services +
`ChannelPlanRequest` schema + `ChannelResponse` extension) →
`dcc8d42` (`ai.marketing.channel_plan`/`ai.marketing.channel_fit` worker handlers +
`normalize_channel_mix`) →
`cfe2bde` (fix: reject non-finite `channel_plan` percentages; test `channel_fit` key filtering) →
`769aeb4` (channel-plan + fit-notes endpoints, 202/poll, RBAC) →
`bc56aee` (e2e journey + 5 captures, refreshed `plan_week_ready.json`) → this docs commit.

## Why

Slice 3a shipped the first half of the AI layer (copy generation + plan-week) on a unified,
kind-discriminated `marketing_ai_generations` table. Two PRD sub-features were left for this slice:
**10.3** (the campaign create wizard's channel-split recommendation — *"For {objective} at your
stage, I'd put {split} — here's why"*) and **10.5** (a fit note per channel card on the Channels
page). Slice 3a's whole-branch review also flagged a live gap: `plan_week_schema()`'s `channel`
field had no `enum` constraint, so a real provider's entries could theoretically return an invalid
channel and get silently dropped, and — more visibly — the stub provider's placeholder value for
an unconstrained string field never matched a real `ChannelKey`, so `output.entries` came back
empty in every stub/e2e/staging run. This slice fixes both: it adds the two new AI features on the
existing seam, and closes the plan-week gap as a one-line schema change riding along.

**Deliberately deferred** (per the design spec's D5 and Out of scope): the Overview
`ai_content_ideas` count — the least-defined AI surface in the PRD, left for a later tiny
follow-up, not this slice; SEO Tools (Slice 4); Performance Analytics + campaign `metrics`/
`top_channel_by_conversions` (Slice 5); a recommend history-list endpoint; a server-side link
between a channel-plan recommendation and the campaign it informs (FE applies the split
client-side, same pattern as Slice 3a's Write-with-AI); inline "Refine" on a generation's output.

## How

**Two more `kind` values on the same table, not two new tables.** `MarketingGenerationKind` gains
`channel_plan` and `channel_fit` (both fit the existing 12-char `kind` column — no enum-length
migration needed). Same reasoning as Slice 3a's original one-table decision (D3): both new flows
share the exact same `status`/`inputs`/`output`/`error`/timestamp shape and the exact same
`generating → ready/failed` lifecycle as `copy`/`plan_week`, so extending the discriminator avoids
a second copy of that machinery. Routes stay per-kind (`get_generation(..., kind=...)` scopes by
`id + startup_id + kind`), so a `channel_plan` id polled via the fit-notes route (or vice versa)
404s exactly like a cross-tenant id would — identical convention to Slice 3a's copy/plan-week
kind-mismatch.

**Channel-plan recommend is deliberately standalone, not tied to a campaign** (design decision
D1). The wizard's channel-split step needs a recommendation *before* a draft campaign necessarily
exists yet; `ChannelPlanRequest(objective, budget)` carries everything the worker needs, with
stage/industry read server-side from the caller's `Startup` row (never from the request body, same
convention as every other AI generation in this API). The FE applies the returned `channel_mix` to
the wizard's sliders and moves on — no server-side link between the recommendation and whatever
campaign eventually gets saved.

**Fit notes: one LLM call for all 8 channels, persisted onto the channel rows, not just returned
in the poll response** (design decision D2). A single `channel_fit` generation asks the model for
one fit note per channel in one call — cheaper than 8 separate generations, and gives the model
cross-channel context (it can reason "you're already active on X, so Y complements it" in one
pass). `handle_marketing_channel_fit` then writes each valid key's note onto that
`MarketingChannel` row's new `ai_fit_note`/`fit_note_generated_at` columns, so the FE's primary
read path is the pre-existing `GET /marketing/channels` (now carrying two more fields per card),
not a poll of the generation id — the generation id/poll route exists for progress-tracking
during the async wait, not as the notes' permanent home.

**Fit notes lazy-seed before the budget check, on purpose.** `handle_marketing_channel_fit` calls
`list_channels` (Slice 1's seed-on-first-read helper) before `metered_complete_json`, so a
workspace with no channel rows yet gets all 8 seeded even on a run that then fails over-budget —
seeding is idempotent and free, and skipping it would mean an over-budget failure leaves the
Channels page still unseeded. This ordering is called out as an explicit accepted decision in a
code comment (`app/worker/handlers/marketing_ai.py`), not an oversight.

**A `ChannelKey` the model's response omits keeps its previous note — it is never wiped.**
`handle_marketing_channel_fit` only writes rows for keys present and valid in the model's `notes`
response; a key the model drops (or an unknown key it invents) leaves that channel's existing
`ai_fit_note` untouched. This matters because a single bad/partial model response should degrade
gracefully (some channels get fresher notes, others keep their last good one) rather than blanking
out notes that took a previous successful run to produce.

**`normalize_channel_mix` guarantees the FE always gets exactly 100, never "close to 100."**
`channel_plan_schema()` enumerates the 8 `ChannelKey`s as object keys (so the stub emits
syntactically valid keys) but leaves each value a plain `integer` — the model (or the stub) can
return non-sequential/zero/malformed percentages. The worker's `normalize_channel_mix` keeps only
finite, non-negative, non-bool values at valid keys, scales the kept values proportionally, and
uses largest-remainder rounding to land the total at exactly 100; if nothing survives filtering
(all-zero, all-invalid, or empty), it falls back to an even split across all 8 channels
(`divmod(100, 8)`, 4 channels get 13 and 4 get 12). This fallback is what the stub-provider capture
actually exercises live — see the FE guide's boxed note and its Follow-ups below. A review round
(`cfe2bde`) caught and fixed a bug in the first cut: the reject guard only checked `v < 0`, which
lets through `float('nan')`/`float('inf')` (neither is `< 0`) and later crashes `int(scaled)`;
fixed by adding an explicit `math.isfinite(v)` check ordered after the `isinstance` check so a
string never reaches `isfinite`.

**Plan-week's fix is schema-only, no `StubLLMClient` change** (design decision D4).
`plan_week_schema()`'s `channel` property gained `"enum": [<the 8 ChannelKey values>]`.
`StubLLMClient._stub_value` already special-cased `"enum" in node` (returning `node["enum"][0]`)
before this slice — it simply never had an enum to honor on this field. With the constraint
present, the stub now emits `"organic_social"` (the first `ChannelKey` value) instead of its
generic `"[stub-llm] channel"` placeholder, so `handle_marketing_plan_week`'s existing
`ChannelKey`-membership filter (unchanged) now lets entries through. This turned Slice 3a's
documented "empty under stub" quirk into "non-empty, single-entry, `organic_social`-tagged under
stub" — real, useful test/demo data instead of a permanently-empty array.

## What's involved

**Migration `0036_channel_fit_notes`** (chains off `0035_marketing_ai_generations`, sole alembic
head) — two nullable columns added to the existing `marketing_channels` table (Slice 1): `ai_fit_note`
(`Text`, nullable) and `fit_note_generated_at` (`DateTime(timezone=True)`, nullable). No backfill,
brief `ADD COLUMN` only; reversible downgrade drops both.

**Enums** — `app/db/models/enums.py`: `MarketingGenerationKind` gains `channel_plan`/`channel_fit`
(both fit the existing 12-char column, no migration needed for the enum itself).

**Models** — `app/db/models/marketing.py` (extended): `MarketingChannel.ai_fit_note`,
`MarketingChannel.fit_note_generated_at`.

**Prompt builders** — `app/services/marketing/ai_prompts.py` (extended): `channel_plan_schema`,
`build_channel_plan_messages(*, objective, stage, industry, budget)`, `channel_fit_schema`,
`build_channel_fit_messages(*, stage, industry, statuses)`; `plan_week_schema()`'s `channel`
property gained an `enum` constraint (the review-follow-up fix).

**Schemas** — `app/schemas/marketing.py` (extended): `ChannelPlanRequest(objective, budget)`;
`ChannelResponse` extended with `ai_fit_note: str | None`, `fit_note_generated_at: datetime | None`.

**Service** — `app/services/marketing/ai_content.py` (extended): `create_channel_plan_generation`
(flush + enqueue `ai.marketing.channel_plan`), `create_channel_fit_generation` (flush + enqueue
`ai.marketing.channel_fit`, no input); both reuse the existing `get_generation`/
`serialize_generation` unchanged.

**Workers** — `app/worker/handlers/marketing_ai.py` (extended): `normalize_channel_mix` (pure
helper, largest-remainder rounding + even-split fallback), `handle_marketing_channel_plan`,
`handle_marketing_channel_fit` — both flush-only, both reuse the shared `_load`/`_fail_over_budget`
helpers from Slice 3a; registered as `ai.marketing.channel_plan` / `ai.marketing.channel_fit`.

**Endpoints** — `app/api/v1/endpoints/marketing.py` (extended): 4 new routes —
`POST /marketing/channel-plan/recommend` (202), `GET
/marketing/channel-plan/recommendations/{id}` (poll), `POST /marketing/channels/fit-notes/generate`
(202), `GET /marketing/channels/fit-notes/{id}` (poll) — all behind the existing `_marketing =
require_role(founder, team_member)` instance; `GET /marketing/channels` needed no code change
(`ChannelResponse`'s new fields surface automatically via `model_validate(from_attributes=True)`).

**Tests**
- `tests/db/test_marketing_channel_fit_columns.py` + `tests/test_marketing_slice3b_migration.py`
  (4) — new columns persist, new `kind` values exist, single head `0036`, upgrade/downgrade
  round-trips.
- `tests/services/marketing/test_ai_prompts.py` (10 total, 5 new) — `channel_plan_schema`/
  `channel_fit_schema` shape + message-builder content; plan-week's new `enum` constraint.
- `tests/services/marketing/test_ai_content_service.py` (9 total, 2 new) — create-channel-plan
  (enqueues + scopes), create-channel-fit.
- `tests/worker/test_marketing_ai_handlers.py` (15 total, 11 new) —
  `normalize_channel_mix` (scales to 100 + drops invalid keys, even split when nothing valid,
  rejects bool/str/negative/non-finite values, largest-remainder rounding lands exactly on 100),
  channel-plan happy path + over-budget, channel-fit writes notes onto rows + seeds + keeps a
  prior note when a key is omitted + drops an invalid key + over-budget.
- `tests/api/test_marketing_ai.py` + `tests/api/test_marketing.py` (6 new across both) — 202/poll
  for both new kinds, kind-mismatch 404, RBAC 403 across all 4 new routes including both `{id}` GET
  routes, `GET /channels` returns the two new fields.
- `e2e/test_marketing.py::test_marketing_channel_ai_journey` (new) — channel-plan recommend →
  drain → poll ready → fit-notes generate → drain → poll ready → `GET /channels` shows the notes
  inline, 5 live captures under `e2e/_captures/marketing/`; `plan_week_ready.json` (Slice 3a's
  capture) refreshed in the same e2e run to reflect the enum fix.

**Errors / API surface — additive only** (no existing Slice 1/2/3a route/shape changed except
`ChannelResponse`, which only gained two new nullable fields): `VALIDATION_ERROR` (422) on
missing/invalid `objective`, negative `budget`; `FORBIDDEN` (403) for non-marketing roles;
`NOT_FOUND` (404) for missing, cross-tenant, or kind-mismatched generation ids. A budget-exhausted
generation is **not** an HTTP error — it's a 200 poll response with `status: "failed"`,
`error: "over_budget"`, same as every other AI generation in this API.

**Docs**
- `docs/fe-integration-guide-marketing-channel-ai.md` (new) — both POST-then-poll flows, every
  field, the stub-provider even-split `channel_mix` quirk documented explicitly and inline, the
  `notes`-vs-`ai_fit_note` field-nesting trap, the `over_budget` failure (cross-ref
  `docs/fe-integration-guide-ai-status.md`), the kind-mismatch 404, RBAC, verification table.
- `docs/fe-integration-guide-marketing-copy.md` (updated) — plan-week section + verification-table
  row updated for the now-populated `output.entries` under stub; refreshed `plan_week_ready.json`
  referenced; the old "empty under stub" caveat removed and replaced with the fix explanation.
- `docs/checklist/PROJECT_CHECKLIST.md` (reconciled — see below).

## Verification

**Per-task unit verification (green before the e2e task), oldest to newest:**
- Task 1 (enums + channel columns + migration `0036`): `tests/db/test_marketing_channel_fit_columns.py`
  + `tests/test_marketing_slice3b_migration.py` — **4 passed**. `alembic upgrade head` clean,
  `alembic check` reports no drift, `alembic heads` — single head `0036_channel_fit_notes`.
- Task 2 (prompt/schema builders + plan-week enum fix): `tests/services/marketing/test_ai_prompts.py`
  — **10 passed** (5 pre-existing + 5 new).
- Task 3 (generation services + schemas): `tests/services/marketing/test_ai_content_service.py` —
  **9 passed** (7 pre-existing + 2 new). Regression: `tests/services/marketing/` — 47 passed;
  `tests/api/test_marketing.py tests/api/test_marketing_ai.py tests/api/test_marketing_campaigns.py`
  — 59 passed.
- Task 4 (worker handlers + `normalize_channel_mix`): `tests/worker/test_marketing_ai_handlers.py`
  — **13 passed** initially, then **15 passed** after a same-branch fix round (`cfe2bde`) added
  `test_normalize_rejects_nan_and_inf` and `test_channel_fit_drops_invalid_key_from_notes` in
  response to the non-finite-percentage bug the review round caught.
- Task 5 (endpoints + RBAC + channels serializer): `tests/api/test_marketing_ai.py
  tests/api/test_marketing.py` — **60 passed** (6 new: 5 in `test_marketing_ai.py`, 1 in
  `test_marketing.py`).
- Task 6 (e2e journey + captures): `bash scripts/e2e_run.sh` — **56 passed, 0 failed**, all e2e
  journeys including the new `test_marketing_channel_ai_journey`; `plan_week_ready.json` refreshed
  live in the same run. No Resend-quota flake this run.
- `poetry run black`/`isort`/`ruff check`/`mypy` clean on every touched file, each task.
- `alembic heads` — single linear head (`0036_channel_fit_notes`) confirmed after the migration
  task; no drift between the ORM models and the applied migration.

**Live e2e (`bash scripts/e2e_run.sh`, full suite):**

```
e2e/test_marketing.py::test_marketing_channel_ai_journey PASSED
...
56 passed in 43.75s
```

The new journey: onboard a founder → `POST /channel-plan/recommend` (captures
`channel_plan_accepted.json`) → drain the in-process worker → `GET
/channel-plan/recommendations/{id}` (captures `channel_plan_ready.json` — the even-split
fallback, live proof `normalize_channel_mix`'s all-zero path actually fires under the stub) →
`POST /channels/fit-notes/generate` (captures `fit_notes_accepted.json`) → drain → `GET
/channels/fit-notes/{id}` (captures `fit_notes_ready.json`) → `GET /marketing/channels` (captures
`channels_with_fit_notes.json`, confirming the inline `ai_fit_note`/`fit_note_generated_at`
surfacing). The same full run refreshed Slice 3a's `plan_week_ready.json` (now showing one
populated entry, `channel: "organic_social"`), live-proving the enum fix.

**Honest gap disclosure.** The e2e journey exercises only the happy-path ready state for both new
kinds, under the stub LLM provider — the `over_budget`/`failed` path, the omitted-key/invalid-key
fit-note filtering, the lazy-seed-before-budget-check ordering, and the RBAC 403 matrix are
unit/integration-tested but not e2e-captured; the stub provider also means `channel_mix` is
captured as an even split (not a differentiated recommendation) and `channel_fit`/`channel_plan`
text output is always the `"[stub-llm]"` placeholder. All of these are called out explicitly in
the new FE guide's verification table rather than silently presented as live-verified.

## Operate / roll back

**New deploy-time requirement: none.** No new background job type infrastructure — both handlers
run inside the existing worker process via the existing job dispatcher/runner, same as every other
`ai.*` job in this API. No new container, no new config beyond the LLM seam's existing settings
(already deployed for Module 03 / Slice 3a).

**Rollback:** revert this slice's commits as a unit (`754044a..bc56aee`, plus this docs commit) and
downgrade the migration (`poetry run alembic downgrade 0035_marketing_ai_generations`) to drop the
two `marketing_channels` columns. Safe — no other feature reads `ai_fit_note`/
`fit_note_generated_at` yet, and the two new `MarketingGenerationKind` values are additive (no
existing row uses them). Downgrade the migration only *after* the app code is already rolled back,
not before, same rollback ordering note as every other slice in this module.

## Follow-ups

**Slices 3c–5 of Module 10, deferred by design, not gaps in this slice:**
- **`Overview.ai_content_ideas`** stays an explicit `null` — deferred per design decision D5 as
  "the least-defined AI surface" in the PRD; a small follow-up slice, not committed to a specific
  future slice number here.
- **Slice 4 — SEO Tools.**
- **Slice 5 — Performance Analytics.** Fills `Overview.top_channel_by_conversions` and campaign
  `metrics` (currently always `{}`).

**Pre-existing gap noted, not fixed here (out of scope per design decision D5):**
- **`Overview.active_campaigns` stays `null`** even though Slice 2 shipped campaigns —
  `app/services/marketing/service.py`'s `overview()` builder never wires a live count for this
  field; it's a Slice-2 leftover the Slice 3b design spec explicitly flagged as noticed-but-out-
  of-scope, not something this slice's diff touches. Whichever slice picks it up next should wire
  a real `Campaign.status == active` count, same shape as `active_channels`' existing live count.

**Deferred within Slice 3b itself:**
- **No history-list endpoint for `channel_plan` or `channel_fit`** (design decision D1/D2 — YAGNI;
  the FE applies a result to the wizard sliders or the Channels page and moves on, unlike copy's
  `GET /copy/generations`).
- **No server-side link between a channel-plan recommendation and the campaign it informs** — pure
  client-side orchestration, same pattern Slice 3a established for Write-with-AI/Save-to-calendar.
- **Inline "Refine" remains unbuilt** — carried over from Slice 3a, still no endpoint to iterate on
  an existing generation's output.
- **Stub-provider `channel_mix` is always the even-split fallback, never a differentiated
  recommendation** — `channel_plan_schema()`'s per-channel values are plain `integer`s with no
  `enum`, so the stub always emits `0` for each and `normalize_channel_mix`'s all-zero path always
  fires. Unlike the plan-week fix, there is no schema-only fix available here: an `enum` constraint
  only works on values with a small fixed vocabulary (like `channel` name strings), not on an
  arbitrary integer percentage — making the stub emit a "realistic" varied mix would need a
  `StubLLMClient` change (e.g. a per-property default overridable in the schema), not attempted
  here since it would touch the stub's general contract, out of this slice's scope. Real-provider
  behavior is unaffected.