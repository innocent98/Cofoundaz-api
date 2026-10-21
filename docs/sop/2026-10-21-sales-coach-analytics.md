# SOP — Sales Hub slice C: AI coach, call prep and analytics (Module 11)

| | |
|---|---|
| **Shipped** | Branch `feature/sales-coach-analytics` → PR to `develop` (link in the PR; built, awaiting review and merge) |
| **Date** | 2026-10-21 |
| **Area** | Sales: `app/db/models/sales_coach.py`, `app/schemas/sales_coach.py`, `app/services/sales/{coach,coach_context,coach_prompts,call_prep,analytics}.py`, `app/worker/handlers/sales_ai.py`, routes appended to `app/api/v1/endpoints/sales.py` |
| **Migration** | `0064_sales_coach` (3 new tables, additive), down `0063_sales_crm_core` |
| **Spec** | `docs/superpowers/specs/2026-10-21-sales-design.md` (FE repo): §3 coach tables, §4 Coach + analytics, D-SAL-9, §11.6 |
| **FE guide** | `docs/fe-integration-guide-sales-coach-analytics.md` (from `e2e/_captures/sales_c/`, 36 captures) |

## What shipped

Three things on top of the slice A CRM: an **AI role-play coach** (an AI buyer built from a deal or a free
scenario, a persona and a difficulty, then a scored scorecard when the call ends), **call prep** for a deal
(context, likely objections with suggested responses, call goal, 3 discovery questions), and
**`GET /sales/analytics`** (funnel, average cycle days, win/loss reasons, activity leaderboard, 12-month
closed-won revenue).

## Why

The Sales UI shipped the Coach and Analytics pages as static mock-ups. Spec D-SAL-9 asks for a real coach that
reuses the co-founder chat protocol and background LLM jobs, and the Analytics page needs numbers computed from
the real pipeline.

## How (decisions)

| Decision | Choice | Alternative rejected |
|---|---|---|
| Chat protocol | Exactly the co-founder protocol: POST returns `202` + an empty `generating` buyer turn, job `ai.sales.coach_reply` fills it, FE polls the GET, `409 REPLY_IN_PROGRESS` while one is generating. Own tables and job types. | A streaming endpoint; reusing `ai_conversations` |
| Job types | `ai.sales.coach_reply`, `ai.sales.coach_scorecard`, `ai.sales.call_prep` (the `ai.` prefix keeps them in `GET /ai/status` failure lists) | Names without the prefix |
| Context | Built once at session start and **snapshotted** in `context_json` (deal, account, contact name/title only, last 10 activities, seller business). Fenced as JSON with `<` escaped; the prompt says it is data. No email or phone goes to the model. Call prep rebuilds from the live deal each time. | Rebuilding per reply (the buyer would drift as the deal is edited; a deleted deal would break the session) |
| Privacy | Sessions are private to their creator (404 for teammates), like the co-founder chat. Call prep is per deal and visible to every Sales member. | Workspace-shared sessions |
| Failure handling | Handlers catch model errors and mark the row `failed` (buyer turn, `scorecard_error`, call prep `error`) instead of raising. Over budget does the same without calling the model. This fixes, for these handlers only, the stuck-`generating` follow-up noted for the co-founder chat. | Letting the runner retry (the FE would poll a `generating` row for minutes) |
| Scorecard | Schema asks for three whole 0 to 10 scores + `feedback[]`; the handler clamps scores, derives `overall` (mean, 1 dp), strips dashes, caps feedback at 8 lines. A missing or non-numeric score is `bad_output`, never a made-up number. The transcript goes in `<transcript>` with `<` escaped. | Trusting the raw model JSON |
| End/retry | `end` → `scoring` → `ended`. Ending a scored session is a no-op; ending one whose scorecard failed re-queues it. `NOTHING_TO_SCORE` 422 when the seller never spoke; 409 while a buyer turn or the scorecard is in flight. | A separate retry endpoint |
| Call prep | One in flight per deal, enforced under the deal row lock (`SELECT ... FOR UPDATE`) so a double click cannot queue two. Regenerate adds a row; GET returns the latest or `null`. | Updating one row in place |
| Analytics funnel | Cohort = deals **created** in range. "Reached stage X" = furthest non-lost stage position in `deal_stage_changes` (incl. the creation row) ≥ X's position (spec §11.6). The lost stage is not a rung, so a lost deal never counts as reaching Closed Won. | Counting stage changes in range (double counts, ignores skipped stages) |
| Analytics scope | Everything else (cycle days, won, loss reasons) uses deals **currently** in the won/lost stage that **closed in range**; a reopened deal drops out. Leaderboard lists all current F/TM members including zeros. Monthly revenue is always the last 12 UTC months and ignores the range. All Python-side bucketing, so it is timezone-safe. | SQL `date_trunc` in the DB session timezone |
| Validation | `deal_id` of another workspace is `422` on `deal_id` (like slice A's foreign ids); path ids are `404`. "Pick a deal or describe a scenario" is raised in the service so it names `deal_id` rather than leaking a Pydantic `Value error,` message. | Model-level validator |

## What's involved

| Area | Paths |
|---|---|
| Enums | `app/db/models/enums.py` (`CoachDifficulty`, `CoachSessionStatus`, `CallPrepStatus`; chat role/status reused) |
| Models + migration | `app/db/models/sales_coach.py` (`SalesCoachSession`, `SalesCoachMessage`, `SalesCallPrep`), `alembic/versions/0064_sales_coach.py` |
| API | routes appended to `app/api/v1/endpoints/sales.py` (same router, same `require_role(*SALES_ROLES)`), `app/schemas/sales_coach.py` |
| Services | `app/services/sales/{coach,coach_context,coach_prompts,call_prep,analytics}.py` |
| Worker | `app/worker/handlers/sales_ai.py`, registered in `app/worker/__main__.py` |
| Tests | `tests/services/sales/{test_coach,test_analytics}.py`, `tests/worker/test_sales_ai_handlers.py`, `tests/api/sales/test_coach_api.py`, `tests/test_sales_coach_migration.py`; the slice A permission sweep (`tests/api/sales/test_permissions.py`) enumerates the router, so it covers the new routes automatically |
| E2E | `e2e/test_sales_c.py`, captures `e2e/_captures/sales_c/` |

Endpoints (founder + team member only): `POST|GET /sales/coach/sessions`, `GET /sales/coach/sessions/{id}`,
`POST /sales/coach/sessions/{id}/messages` (202), `POST /sales/coach/sessions/{id}/end` (202),
`POST|GET /sales/deals/{id}/call-prep`, `GET /sales/analytics?from&to`.

## Verification

- Unit/integration on scratch DBs (`ci_scratch_test`), never the dev DB: services, handlers (fake LLM client incl. raising, over-budget, dashes, fence escape, idempotence, cross-workspace payloads), API (202/409/422/404, mentor + consultant 403, cross-workspace deal ids, privacy between teammates), and migration round trip + `alembic check`.
- Live: `e2e/test_sales_c.py` passes against a real uvicorn server (scratch DB `b_e2e`, port 8031, stub LLM, in-process worker drain). It covers both setups, the whole poll protocol and its 409s, scoring, retry after a failed scorecard, call prep (empty, 409, ready, regenerate), analytics with a built history (default and wide range, 3 error shapes), mentor 403 on three routes, a foreign workspace's deal id / session id, and the over-budget states.
- **Not live-verified:** the real model (buyer realism, scorecard calibration, latency); a provider that raises mid-call (unit only); the consultant 403 (same gate as the mentor, unit only). Captures are stub output (`[stub-llm]`, scores all 0).

## Operate / roll back

- Deploy: `alembic upgrade head` creates 3 tables. No new env vars or services; needs the existing worker (new job types `ai.sales.*` are registered in `app/worker/__main__.py`) and the existing LLM settings (`LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MAX_TOKENS`, `LLM_DAILY_TOKEN_BUDGET`). With `LLM_PROVIDER=stub` everything works with placeholder text.
- Migration order: Finance slice (a) also uses `0064`, down `0063_sales_crm_core`. Whichever merges second must be rechained (rename the file and revision to the next number and set `down_revision` to the other's id; nothing else references the id).
- Roll back: `alembic downgrade -1` drops `sales_call_preps`, `sales_coach_messages`, `sales_coach_sessions` (**deletes all practice sessions and call preps**; no other table is touched). Revert the PR for code. Queued `ai.sales.*` jobs for a reverted build have no handler: purge them first.
- Cost: every buyer turn, scorecard and call prep is one metered call (`LLM_MAX_TOKENS` each). A session sends at most the last 30 turns.

## Follow-ups

- Real-model walk: buyer realism per difficulty, scorecard calibration, objection quality, polling latency.
- Practice sessions are never deleted; add a delete/archive if the history list grows (FE paginates with `limit`/`offset`).
- Analytics has no per-member filter or CSV export; the funnel cohort (created in range) may need a "stage activity in range" variant if founders expect it.
- Slice B (sequences) and FE-BC remain; Health Score sales signal (D-SAL-10) unchanged.
- The spec's gap list (`docs/checklist/sales-prd-gaps.md`, rows 25 to 30) lives in the FE repo, not this one; tick those rows there.

## Fix 2026-10-21: strict schema (fix/sales-coach-schema)
Real-model scorecards and call preps failed with an OpenAI 400 ("additionalProperties is required to be supplied and to be false"): strict `json_schema` needs `additionalProperties: false` on every object. Added it to `scorecard_schema()` and `call_prep_schema()` (top level and the objection items) in `app/services/sales/coach_prompts.py`; `tests/services/sales/test_coach_schemas.py` walks both schemas and asserts every object is strict-valid. The LLM stub never enforced this, which is why unit and e2e tests passed.
