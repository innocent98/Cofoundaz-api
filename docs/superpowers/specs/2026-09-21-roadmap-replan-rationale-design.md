# Module 03 — Roadmap re-plan rationale (design)

**Status:** approved-for-planning
**Date:** 2026-09-21
**Module:** 03 (AI Co-Founder) consumer — an AI-authored "why we re-planned" rationale on a roadmap re-plan.
**Depends on:** Module 03 Slice 1 (LLM seam, `complete`) — shipped; the job worker; Module 05 (Roadmap, incl. `apply_replan` + `RoadmapReplan`) — shipped.

## Goal

When a founder applies a roadmap re-plan (`POST /roadmap/replan/apply`), attach an AI-authored
holistic rationale explaining why the dates shifted — async-upgraded onto the `RoadmapReplan` record
via the assessment-narrative pattern (templated fallback written synchronously, AI prose overwrites
it moments later). Also remove the dead `roadmap.replan` job enqueued at assessment-complete (it has
no handler).

One sentence: *the assessment-narrative async-upgrade pattern, applied to a new `rationale` field on
`RoadmapReplan`, filled by a `complete()` call over the applied changes.*

## Why

- The roadmap FE guide's own Follow-ups already reserve "AI-authored rationale" for the re-plan
  `reason`/`summary`, which are templated today (`"Re-planned N milestones"`, `"10 days overdue and
  not yet done."`). This slice delivers that reserved rationale.
- It reuses the shipped free-text seam (`complete`) with no new capability, and it's the natural next
  Module 03 consumer.
- The `roadmap.replan` job enqueued at assessment-complete (`app/services/assessment/service.py`) has
  **no registered handler** — a queued job nothing runs. It is unrelated to this rationale (auto-replan
  applies no changes, so no `RoadmapReplan` row exists at assessment time); removing the dead enqueue
  keeps the queue honest.

## Scope

### In scope
1. **New `rationale` Text column** (nullable) on `RoadmapReplan` (+ migration `0028`).
2. **Enqueue on apply** — `apply_replan` writes a templated `rationale` on the new `RoadmapReplan` row
   and enqueues `ai.roadmap.rationale`.
3. **Worker `handle_roadmap_rationale`** — loads the row, builds a PII-free context from its `changes`
   + roadmap stage, one `complete()` call → overwrites `rationale`.
4. **Expose `rationale`** on `GET /replan/history` (and the `apply` response `result`).
5. **Remove the dead `roadmap.replan` enqueue** at assessment-complete (and update the concurrency
   test's expected job set).
6. Tests (unit + one live e2e against the stub), roadmap FE guide update, SOP, checklist.

### Out of scope (deferred)
- Upgrading per-change `reason` strings inside the `changes` JSONB (we add a holistic record-level
  rationale instead).
- Repurposing the `roadmap.replan` job (auto-drift advisory) — a separate product/design pass.
- A rationale on the `preview` step (no record exists until apply).
- The remaining Module 03 consumer (onboarding AI panel).

## Architecture

### 1. Data model — `app/db/models/roadmap.py` + migration

Add to `RoadmapReplan`:
```python
rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
```
Nullable so pre-existing rows (and any all-stale/empty apply that writes no row) need no backfill.
Migration `0028_roadmap_replan_rationale` (`--autogenerate`, then renumber): `op.add_column` /
`op.drop_column` on `roadmap_replans`. Single linear head off `0027_daily_briefings`.

### 2. Enqueue on apply — `app/services/roadmap/replan.py`

In `apply_replan`, where the `RoadmapReplan` row is created (only when `applied` is non-empty), set an
instant templated fallback and enqueue the AI job:
```python
replan = RoadmapReplan(..., summary=summary, rationale=_templated_rationale(summary, snapshot))
db.add(replan); db.flush()
replan_id = str(replan.id)
job_dispatcher.enqueue(db, "ai.roadmap.rationale", {"replan_id": replan_id}, roadmap.startup_id)
```
`_templated_rationale(summary, snapshot)` — a one-line non-null fallback derived from the applied
changes (e.g. the summary plus the count and the dominant slip), so the FE always has sensible prose
even if the AI job never completes. `job_dispatcher` imported from `app.platform.jobs` (mirrors the
assessment service). No `db.commit` here — the endpoint commits.

### 3. Worker — `app/worker/handlers/ai.py` (extend)

`handle_roadmap_rationale(db, job)`:
1. `replan = db.get(RoadmapReplan, job.payload["replan_id"])`; no-op if missing.
2. Load the roadmap (for `stage`) and startup (name/industry/stage) via `replan.roadmap_id`.
3. `messages = build_roadmap_rationale_messages(stage=..., name=..., industry=..., changes=replan.changes)`
   — PII-free: startup name/industry/stage + each change's `title`, `old_due`, `new_due`, `reason`.
4. `text = get_llm_client().complete(messages, max_tokens=settings.LLM_MAX_TOKENS)`.
5. `replan.rationale = text.strip()`; `db.flush()`. Fail-loud on the LLM call (runner owns the txn).

Register: `register_handler("ai.roadmap.rationale", handle_roadmap_rationale)`.

**Prompt builder** in `app/services/roadmap/ai_rationale.py`:
`build_roadmap_rationale_messages(*, stage, name, industry, changes) -> list[LLMMessage]` — mirrors
`build_narrative_messages`; asks for a short (2-3 sentence) reassuring, concrete "why these dates
moved and what it means" rationale.

### 4. Expose the field

`GET /replan/history` serialization (`app/api/v1/endpoints/roadmap.py`) and the `apply` result gain
`"rationale": r.rationale` (string | null). `apply_replan`'s returned dict adds `rationale` alongside
`summary`.

### 5. Remove the dead job

Delete `job_dispatcher.enqueue(db, "roadmap.replan", job_payload, startup.id)` at
`app/services/assessment/service.py`. Update `tests/services/assessment/test_complete_concurrency.py`
(and any other test/e2e) whose expected completion-job set lists `"roadmap.replan"` — after removal the
set is `["ai.assessment.narrative", "ai.health.recommendations"]` (sorted). Grep for `roadmap.replan`
across tests/e2e and reconcile every assertion.

### Data flow

```
POST /replan/apply → apply_replan applies changes → RoadmapReplan row (summary + templated rationale)
    → enqueue ai.roadmap.rationale {replan_id}   … worker …
  handle_roadmap_rationale → complete() → overwrite rationale
GET /replan/history → AI rationale (templated rationale is the instant value + fallback)
```

## Error handling

- **No row on empty apply**: an all-stale/empty apply writes no `RoadmapReplan` and enqueues nothing
  (unchanged behavior).
- **Idempotency**: one job per applied re-plan (a fresh row each apply); the handler overwrites
  `rationale` — a re-run is harmless.
- **Fallback**: the templated `rationale` is the instant value; if the job fails, it remains and the
  job retries (bounded backoff). Pre-existing rows have `rationale = null` (FE renders nothing).
- **LLM failure**: fail-loud → retry; nothing partial persists.
- PII-free prompt (milestone titles/dates are business content); `LLM_API_KEY` never logged.

## Testing

- **Unit — builder**: `build_roadmap_rationale_messages` is PII-free and includes the change context.
- **Unit — handler** (stub + fake LLM): a replan row → `rationale` overwritten with AI text; stub
  marks it `[stub-llm] …`; missing replan → no-op; LLM error → fail-loud.
- **Unit — apply enqueue**: applying a real drift change writes a `RoadmapReplan` with a non-null
  templated `rationale` and enqueues exactly one `ai.roadmap.rationale`; an all-stale/empty apply
  enqueues none.
- **Unit — dead-job removal**: completing an assessment no longer enqueues `roadmap.replan`
  (concurrency test's expected set updated).
- **E2E (stub)**: onboard → force a milestone slip → `POST /replan/preview` → `POST /replan/apply`
  (rationale templated) → drain worker → `GET /replan/history` shows the `[stub-llm]` rationale.
  Capture apply + history.
- DB-clean unit tests (`db` fixture; no `SessionLocal()` on the app DB). Coverage ≥ 95%.

## Security & privacy

Trigger is the already-`_editor`-gated apply endpoint; the worker adds no endpoint. Prompt carries
only business context (startup name/industry/stage, milestone titles/dates/slip reasons) — no PII.
`LLM_API_KEY` env-only, never logged.

## FE impact (integration guide)

Update `docs/fe-integration-guide-roadmap.md` §9: `GET /replan/history` items and the `apply` response
now carry `rationale` (string | null) — a holistic AI-authored "why we re-planned" narrative,
templated instantly and AI-upgraded within seconds (re-fetch history to pick it up). Render as opaque
prose (as the guide already advises for `reason`). Note the deprecation/removal of the never-run
`roadmap.replan` job if the guide mentions it. Payloads verbatim from captures.

## Global constraints (carried into the plan)

- **No AI attribution** in any commit/PR/issue/comment.
- Reproduce CI locally & green before push: black/isort/ruff, mypy, pylint ≥ 9.5, bandit, pytest ≥ 95%,
  **Migrations (fresh-DB round-trip + `alembic check` drift) — one new migration `0028`, single head**, e2e.
- DB-clean unit tests (`db` fixture, no `SessionLocal()` on the app DB).
- Worker no-commit convention (handlers end with `db.flush()`); seam fail-loud; enum/FK conventions.
- SOP + checklist + FE guide updated in the same pass; FE-guide payloads copied verbatim from live captures.

## Follow-ups

Per-change `reason` AI upgrade; repurposing the `roadmap.replan` job (auto-drift advisory); the last
Module 03 consumer (onboarding AI panel).
