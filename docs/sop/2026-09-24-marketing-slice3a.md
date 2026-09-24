# SOP — Module 10 Marketing Hub, Slice 3a: AI Content Generation (Copy + Plan-Week)

**What shipped** — the content-generation half of Module 10's AI layer (Slice 3a of the 3-slice
AI Content Assistant, itself Slice 3 of 5 in the Marketing Hub): an **AI copy generator**
(asset-type + tone-directed ad/social/email copy, up to 3 variants) and an **AI weekly-plan
generator** ("Plan my week" — up to 7 proposed calendar entries), both served through the same
async `POST -> 202 -> poll` pattern every other AI feature in this API already uses, backed by one
unified, kind-discriminated table (`marketing_ai_generations`). Founder/team_member access only,
same RBAC dependency as Slices 1–2. Metered against the existing per-workspace `llm_budget`
guard — an exhausted daily budget fails the generation with `error: "over_budget"` rather than
silently degrading.

Commits (branch `feat/module-10-marketing-slice3a`, off Slice 2's merge point (PR #101); not yet
merged, no PR opened yet), oldest to newest:
`bf41466` (design spec) → `9c59afc` (implementation plan) →
`d7096e2` (`marketing_ai_generations` table + `MarketingGenerationKind`/`MarketingGenerationStatus`/
`AssetType`/`CopyTone` enums, migration `0035_marketing_ai_generations`) →
`03b77c3` (copy + plan-week prompt/schema builders, `app/services/marketing/ai_prompts.py`) →
`b7e55b2` (AI generation service: create-copy, create-plan-week, poll, copy history) →
`e5b782f` (`ai.marketing.copy` + `ai.marketing.plan_week` worker handlers) →
`fdde2b9` (refactor: dedupe the over-budget failure block shared by both handlers) →
`0d14afa` (copy + plan-week endpoints, 202/poll, RBAC) →
`9ac3dfc` (test: assert 403 on both `{id}` GET routes for non-marketing roles) →
`1ff400f` (e2e journey + 5 captures) → this docs commit.

## Why

Slices 1–2 shipped the Marketing Hub's CRUD spine (Content Calendar + Channels + Overview) and
campaign-planning layer (Campaigns + Audience Segments), leaving `Overview.ai_content_ideas`
(Slice 1) and the whole "AI Content Assistant" PRD sub-feature unbuilt. This slice is the first of
that AI layer: a founder can generate ready-to-edit copy for an asset (ad, social post, email,
landing headline, product description) in a chosen tone, or ask for a starter 7-day content
calendar tailored to their startup's stage/industry — both as background jobs the FE polls,
matching the `business.plan.generate`-style async pattern already established for every other
LLM-backed generation in this API, rather than a synchronous request that could time out on a slow
model call. Slice 3b (channel-plan recommend + fit notes for `channel_mix`) and inline "Refine" are
explicitly deferred to keep this slice's scope to the two generation flows the design spec named.

## How

**One unified, kind-discriminated table, not two.** `MarketingAiGeneration` carries a `kind`
column (`copy` | `plan_week`) rather than two separate tables — both flows share the exact same
`status`/`inputs`/`output`/`error`/timestamp shape, the exact same generating→ready/failed
lifecycle, and the exact same over-budget failure mode, so a single table with a discriminator
avoids duplicating that machinery. **Routes stay per-kind, though** (`/copy/generate` +
`/copy/generations/{id}` vs. `/calendar/plan-week` + `/calendar/plan-week/{id}`) — `get_generation`
(`app/services/marketing/ai_content.py:67-77`) filters by `kind` in the same query as `startup_id`,
so a `plan_week` id polled via the copy route (or vice versa) 404s exactly like a cross-tenant id
would, rather than serving the wrong kind's row through the wrong route. This was a deliberate
design-spec choice: one table for storage/lifecycle reuse, kind-scoped routes for a clean FE
contract (no need to disambiguate a generic `/generations/{id}` response by an extra field).

**The 202-body is intentionally thin.** Both `POST /copy/generate` and `POST
/calendar/plan-week` return only `{id, status: "generating"}` — not the full row — because the
row's `output` is empty at creation time anyway; the FE's only job at that point is to remember
`id` and start polling `GET …/{id}`, which returns the full `GenerationResponse` shape once ready.

**Over-budget failure reuses the existing `llm_budget` seam, not a new guard.**
`metered_complete_json` (`app/platform/llm_budget.py`, built in the Module 03 LLM-budget slice)
returns `None` when the workspace's daily token budget is exhausted; both worker handlers treat a
`None` result identically via one shared `_fail_over_budget` helper — `status = failed`, `error =
"over_budget"`, `output` stays at its schema default `{}`. `fdde2b9` extracted this shared block
out of the two handlers after the initial cut had it duplicated verbatim in both
(`handle_marketing_copy` and `handle_marketing_plan_week`) — same dedupe discipline as every other
review-driven cleanup in this repo.

**Copy truncates the model's variants to exactly 3; plan-week drops any entry with an invalid
channel.** `handle_marketing_copy` slices `result["variants"][:3]`
(`app/worker/handlers/marketing_ai.py:59`) even though `copy_schema()` already asks the model for
exactly 3 — a defensive truncation, not a trust boundary the schema alone should carry.
`handle_marketing_plan_week` filters `result["entries"]` down to only items whose `channel` field
is a valid `ChannelKey` enum value before capping at 7 (`app/worker/handlers/marketing_ai.py:90-95`)
— this is the mechanism behind the stub-provider empty-`entries` quirk documented in the FE guide
(§5): the stub LLM's placeholder value for an unconstrained string field
(`"[stub-llm] channel"`) is not a real `ChannelKey`, so every stub-generated entry gets dropped.
This is real, reproducible stub behavior traced to `app/platform/llm.py::StubLLMClient._stub_value`
and `plan_week_schema()`'s lack of an `enum` constraint on `channel` — not a bug introduced by this
slice, and not worked around; documented explicitly instead (see FE guide §5 and Follow-ups below).

**"Write-with-AI reuses copy-gen" is a documented FE composition, not new server code.** The design
spec named a "Write with AI" flow (generate copy for an in-progress calendar entry, then save the
chosen variant onto it) and an "Add all"/"Save to calendar" flow (from a plan-week result). Neither
needed a new endpoint: both compose existing routes — `POST /copy/generate` + `PATCH
/marketing/calendar-entries/{id}` (Slice 1) for Write-with-AI, and `POST
/marketing/calendar-entries` (Slice 1, one call per kept entry — no bulk-create endpoint) for
Save-to-calendar. No link between a generation row and the calendar entry it informs is persisted
server-side; if the FE wants "written with AI" provenance on an entry, it tracks that association
client-side. This is called out explicitly in the FE guide rather than silently assumed.

**Audience-segment validation on copy mirrors Slice 2's persona-validation pattern.**
`create_copy_generation` does one tenancy-scoped `filter_by(id=..., startup_id=...)` query against
`AudienceSegment` when `audience_segment_id` is provided, raising the same
`_validation(...)`-shaped 422 Slice 2's segments/campaigns services already use — no new validation
helper, reusing `app/services/marketing/service.py::_validation`.

**Access is the same `require_role(founder, team_member)` dependency instance Slices 1–2 already
built.** The two new route groups hang off the same `_marketing` dependency on the shared
`/marketing` router — no new RBAC wiring, only new routes registered behind it. `9ac3dfc` added a
dedicated test asserting the 403 fires on the `{id}` GET routes specifically (RBAC rejects before
any row lookup, so a forbidden role gets 403 even against a nonexistent/foreign id) — a gap a
review round flagged after the initial RBAC test only covered the POST/list routes.

## What's involved

**Migration `0035_marketing_ai_generations`** (chains off `0034_campaigns_segments`, sole alembic
head) — one new table:
- `marketing_ai_generations`: `id` (uuid PK), `startup_id` (uuid FK → `startups.id`,
  `ondelete="CASCADE"`, indexed), `created_by` (uuid FK → `users.id`, `ondelete="SET NULL"`,
  nullable), `kind` (non-native enum, `copy`/`plan_week`, length 12, not null), `inputs` (JSONB,
  not null — the create-time request echoed back), `status` (non-native enum,
  `generating`/`ready`/`failed`, length 12, not null), `output` (JSONB, not null, default `{}`),
  `error` (`String(200)`, nullable — `"over_budget"` today, room for future error codes),
  `created_at`/`updated_at`.

**Enums** — `app/db/models/enums.py`: `MarketingGenerationKind`, `MarketingGenerationStatus`,
`AssetType` (5 values), `CopyTone` (4 values), all `enum.StrEnum` mapped `native_enum=False`, same
convention as every other enum in this repo.

**Models** — `app/db/models/marketing.py` (extended, Slice 1/2 models untouched):
`MarketingAiGeneration` (`UUIDMixin, TimestampMixin, Base`); registered in
`app/db/models/__init__.py`.

**Prompt builders** — `app/services/marketing/ai_prompts.py` (new): `copy_schema`,
`build_copy_messages(*, asset_type, channel, tone, key_message, cta, segment_name)`,
`plan_week_schema`, `build_plan_week_messages(*, stage, industry, name)` — plain JSON-schema +
`LLMMessage` list builders, same shape as every other structured-output AI feature's prompt module.

**Schemas** — `app/schemas/marketing.py` (extended): `CopyGenerateRequest`, `GenerationResponse`
(shared by both kinds).

**Service** — `app/services/marketing/ai_content.py` (new): `serialize_generation`,
`create_copy_generation` (validates `audience_segment_id`, enqueues `ai.marketing.copy`),
`create_plan_week_generation` (enqueues `ai.marketing.plan_week`, no input validation — no request
body), `get_generation(..., kind=...)` (kind-scoped lookup, 404 on mismatch), `list_copy_generations`
(kind-scoped to `copy` only).

**Workers** — `app/worker/handlers/marketing_ai.py` (new): `handle_marketing_copy`,
`handle_marketing_plan_week`, both flush-only (no commit — the job runner commits), both routed
through the shared `_load`/`_fail_over_budget` helpers; registered as `ai.marketing.copy` /
`ai.marketing.plan_week`.

**Endpoints** — `app/api/v1/endpoints/marketing.py` (extended, Slice 1/2 routes untouched): 4 new
routes — `POST /marketing/copy/generate` (202), `GET /marketing/copy/generations` (history),
`GET /marketing/copy/generations/{id}` (poll), `POST /marketing/calendar/plan-week` (202),
`GET /marketing/calendar/plan-week/{id}` (poll) — all behind the existing `_marketing =
require_role(MembershipRole.founder, MembershipRole.team_member)` instance.

**Tests**
- `tests/db/test_marketing_models.py` + `tests/test_marketing_migration.py` — extended for the new
  table, single-head assertion.
- `tests/services/marketing/test_ai_prompts.py` (5) — schema shape + message-builder content for
  both kinds.
- `tests/services/marketing/test_ai_content_service.py` (7) — create-copy (with/without segment),
  foreign-segment 422, create-plan-week, kind-scoped `get_generation` (including the
  mismatch-is-404 case), kind-scoped history list.
- `tests/worker/test_marketing_ai_handlers.py` (4) — copy fills 3 variants (truncating a 4-item
  stub response), copy over-budget → `failed`/`over_budget`/`output: {}`, idempotent no-op when
  the row isn't `generating` (e.g. already `ready`), plan-week channel-filtering behavior.
- `tests/api/test_marketing_ai.py` (5) — 202-then-poll for both kinds, kind-mismatch 404, RBAC
  403 across all 6 routes including both `{id}` GET routes.
- `e2e/test_marketing.py::test_marketing_ai_generation_journey` (new, alongside Slices 1–2's
  journeys in the same file) — copy generate → drain → poll ready → history list → plan-week
  generate → drain → poll ready, 5 live captures under `e2e/_captures/marketing/`.

**Errors / API surface — additive only** (no existing Slice 1/2 route/shape changed):
`VALIDATION_ERROR` (422) on invalid enum values, blank/oversized `key_message`/`cta`, foreign
`audience_segment_id`; `FORBIDDEN` (403) for non-marketing roles; `NOT_FOUND` (404) for missing,
cross-tenant, or kind-mismatched generation ids. A budget-exhausted generation is **not** an HTTP
error — it's a 200 poll response with `status: "failed"`, `error: "over_budget"`.

**Docs**
- `docs/fe-integration-guide-marketing-copy.md` (new) — both POST-then-poll flows, every field,
  the `over_budget` failure (cross-ref `docs/fe-integration-guide-ai-status.md`), the kind-mismatch
  404, RBAC, the two FE compositions (Write-with-AI, Save-to-calendar/Add-all), and the
  stub-provider empty-`entries` quirk documented explicitly and inline. Cross-references the Slice
  1/2 guides.
- `docs/checklist/PROJECT_CHECKLIST.md` (reconciled — see below).

## Verification

**Per-task unit verification (green before the e2e task), oldest to newest:**
- Task 1 (enums + model + migration): model/migration round-trip test — 1 passed. Full suite:
  **1473 passed, 1 pre-existing failure** (unrelated live-Resend quota issue, not touched by this
  task). `alembic check`/`alembic heads` clean, single head `0035_marketing_ai_generations`.
- Task 2 (prompt builders): `tests/services/marketing/test_ai_prompts.py` — 5 passed. Full suite:
  **1474 passed**, same 5 pre-existing unrelated failures.
- Task 3 (AI generation service): `tests/services/marketing/test_ai_content_service.py` — 7
  passed. Full suite: **1481 passed**, same 5 pre-existing unrelated failures (none touch
  `app/schemas/marketing.py` or `app/services/marketing/*`).
- Task 4 (worker handlers): `tests/worker/test_marketing_ai_handlers.py` — 4 passed. Full suite:
  **1485 passed**, same 5 pre-existing unrelated failures. A same-branch refactor (`fdde2b9`)
  deduped the over-budget block across both handlers after the initial cut, re-verified: 4 passed
  again post-refactor.
- Task 5 (endpoints + RBAC): `tests/api/test_marketing_ai.py` — 5 passed (`59 passed` on the
  focused `tests/api/` slice used to verify no regression). Full suite: **1490 passed, 5
  pre-existing unrelated failures**.
- Task 6 (e2e journey + captures): `bash scripts/e2e_run.sh` — **55 passed, 0 failed**, all e2e
  journeys including the new `test_marketing_ai_generation_journey`. No Resend quota errors this
  run.
- `poetry run black`/`isort`/`ruff check` clean on every touched file, each task.
- `alembic heads` — single linear head (`0035_marketing_ai_generations`) confirmed after the
  migration task; no drift between the ORM models and the applied migration.

**Live e2e (`bash scripts/e2e_run.sh`, full suite):**

```
e2e/test_marketing.py::test_marketing_ai_generation_journey PASSED
...
55 passed in ...s
```

The new journey: onboard a founder → `POST /copy/generate` (captures
`copy_generate_accepted.json`) → drain the in-process worker → `GET
/copy/generations/{id}` (captures `copy_generation_ready.json`) → `GET /copy/generations`
history (captures `copy_generations_history.json`) → `POST /calendar/plan-week` (captures
`plan_week_accepted.json`) → drain → `GET /calendar/plan-week/{id}` (captures
`plan_week_ready.json`).

**Honest gap disclosure.** The e2e journey exercises only the happy-path ready state for both
kinds, under the stub LLM provider — the `over_budget`/`failed` path, the `audience_segment_id`
422, the kind-mismatch 404, and the RBAC 403 matrix are unit/integration-tested but not
e2e-captured; the stub provider also means `output.entries` is captured empty (§5's documented
quirk) rather than showing the real element shape. All of these are called out explicitly in the
FE guide's verification table rather than silently presented as live-verified.

## Operate / roll back

**New deploy-time requirement: none.** No new background job type infrastructure — both handlers
run inside the existing worker process via the existing job dispatcher/runner, same as every other
`ai.*` job in this API. No new container, no new config beyond the LLM seam's existing
`LLM_DAILY_TOKEN_BUDGET`/`LLM_PROVIDER` settings (already deployed for Module 03).

**Rollback:** revert this slice's commits as a unit (`bf41466..1ff400f`, plus this docs commit) and
downgrade the migration (`poetry run alembic downgrade 0034_campaigns_segments`) to drop
`marketing_ai_generations`. Safe — no other feature reads this table yet. Downgrade the migration
only *after* the app code is already rolled back, not before, same rollback ordering note as every
other slice in this module.

## Follow-ups

**Slices 3b–5 of Module 10, all deferred by design, not gaps in this slice:**
- **Slice 3b — Channel-plan recommend + fit notes.** An AI recommender for a campaign's
  `channel_mix` (Slice 2), plus "fit notes" explaining the recommendation. Nothing in this slice
  blocks it — the same `marketing_ai_generations` table and worker-handler pattern this slice
  established can add a third `kind` value.
- **Slice 4 — SEO Tools.**
- **Slice 5 — Performance Analytics.** Fills `Overview.top_channel_by_conversions` and `metrics`;
  will likely reuse `marketing_ai_generations` for a weekly-readout `kind` (see below).

**Deferred within Slice 3a itself:**
- **Inline "Refine."** No endpoint to iterate on an existing generation's output (e.g. "make
  variant 2 punchier") — a founder who wants a different result must start a brand-new generation
  from scratch.
- **No generation↔calendar-entry link persisted.** Both FE compositions (Write-with-AI,
  Save-to-calendar) are pure client-side orchestration over existing endpoints; the API does not
  record which calendar entry a given generation informed, so "written with AI" provenance, if
  wanted, must be tracked client-side.
- **Plan-week has no history-list endpoint** (unlike copy's `GET /copy/generations`) — a founder
  can poll a plan-week id they already have, but not browse past plan-week generations.
- **Stub-provider `plan_week` entries are always empty**, because `plan_week_schema()`'s `channel`
  field has no JSON-schema `enum` constraint and the stub LLM's placeholder value for an
  unconstrained string field never matches a real `ChannelKey`. This only affects the stub
  provider (staging/e2e); a real provider is not affected. If a non-empty stub result becomes
  useful (e.g. richer local dev/demo data), a real fix would add an `enum` constraint to
  `plan_week_schema()`'s `channel` property and update `StubLLMClient` to honor per-field enums —
  not attempted here, since it would change the schema contract handed to a real LLM provider too,
  which is out of this slice's scope.
- **S5 weekly-readout reuses this table.** The design spec names a future weekly performance
  readout as another `marketing_ai_generations` `kind` — no code changes needed here to enable
  that; noted as forward context for Slice 5, not a commitment made by this slice.
