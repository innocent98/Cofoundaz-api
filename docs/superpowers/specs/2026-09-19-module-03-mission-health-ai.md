# Module 03 — mission reason line + health recommendations (AI upgrade) — design

**Status:** approved-for-planning
**Date:** 2026-09-19
**Module:** 03 (AI Co-Founder) consumers — two more templated surfaces upgraded to real LLM text.
**Depends on:** Module 03 Slice 1 (LLM seam, `complete`) + Slice 2 (structured output, `complete_json`) — shipped; the job worker; Module 05 (missions) + Module 07 (health score) — shipped.

## Goal

Replace the two remaining static/templated text surfaces with async LLM upgrades, using the same
async-upgrade pattern as the assessment narrative (Slice 1):

1. **Mission reason line** — each `MissionTask.reason` (today "From your '<milestone>' milestone.")
   becomes a short, motivational "why this task today" line.
2. **Health recommendation body** — each pending `HealthRecommendation.body` (today a static catalog
   string) becomes a personalized 1–2 sentence actionable recommendation.

One sentence: *the assessment-narrative async-upgrade pattern, applied to two more consumers, each via
one structured `complete_json` call that rewrites text a template already wrote.*

## Why

- Missions and health recommendations are user-facing every day but read as boilerplate. Both already
  have a stored text field written by a template; the LLM seam + structured output make personalizing
  them a low-risk, high-signal slice with **no schema change**.
- Completing these two consumers advances Module 03 toward done (remaining after this: dashboard
  briefing, onboarding panel, roadmap re-plan rationale).

## Scope

### In scope
1. **Mission reason upgrade** — enqueue `ai.mission.reason` when a mission is generated with ≥1 task;
   `handle_mission_reason` rewrites **every** task's `reason` in that mission via one `complete_json`.
2. **Health recommendation upgrade** — enqueue `ai.health.recommendations` after `generate_recommendations`;
   `handle_health_recommendations` rewrites the `body` of **pending** recs whose body is still the catalog
   default, via one `complete_json`. **Title is left as the catalog headline** (stable FE key).
3. Prompt builders (`app/services/mission/ai_reason.py`, `app/services/health_score/ai_recommendations.py`).
4. Tests (unit + one live e2e per consumer against the stub), FE-guide updates, SOP, checklist.

### Out of scope (deferred)
- New notifications (both are silent enrichments; mission-ready + health-recompute notifications already fire).
- Any migration (all fields exist).
- Upgrading health recommendation `title`; upgrading `accepted`/`dismissed` recs (user-acted rows are never rewritten).
- The other Module 03 consumers (dashboard briefing, onboarding panel, roadmap re-plan rationale) — later slices.
- Anthropic provider impl.

## Architecture

### 1. Triggers & enqueue points

- **Mission** — in `get_or_generate_today` (`app/services/mission/service.py`), after the final
  `db.flush()`: if the mission has ≥1 `MissionTask`, `job_dispatcher.enqueue(db, "ai.mission.reason",
  {"mission_id": str(mission.id)}, startup.id)`. Only on the create path — the cached early-return never
  re-enqueues, so it fires **exactly once per mission per day**. Empty (weekend) missions do not enqueue.
- **Health** — in `recompute_health_score` (`app/services/health_score/service.py`), immediately after
  `generate_recommendations(db, startup.id, dim_scores)`:
  `job_dispatcher.enqueue(db, "ai.health.recommendations", {"startup_id": str(startup.id)}, startup.id)`.
  Both recompute callers (assessment-complete, lazy-read) are covered; repeated enqueues are made free by
  the handler's idempotency guard (below).
- Dispatcher: `from app.platform.jobs import job_dispatcher` (same singleton the assessment service uses).

### 2. Handlers (`app/worker/handlers/ai.py`, extend; both registered there)

**`handle_mission_reason(db, job)`**
1. `mission = db.get(Mission, uuid(job.payload["mission_id"]))`; no-op if missing.
2. Load its tasks ordered by `order`; no-op if none.
3. `result = get_llm_client().complete_json(build_mission_reason_messages(mission, tasks, startup),
   schema=mission_reason_schema(len(tasks)), max_tokens=settings.LLM_MAX_TOKENS)`.
4. Map returned `{reasons: [{order, reason}]}` by `order`; for each task set `task.reason = reason[:300]`
   when a reason is present for that order (tasks with no returned reason keep their templated fallback).
5. `db.flush()`. No commit/rollback (runner owns txn). Fail-loud on the LLM call → job retry.

**`handle_health_recommendations(db, job)`**
1. `startup = db.get(Startup, uuid(job.payload["startup_id"]))`; no-op if missing.
2. Query `HealthRecommendation` rows for the startup with `status == pending`.
3. **Idempotency guard:** keep only rows whose `body` still equals the catalog default for their `key`
   (`RECOMMENDATION_CATALOG`). If the filtered set is empty → **return before any LLM call**.
4. `result = get_llm_client().complete_json(build_health_recommendation_messages(rows, startup),
   schema=health_recommendation_schema(), max_tokens=settings.LLM_MAX_TOKENS)`.
5. Map returned `{recommendations: [{key, body}]}` by `key`; for each still-in-scope row set
   `row.body = body` when present. `db.flush()`. Fail-loud on the LLM call.

Registration (module top-level, beside the existing ones):
`register_handler("ai.mission.reason", handle_mission_reason)` and
`register_handler("ai.health.recommendations", handle_health_recommendations)`.
(`app/worker/__main__.py::register()` already imports `handlers.ai`.)

### 3. Prompt builders + schemas

- `app/services/mission/ai_reason.py`
  - `build_mission_reason_messages(mission, tasks, startup) -> list[LLMMessage]` — PII-free: startup
    name/industry/stage + per-task `{order, title}` + any milestone context already on the reason; asks
    for a short (≤300 char) motivational "why this task today" line per task, keyed by `order`.
  - `mission_reason_schema(n)` — strict object `{reasons: array(maxItems=n) of {order:int, reason:str}}`,
    all required, `additionalProperties:false`.
- `app/services/health_score/ai_recommendations.py`
  - `build_health_recommendation_messages(rows, startup) -> list[LLMMessage]` — PII-free: startup
    context + per-rec `{key, dimension, title}` (the catalog title as the topic); asks for a personalized
    1–2 sentence actionable `body` per `key`.
  - `health_recommendation_schema() -> dict` — strict object `{recommendations: array of {key:str,
    body:str}}`, all required, `additionalProperties:false`.

Both mirror `build_narrative_messages` (Slice 1) / `build_record_fill_messages` (records slice).

### Data model

**No migration.** `MissionTask.reason` (`String(300)`), `HealthRecommendation.title`/`body` all exist.
Single alembic head unchanged.

## Data flow

```
GET /mission/today (or 06:00 scheduler) → get_or_generate_today creates mission + templated reasons
    → enqueue ai.mission.reason {mission_id}   … worker …
  handle_mission_reason → complete_json(mission_reason_schema) → overwrite every task.reason
GET /mission/today → AI reasons (templated reason is the instant value + fallback)

assessment complete / health lazy-read → recompute_health_score → generate_recommendations (catalog text)
    → enqueue ai.health.recommendations {startup_id}   … worker …
  handle_health_recommendations → (pending & body==catalog?) → complete_json → overwrite body
GET health recommendations → AI bodies (catalog body is the instant value + fallback)
```

## Error handling

- **Mission**: missing mission / no tasks → no-op. A task with no returned reason keeps its template.
- **Health**: missing startup / no pending-default rows → no-op (no LLM call). `accepted`/`dismissed`
  rows are never in scope. A key with no returned body keeps its catalog body.
- **Idempotency**: mission enqueues once/mission; health guards on body==catalog default, so repeated
  recomputes are free once upgraded and never clobber an already-personalized or user-acted row.
- **Concurrency**: both handlers re-query at run time (no stale read); health mutates only
  still-pending-and-default rows. `reason` and health `title` are not user-editable — no user input is clobbered.
- **LLM failure**: fail-loud → bounded job retry/backoff; templated values remain as fallback.
- PII-free prompts; `LLM_API_KEY` never logged.

## Testing

- **Unit — mission handler** (stub): a mission with N tasks → all N reasons rewritten (stub markers);
  a task whose order is absent from the response keeps its templated reason; missing mission → no-op.
- **Unit — mission enqueue**: generating a mission with ≥1 task enqueues exactly one `ai.mission.reason`;
  an empty weekend mission enqueues none; a cached same-day GET does not re-enqueue.
- **Unit — health handler** (stub): pending-default rows → body rewritten; a run when all bodies are
  already non-default → **no LLM call** and no change; `accepted`/`dismissed` rows never touched.
- **Unit — health enqueue**: `recompute_health_score` enqueues one `ai.health.recommendations`.
- **E2E (stub), mission**: onboard → complete assessment (roadmap exists) → `GET /mission/today` →
  drain worker → `GET /mission/today` shows stub-marked reasons. Capture both GETs.
- **E2E (stub), health**: onboard → complete assessment → drain worker → fetch recommendations shows
  stub-marked bodies. Capture the list.
- All unit tests DB-clean (`db` fixture; no `SessionLocal()` on the app DB). Coverage ≥ 95%.

## Security & privacy

Triggers are internal (job worker); no new endpoints, so no new authz surface. Prompts carry only
business context (startup name/industry/stage, task/milestone titles, recommendation topics) — no PII.
`LLM_API_KEY` env-only, never logged.

## FE impact (integration guide)

- **Mission** (`docs/fe-integration-guide-*mission*`): `GET /mission/today` `tasks[].reason` is now
  written by AI shortly after a mission is generated; the first read right after generation may show the
  templated reason, which is replaced within seconds — re-fetch (or the scheduler pre-warms it at 06:00).
  Reason is `string | null`; still ≤300 chars. Payloads verbatim from captures.
- **Health** (`docs/fe-integration-guide-*health*`): recommendation `body` is now AI-personalized
  shortly after a recompute; `title` is unchanged (stable catalog headline). `accepted`/`dismissed` recs
  are never rewritten. Payloads verbatim from captures.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit/PR/issue/comment.
- Reproduce CI locally & green before push: black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%,
  **single alembic head unchanged — no migration**, e2e.
- DB-clean unit tests (`db` fixture, no `SessionLocal()` on the app DB).
- Worker no-commit convention (handlers end with `db.flush()`); seam fail-loud; enum/FK conventions unchanged.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads copied verbatim from live captures.

## Follow-ups

The remaining Module 03 consumers (dashboard briefing, onboarding panel, roadmap re-plan rationale);
Anthropic provider; optional: surface "AI-personalized" provenance to the FE if product wants a badge.
