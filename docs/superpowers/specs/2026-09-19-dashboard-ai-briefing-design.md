# Module 03 — Dashboard AI briefing (design)

**Status:** approved-for-planning
**Date:** 2026-09-19
**Module:** 03 (AI Co-Founder) consumer — the dashboard `briefing` / `risks` / `opportunities` sections.
**Depends on:** Module 03 Slice 1 (LLM seam) + Slice 2 (structured output `complete_json`) — shipped; the job worker; Module 02 (Dashboard) + Module 06 (Health Score) + Module 04/05 (Mission/Roadmap) — shipped.

## Goal

Turn the three currently-static dashboard sections — `briefing`, `risks`, `opportunities`
(hardcoded `{"status": "empty", "message": <fixed string>}` in `app/services/dashboard/service.py`)
— into AI-authored daily text, generated once per day per startup via the LLM, using the
async-upgrade pattern (instant placeholder + async fill + graceful fallback).

One sentence: *a daily briefing row, lazily generated on the first dashboard load of the day when
the startup has a completed assessment, filled asynchronously by one structured `complete_json`
call that writes all three sections.*

## Why

- The dashboard's AI panel was scaffolded FE-compatible but never implemented: the sections always
  return `status: "empty"`. The dashboard FE guide explicitly notes the `{status, message}` shape is
  stable "so Module 03 can later swap in a non-`empty` status without breaking the FE contract" —
  this slice is exactly that swap.
- It reuses the shipped LLM seam (`complete_json`) and the daily-generate + async-upgrade patterns
  already proven by missions and the assessment narrative. It is the natural next Module 03 consumer
  after mission-reason + health-recommendations.

## Scope

### In scope
1. **New `daily_briefings` table** (+ migration) — one row per (startup, date), holding the three
   text fields and a status.
2. **Lazy generation** — on `GET /dashboard/summary`, when the startup has a completed assessment
   and no briefing exists for today, create a `generating` row and enqueue `ai.dashboard.briefing`.
3. **Worker `handle_dashboard_briefing`** — synthesizes a PII-free context, one `complete_json` call
   → `{briefing, risks, opportunities}`, writes them + `status="ready"`.
4. **`get_summary` wiring** — return the stored briefing (`generating` / `ready`) instead of the
   static block; keep the static `empty` block for startups with no completed assessment.
5. Tests (unit + one live e2e against the stub), dashboard FE guide update, SOP, checklist.

### Out of scope (deferred)
- `risks`/`opportunities` as structured lists (v1 keeps the existing single-`message` string shape).
- A scheduled 06:00 pre-warm (lazy-on-read only in v1).
- Regeneration within a day / manual refresh; multi-day history.
- A "briefing ready" notification (silent; the FE polls the summary it already loads).
- The other Module 03 consumers (onboarding AI panel, roadmap re-plan rationale).

## Architecture

### 1. Data model — `app/db/models/dashboard.py` (new) + migration

`DailyBriefing(UUIDMixin, TimestampMixin, Base)`:
- `startup_id: Mapped[uuid.UUID]` — FK `startups.id` `ondelete="CASCADE"`, `index=True`.
- `briefing_date: Mapped[date]`.
- `status: Mapped[BriefingStatus]` — `Enum(BriefingStatus, native_enum=False, length=12)`, values
  `generating | ready | failed`, default `generating`.
- `briefing: Mapped[str]` (Text), `risks: Mapped[str]` (Text), `opportunities: Mapped[str]` (Text) —
  the three section messages; hold the instant placeholder text until the AI job overwrites them.
- `__table_args__ = (UniqueConstraint("startup_id", "briefing_date", name="uq_briefing_startup_date"),)`
  — mirrors `missions`, makes lazy generation idempotent under concurrent dashboard loads.

`BriefingStatus` added to `app/db/models/enums.py`.

**Migration** authored with `--autogenerate` (single new table), verified drift-clean. This is the
first Module 03 slice with a migration; it must keep the single alembic head linear.

### 2. Trigger & lazy generation — `app/services/dashboard/service.py`

A `get_or_generate_briefing(db, startup) -> DailyBriefing | None`:
- If a completed assessment does not exist (`latest_completed_result(db, startup.id) is None`) →
  return `None` (caller renders the static `empty` block, unchanged).
- Else look up today's row by `(startup_id, briefing_date=today)`; if present, return it.
- Else create a `generating` row with instant placeholder text in all three fields
  (e.g. briefing = "Putting together your briefing…"), `db.flush()`, and
  `job_dispatcher.enqueue(db, "ai.dashboard.briefing", {"startup_id": ..., "briefing_date": ...}, startup.id)`.
  Return the row. Enqueue exactly once per row (the cached-existing branch never re-enqueues); the
  unique constraint + a caught `IntegrityError` (same guard style as the existing dashboard mission
  race handling) collapse concurrent first-loads to one row/one job.

`get_summary` replaces the static `briefing/risks/opportunities` block:
```
b = _section_isolated(db, lambda: get_or_generate_briefing(db, startup))
if b is None:  # no assessment yet
    briefing = {"status": "empty", "message": _BRIEFING_EMPTY}   # unchanged statics
    risks = {"status": "empty", "message": _RISKS_EMPTY}
    opportunities = {"status": "empty", "message": _OPPS_EMPTY}
else:
    briefing = {"status": b.status.value, "message": b.briefing}
    risks = {"status": b.status.value, "message": b.risks}
    opportunities = {"status": b.status.value, "message": b.opportunities}
```
`GET /dashboard/summary` already `db.commit()`s (endpoint) after `get_summary`, so the row + job
persist. The whole thing stays inside `_section_isolated` so a briefing failure can never 500 the
dashboard.

### 3. Worker — `app/worker/handlers/ai.py` (extend)

`handle_dashboard_briefing(db, job)`:
1. `startup = db.get(Startup, job.payload["startup_id"])`; no-op if missing.
2. Load the row by `(startup_id, briefing_date=payload["briefing_date"])`; no-op if missing or
   `status != generating` (idempotent — a re-run after `ready` does nothing).
3. Build a PII-free context via `build_dashboard_briefing_messages(...)`: startup name/industry/stage
   + health score/band (from the `HealthScore` row if present), today's mission task counts
   (total / done), count of milestones due within 7 days, tasks-done-this-week. No user names, no PII.
4. `result = get_llm_client().complete_json(messages, schema=dashboard_briefing_schema(), max_tokens=...)`.
5. Write `row.briefing/risks/opportunities` from the returned `{briefing, risks, opportunities}`
   (each a required string), set `row.status = ready`, `db.flush()`. Fail-loud on the LLM call.

Register: `register_handler("ai.dashboard.briefing", handle_dashboard_briefing)`.

**Prompt builder + schema** in `app/services/dashboard/ai_briefing.py`:
- `build_dashboard_briefing_messages(*, name, industry, stage, health_score, health_band, mission_total, mission_done, upcoming_count, tasks_done_week) -> list[LLMMessage]`.
- `dashboard_briefing_schema() -> dict` — strict object with required string fields `briefing`,
  `risks`, `opportunities`, `additionalProperties: false`. (Stub → `"[stub-llm] briefing"` etc.)

### Data flow

```
GET /dashboard/summary → get_or_generate_briefing:
    (assessment complete? no → static empty block)
    (today's row exists? yes → return it) else create generating row + enqueue ai.dashboard.briefing
  … worker … handle_dashboard_briefing → complete_json → write 3 fields + status=ready
GET /dashboard/summary (re-fetch) → status "ready" with AI text
```

## Error handling

- **Gating**: no completed assessment → no row, no job, static empty message (no LLM cost for empty
  workspaces).
- **Idempotency**: one row/job per (startup, day) via the unique constraint + IntegrityError guard;
  the handler no-ops unless `status == generating`.
- **Fallback**: `generating` placeholder text is the instant value; if the job fails, the row stays
  `generating` (placeholder shown) and the job retries — the dashboard never 500s (`_section_isolated`).
- **LLM failure**: fail-loud → bounded job retry/backoff; nothing partial persists (handler writes all
  three fields together, then `status=ready`).
- PII-free prompt; `LLM_API_KEY` never logged.

## Testing

- **Unit — schema/builder**: `dashboard_briefing_schema()` shape (3 required strings, additionalProps
  false); builder is PII-free and includes the context values.
- **Unit — handler** (fake LLM + stub): `generating` row → 3 fields written + `status=ready`; no-op
  when row missing / already `ready` / startup missing; fail-loud on LLM error.
- **Unit — lazy generation**: no assessment → returns None, no row, no job; with assessment → one
  `generating` row + exactly one `ai.dashboard.briefing` job; a second same-day call re-enqueues
  nothing.
- **Unit — get_summary wiring**: no assessment → static empty block; generating row → `status:
  "generating"` messages; ready row → `status: "ready"` AI messages.
- **E2E (stub)**: onboard → complete assessment → `GET /dashboard/summary` (briefing `generating`) →
  drain worker → `GET /dashboard/summary` shows `status: "ready"` with `[stub-llm]` briefing. Capture
  both summaries.
- DB-clean unit tests (`db` fixture; no `SessionLocal()` on the app DB). Coverage ≥ 95%.

## Security & privacy

Trigger is the already-authorized dashboard read; the worker adds no endpoint. Prompt carries only
business/state context (name/industry/stage, health score/band, counts) — no PII. `LLM_API_KEY`
env-only, never logged.

## FE impact (integration guide)

Update `docs/fe-integration-guide-dashboard.md`: `briefing`/`risks`/`opportunities` can now return
`status: "generating"` (show a subtle loading state; `message` is a friendly placeholder) or
`status: "ready"` (render `message` as the AI text), in addition to the existing `"empty"` (no
assessment yet). The FE should re-fetch `GET /dashboard/summary` shortly after first load to pick up
the `ready` transition (no separate endpoint/poll cadence needed beyond the dashboard's own refresh).
Payloads pasted verbatim from the e2e captures.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit/PR/issue/comment.
- Reproduce CI locally & green before push: black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%,
  **Migrations (fresh-DB round-trip + `alembic check` drift) — one new migration, single head linear**, e2e.
- DB-clean unit tests (`db` fixture, no `SessionLocal()` on the app DB).
- Worker no-commit convention (handlers end with `db.flush()`); seam fail-loud; enum/FK conventions
  (`Enum(..., native_enum=False, length=…)`, FK `index=True`) as in the codebase.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads copied verbatim from live captures.

## Follow-ups

`risks`/`opportunities` as structured lists; a 06:00 scheduled pre-warm; intra-day regeneration/manual
refresh; the remaining Module 03 consumers (onboarding AI panel, roadmap re-plan rationale).
