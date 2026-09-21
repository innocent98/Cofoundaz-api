# SOP — Per-Workspace LLM Token Budget + `GET /ai/status` (Module 03 deferred infra, 2 of 3)

**What shipped** — a per-workspace daily LLM token budget (ledger + config + enforcement across
every AI-enrichment call site) and a new workspace-scoped `GET /ai/status` endpoint that surfaces
today's usage against that budget plus recent failed AI-enrichment jobs. This closes **2 of the 3**
infra items Module 03 Slice 1 named as deferred, non-blocking follow-ups when Module 03 was marked
complete on 2026-09-21 (`docs/checklist/PROJECT_CHECKLIST.md`); the third (a non-OpenAI/Anthropic
`LLMClient` provider implementation) is **dropped won't-do (OpenAI-only)** in this same pass — see
Follow-ups.

Commits, oldest to newest, branch `feat/llm-budget-ai-status` (off `develop`), PR to follow:

| Commit | Subject |
|---|---|
| `40996dd` | `llm_usage_daily` ledger + `LLM_DAILY_TOKEN_BUDGET` config + migration `0030` |
| `729fa96` | surface `last_usage_tokens` from the LLM seam |
| `5efbffb` | per-workspace daily token budget metering helper (`app/platform/llm_budget.py`) |
| `e8fac91` | enforce the budget in the 8 `ai.py` AI handlers |
| `6cd6405` | budget-guard the business-plan generator |
| `9f65542` | `GET /ai/status` — workspace token usage vs. budget + failed enrichments |
| `fa6f0cc` | live e2e for `GET /ai/status` |

## Why

Every prior Module 03 slice (1–7) wired a real AI consumer onto the free-text/structured-output
LLM seam Slice 1 built, but **nothing capped how many tokens a single workspace could burn** —
a founder retriggering enrichment-heavy actions (re-onboarding flows, repeated `ai_fill` retries,
business-plan regeneration) had no ceiling, and there was no way for the FE or an operator to see
*why* a founder was or wasn't getting AI-upgraded content on a given day. Slice 1's own Deferred
list named this explicitly: "no per-workspace LLM budget/rate limiting" and "no structured 'AI
enrichment failed / still templated' signal for the FE or an operator." Module 03 was marked
complete anyway (owner decision, 2026-09-21) with these scoped out as non-blocking — this slice is
that follow-up work, done as its own small, self-contained infra slice rather than folded into a
product feature.

## How

**A daily, per-startup token ledger (`llm_usage_daily`) is the single source of truth for both
enforcement and observability** — one row per `(startup_id, usage_date)`, upserted via
`INSERT ... ON CONFLICT DO UPDATE tokens_used = tokens_used + :tokens` (`app/platform/
llm_budget.py::debit`) so concurrent AI jobs racing on the same workspace/day accumulate
correctly instead of clobbering each other. `GET /ai/status` reads the same table for
`tokens_used_today`.

**`LLM_DAILY_TOKEN_BUDGET` (default `15000`) is a single server-wide config value, not a
per-workspace setting.** Every workspace shares the same cap. `≤ 0` is the documented kill-switch
— `over_budget()` returns `False` unconditionally and `GET /ai/status` reports `daily_budget:
null` ("unlimited"), so an operator can disable the whole feature by config without a deploy of
code.

**Actual token usage, not an estimate, is what gets debited** — this only works because
`729fa96` (a prerequisite of this slice) made `OpenAILLMClient` record `last_usage_tokens` from
the API response's own `usage.total_tokens` after every `complete()`/`complete_json()` call.
`StubLLMClient` (used by every e2e run) reports `0`, which is *why* the whole existing e2e suite
and every stub-backed unit test stay green under this change without modification — the budget
mechanism exists but the stub provider never actually accrues anything.

**`metered_complete`/`metered_complete_json` (`app/platform/llm_budget.py`) is a check-then-debit
wrapper, not a decorator or middleware** — deliberately explicit at each call site rather than
hidden, so every handler's diff shows exactly where budget enforcement was added. Each does: check
`over_budget(db, startup_id)` → if over, return `None` immediately (no LLM call at all) → otherwise
call the real client → debit `last_usage_tokens` → return the result. **`None` is the contract for
"skip"** — every one of the 8 call sites in `app/worker/handlers/ai.py` was changed to check for
`None` and `return` early, leaving whatever value the record already had (templated or a prior AI
upgrade) untouched. No error is raised, no job fails, no status flips to `failed` — this is a
silent, expected degrade, same tone as every other "templated is the permanent fallback" pattern
Module 03 slices 1–7 already established, just triggered by budget instead of by an LLM error.

**The business-plan generator (`app/worker/handlers/plan.py`, §08.11) does NOT use
`metered_complete`** — it was deliberately kept on a **check-once-up-front, debit-per-section-via-
the-raw-client** shape instead. The plan generator makes 10 sequential `client.complete()` calls
(one per fixed section) inside a single job. Gating *each* section through `metered_complete`
would let a plan trip over budget mid-generation and persist with some sections AI-written and the
rest silently empty — a broken, partial document. Instead: `over_budget()` is checked once before
section 1; if already over, the whole job returns early and the plan **stays in `generating`
status for a later retry** (no partial document, no truncated content) — but if usage crosses the
budget *during* generation (section 6 of 10 pushes it over), the remaining sections still
complete, so a single plan is never truncated mid-document, at the cost of that one plan being
allowed to slightly overshoot the budget. After each section's `client.complete()`, the handler
calls `debit(db, startup.id, client.last_usage_tokens)` directly (the same ledger `metered_complete`
would have used) so usage is still tracked accurately per section.

**`GET /ai/status` is workspace-level only — there is no per-field status.** It reports one
`over_budget` boolean and one `tokens_used_today` count for the whole workspace, not "is this
specific `MissionTask.reason` AI-authored or still templated." Building a per-record signal would
mean touching every consumer's schema/response shape (8+ places); this slice deliberately scopes
to a single new read-only endpoint that answers "is this workspace currently budget-limited, and
what recently failed" — the cheapest signal that unblocks the FE, not the most granular one
possible. See Follow-ups.

**The stub provider accruing `0` tokens is what keeps every existing test/e2e green — this was
verified, not assumed.** Because `StubLLMClient.last_usage_tokens` is always `0`, `debit()`'s
early-return-on-`tokens <= 0` guard means the ledger is never actually written to under
`LLM_PROVIDER=stub`, so `over_budget()` never trips during any test run and no existing AI-consumer
test needed its assertions changed for budget reasons.

**The ~9 handler call sites (8 in `ai.py` + the plan generator) needed a monkeypatch-target
reconciliation, not just a wrapper swap.** Every pre-existing handler test that monkeypatched
`app.worker.handlers.ai.get_llm_client` directly (to inject a controllable fake client) broke the
moment handlers stopped importing `get_llm_client` and started calling `metered_complete*`
instead — those tests now monkeypatch `app.platform.llm_budget.get_llm_client` (the module
`metered_complete*` actually resolves the client through) and set `LLM_DAILY_TOKEN_BUDGET` high
enough that the budget check never skips the call, so each test still exercises the real
enrichment path it was written to test.

## What's involved

**Migration `0030_llm_usage_daily`** (chains off `0029_startup_profile_ai_panel`, sole prior
alembic head) — `alembic/versions/0030_llm_usage_daily.py`:
- `llm_usage_daily` table — `startup_id` (FK → `startups.id`, `ondelete=CASCADE`, indexed),
  `usage_date` (Date), `tokens_used` (Integer, default `0`), plus the standard `id`/
  `created_at`/`updated_at` mixin columns; `UNIQUE(startup_id, usage_date)`.

**Config** (`app/core/config.py:124`)
- `LLM_DAILY_TOKEN_BUDGET: int = 15000` — per-startup, per-UTC-day token cap; `≤ 0` = unlimited.

**Model**
- `app/db/models/llm_usage.py::LlmUsageDaily` (new).

**LLM seam extension** (`app/platform/llm.py`)
- `OpenAILLMClient` now records `last_usage_tokens` from `usage.total_tokens` on every
  `complete()`/`complete_json()` response; `StubLLMClient` always reports `0`.

**Budget helper** (`app/platform/llm_budget.py`, new)
- `today_utc()`, `over_budget(db, startup_id) -> bool`, `debit(db, startup_id, tokens) -> None`
  (upsert against `llm_usage_daily`), `metered_complete(...)`/`metered_complete_json(...)`
  (check-then-debit wrappers, return `None` on over-budget), `is_ai_enrichment_job(job_type) ->
  bool` (matches `ai.*`, `*.ai_fill`, `business.plan.generate`).

**Enforcement — 8 handlers** (`app/worker/handlers/ai.py`)
- `handle_assessment_narrative`, `handle_canvas_ai_fill`, `handle_record_ai_fill`,
  `handle_mission_reason`, `handle_health_recommendations`, `handle_dashboard_briefing`,
  `handle_roadmap_rationale`, `handle_onboarding_panel` — each now calls `metered_complete`/
  `metered_complete_json` instead of `get_llm_client()` directly, and `return`s early (keeping the
  existing templated/default value) when the call returns `None`.

**Enforcement — business-plan generator** (`app/worker/handlers/plan.py`)
- `handle_plan_generate` — one `over_budget()` check before section 1 (early return, plan stays
  `generating`); per-section `debit()` call via the raw client after each of the 10 section
  `complete()` calls.

**New endpoint** (`app/api/v1/endpoints/ai.py::ai_status`, `app/api/v1/api.py`)
- `GET /api/v1/ai/status` — `tokens_used_today`, `daily_budget` (nullable), `over_budget`,
  `resets_at` (next UTC midnight), `recent_enrichment_failures` (up to 20, newest first, filtered
  via `is_ai_enrichment_job` from the 200 most-recent failed jobs of any type — see the FE guide's
  Verification table for the known pre-filter-cap edge case this introduces).

**Tests**
- `tests/db/test_llm_usage_models.py` (2) — ledger round-trip + unique constraint.
- `tests/platform/test_llm_usage_seam.py` (3) — `last_usage_tokens` populated for the real client,
  `0` for the stub.
- `tests/platform/test_llm_budget.py` (6) — `over_budget`/`debit`/`metered_complete*`/
  `is_ai_enrichment_job` unit coverage, including the `≤0` unlimited kill-switch.
- `tests/worker/test_ai_budget_enforcement.py` (2, new) — enrich-and-debit and
  skip-and-keep-templated paths, plus every pre-existing `tests/worker/test_*_handler.py` file
  reconciled to monkeypatch `app.platform.llm_budget.get_llm_client`.
- `tests/worker/test_plan_handler.py` (5) — extended with the over-budget-skips-whole-job and
  mid-generation-overshoot-still-completes cases.
- `tests/api/test_ai_status.py` (6, new) — endpoint shape, `daily_budget: null` on unlimited,
  `over_budget` true/false, `recent_enrichment_failures` filtering (AI vs. non-AI job types).
- `e2e/test_ai_status.py` (new) — live journey, see Verification.

**Docs**
- `docs/fe-integration-guide-ai-status.md` (new) — field-by-field contract, captured live.
- `docs/checklist/PROJECT_CHECKLIST.md` — Module 03's deferred-follow-ups bullet reconciled (this
  pass).

## Verification

**Per-task unit verification, re-run for this docs pass:**

| Suite | Result |
|---|---|
| `tests/db/test_llm_usage_models.py` | 2 passed |
| `tests/platform/test_llm_usage_seam.py` | 3 passed |
| `tests/platform/test_llm_budget.py` | 6 passed |
| `tests/worker/test_ai_budget_enforcement.py` | 2 passed |
| `tests/worker/test_plan_handler.py` | 5 passed |
| `tests/api/test_ai_status.py` | 6 passed |

**Full non-e2e suite:** `poetry run pytest -q --no-cov` → **1,346 passed**, no regression (1,340
at Task 5's snapshot, before Tasks 6–7 added `test_plan_handler.py`'s new cases and
`test_ai_status.py`).

**Migration:** `poetry run alembic heads` → single head `0030_llm_usage_daily`; `poetry run
alembic check` → `No new upgrade operations detected.` (no drift between models and the migration
chain).

**Live e2e (`scripts/e2e_run.sh`), full suite — 52 passed, no regression** (51 pre-existing + 1
new). `e2e/test_ai_status.py::test_ai_status` walks: sign up → onboard (industry/stage/goals +
complete) → `GET /api/v1/ai/status` with the resulting `X-Workspace-Id` → asserts `200`,
`tokens_used_today == 0` (stub run, nothing accrued), `over_budget is False`, `daily_budget`
present, `resets_at` present and non-empty, `recent_enrichment_failures == []` → captures
`status.json` (the FE guide's source-of-truth body). Because `LLM_PROVIDER=stub` debits `0`
tokens on every call, no pre-existing AI-consumer e2e test was ever at risk of tripping the budget
mid-suite — verified by the full-suite green run, not assumed.

This is the full CI-relevant reproduction for the app-code tasks (1–7); this task (8) is docs-only
and introduces no `app`/test changes — the numbers above are a re-verification, not new coverage.

## Operate / roll back

**Deploy-time requirement: none new.** No new container, no new health check. The only new runtime
knob is `LLM_DAILY_TOKEN_BUDGET`, which already has a safe default (`15000`) — no `.env` change is
required to deploy this slice.

**Kill-switch (operate):** set `LLM_DAILY_TOKEN_BUDGET=0` (or any value `≤ 0`) in the relevant
`.env.*.enc` to disable budget enforcement entirely — `over_budget()` always returns `False`, every
AI handler always calls the LLM, and `GET /ai/status` reports `daily_budget: null`. No restart-only
config; a normal deploy picks it up like any other setting.

**Roll back:** revert this slice's commits as a unit (`40996dd..fa6f0cc`, plus this docs commit)
and downgrade the migration (`poetry run alembic downgrade 0029_startup_profile_ai_panel`) to drop
`llm_usage_daily`. Downgrading is safe — no other feature reads that table. If the code is reverted
without downgrading, the table simply stops being written to; if the migration is reverted first,
in front of not-yet-rolled-back code, the next AI job's `metered_complete*` call raises (the table
no longer exists) — **downgrade the migration only after the app code is already rolled back**, not
before, same convention as every other Module 03 migration rollback documented in this
directory.

## Follow-ups

**Only genuine build gap: call-rate limiting was not built — only token-volume budgeting was.**
"Per-workspace LLM budget/rate limiting" was the item's name since Slice 1, but this slice only
built the token-volume half. A workspace making many small, cheap LLM calls in rapid succession
(each individually well under `LLM_DAILY_TOKEN_BUDGET`) is not throttled by call frequency at all
today — only cumulative daily token volume is capped.

**No per-field/per-record "still templated" signal — only a workspace-level aggregate.**
`GET /ai/status` answers "is this workspace currently budget-limited, and what recently failed,"
not "is this specific `MissionTask.reason` / `HealthRecommendation.body` / canvas block currently
templated or AI-authored." A founder can be far under budget with zero recent failures and still
be looking at templated copy on a record whose enrichment job simply hasn't run yet (or ran before
this slice existed) — `/ai/status` gives no visibility into that. This is the same class of gap
every prior Module 03 slice's own SOP has flagged for its one feature; this slice doesn't close it
for any of them, it only adds one new, coarser, workspace-wide signal.

**No per-consumer terminal state on a budget-skip.** When a job skips enrichment over-budget, most
handlers just leave a field templated (fine — the templated value is legitimate content). But a few
surfaces show placeholder/empty states: `handle_dashboard_briefing`'s skip leaves the `DailyBriefing`
row with `status = "generating"` and its placeholder body indefinitely (no terminal state, not
re-driven until manually re-enqueued); `handle_canvas_ai_fill` and `handle_record_ai_fill` skip
leaves their block/kind empty (no records added, no blocks filled) rather than templated prose.
Also, `handle_plan_generate`'s early-return leaves the business plan's document status as
`generating` indefinitely, with nothing to flip it to a terminal "stuck, will retry when budget
resets" state or to actually retry it once `resets_at` passes — a founder generating a plan while
over budget sees a dashboard that says "generating" forever unless something re-triggers the job.
No scheduled re-drive of skipped jobs exists.

**`resets_at` (and the whole daily window) is fixed UTC midnight, not workspace-timezone-aware.**
Every workspace shares the exact same reset instant regardless of the founder's actual timezone —
flagged in the FE guide, not fixed here.

**The `/ai/status` `recent_enrichment_failures` pre-filter-cap edge case is a known, un-fixed gap,
not a bug in this slice's own tests.** The endpoint fetches the 200 most-recent failed jobs of
*any* type, then filters to AI-enrichment ones, then caps at 20. A workspace with 200+ more-recent
non-AI-job failures (e.g. a burst of unrelated failing jobs) can have a genuinely-recent AI
enrichment failure fall outside that first 200-row window and never appear in
`recent_enrichment_failures`, even though it would otherwise rank in the newest 20 AI failures.
Not expected to matter in practice (200 failed jobs of any kind in a single day for one workspace
is already an anomaly worth its own alerting), but it is a real, unaddressed correctness gap in
the endpoint's own query shape — see the FE guide's Verification table.
