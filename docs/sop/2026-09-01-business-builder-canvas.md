# SOP — Business Builder Canvas Core (Module 08, Slice 1)

**What shipped** — The founder's canvas toolkit: five structured strategy canvases
(`business_model`, `lean`, `value_prop`, `mission_vision`, `swot`) each backed by a typed block
scaffold. `GET /business-builder/overview` returns a 5-row completion grid across every canvas
type; `GET /business-builder/canvases/{type}` lazy-gets (and lazy-creates) one canvas with its
current blocks, its block definitions, and a completion summary; `PUT
/business-builder/canvases/{type}` full-replace-saves a canvas under optimistic-concurrency
versioning; `POST /business-builder/canvases/{type}/ai-fill` enqueues a deferred AI-fill job (no
worker consumes it yet — same seam pattern as every other `job_dispatcher.enqueue` call in this
codebase). One new table (`business_canvases`, migration `0011_business_canvases`).

Commits (branch `feat/business-builder-canvas`): `ac05463`..`b6c8544` (Tasks 1–5) + this task's
E2E/SOP/FE-guide/checklist commit (Task 6 — final task of the 6-task plan,
`.superpowers/sdd/2026-09-01-business-builder-canvas/`).

## Why

Founders need a structured place to think through their business model, not just a roadmap of
tasks — the classic Business Model Canvas, Lean Canvas, Value Proposition Canvas, Mission/Vision
statement, and SWOT are five standard strategy frameworks every startup eventually fills in, and
today the app has nowhere for that to live. Slice 1's job is the **core mechanic**: a canvas a
founder can open, see its blocks, fill them in, save them safely against concurrent edits, and
track completion — with the AI-assisted fill deliberately deferred behind the same
enqueue-only job seam every other not-yet-built AI feature in this codebase uses (Health Score's
`healthscore.initialize`, Roadmap's `roadmap.replan`, Mission's reason line — see those modules'
own SOPs). Building the real ai-fill worker now would be scope creep ahead of Module 03 (AI
Co-Founder), which is where every other deferred-AI seam in this app converges.

## How

**A generic canvas table + an in-code block registry, not five separate tables.** `BusinessCanvas`
(`app/db/models/business.py`) is one table with a `type` column (`CanvasType` enum) and a JSONB
`blocks` column — not five per-canvas-type tables, and not five separate Pydantic response models.
The per-type shape (which block keys exist, their labels, whether each is `"text"` or `"list"`)
lives entirely in `CANVAS_BLOCKS` (`app/services/business/canvas_defs.py`), a static
`dict[CanvasType, tuple[BlockDef, ...]]`. This is the same "generic table + code-level registry"
tradeoff Roadmap's Slice 2 made for its gallery templates
(`app/services/roadmap/gallery.py`'s `GALLERY_TEMPLATES`) rather than five migrations for five
schemas that are 90% identical (id/version/timestamps) and differ only in field names — adding a
sixth canvas type later is a `CANVAS_BLOCKS` entry plus a `CanvasType` enum member, not a
migration.

**`block_defs` is served on every read specifically so the FE never hardcodes a canvas's shape.**
`serialize_canvas` (`app/services/business/service.py:104`) always includes
`block_defs: [{key, label, kind}]` alongside `blocks` — the FE renders each canvas's form fields
by iterating `block_defs`, not by knowing in advance that `business_model` has 9 blocks starting
with `key_partners`. This is what lets `CANVAS_BLOCKS` change (reorder, relabel, or gain a block)
without an FE deploy — see the FE guide's "block_defs contract" section.

**Optimistic-concurrency versioning, not a lock.** `save_canvas`
(`app/services/business/service.py:81`) compares the caller's `expected_version` against the
row's current `version` and raises `CanvasVersionConflict` (409 `CANVAS_VERSION_CONFLICT`) on
mismatch — no `SELECT ... FOR UPDATE`, no server-side merge. A canvas is a single founder (or
small team) editing occasionally, not a high-contention money path, so the standard
codebase-wide `with_for_update()` pattern (used for jobs, mission-task races, etc.) would be
unnecessary machinery here; a version counter checked at save time is the right weight. The FE's
job on a 409 is reload-and-reapply — see the FE guide's optimistic-concurrency trap.

**PUT is deliberately full-replace, not a merge.** `save_canvas` does not merge the incoming
`blocks` into the existing row — it rebuilds from `empty_blocks(canvas.type)` and applies
`merged.update(blocks)` (`service.py:90-92`), so any block key **omitted** from the request body
is reset to its empty value. `tests/services/business/test_service.py` pins this behavior
explicitly (Task 2) so a future refactor toward "partial patch" semantics would have to
deliberately break a named test, not just happen by accident. This was picked over a
merge-on-save because a merge semantics silently masks a scenario the FE actually needs to know
about — the caller sending a stale version of *some* blocks — behind a keys-present check instead
of the version check that already exists for exactly this purpose. The tradeoff is that the FE
contract is strict: every save must resend the complete `blocks` object. See the FE guide's
prominent full-replace warning.

**Validation is per-block-kind, not per-canvas-type.** `validate_blocks`
(`service.py:31`) checks each incoming key against that canvas type's `CANVAS_BLOCKS` registry
(unknown key → 422) and its declared `kind` (`"text"` must be `str`, `"list"` must be
`list[str]` → 422) — generic across all five canvas types because the registry, not per-type
code, carries the shape. `validate_blocks` runs before the version check in `save_canvas`, so a
malformed body 422s even against a stale version (order matters for a client debugging a failed
save — it sees the real problem, not a conflict that happens to also be true).

**Completion is derived, not stored.** `completion()` (`service.py:63`) computes
`filled_blocks`/`total_blocks`/`completion_pct`/`status` (`start`/`continue`/`complete`) fresh
from `blocks` on every read — no `completion_pct` column, no denormalized cache. `_is_filled`
treats a non-empty string or non-empty list as filled; an empty list `[]` or empty string `""`
is not. This mirrors Health Score's own "recompute on read, no cache" choice
(`docs/sop/2026-08-19-health-score.md`) for the same reason: the source data (`blocks`) is small
and cheap to scan, so a cached derived column would only be an invalidation bug waiting to
happen.

**`business.artifact.completed` fires once, on the `not was_complete -> now_complete` edge.**
`save_canvas` computes completion before and after the save and publishes the event only on the
transition into `complete` (`service.py:96-100`) — not on every save of an already-complete
canvas, and not on a save that doesn't reach 100%. Same "guard on the transition, not the
action" pattern Dashboard's `write_activity` call sites use (`was_done`/`now_done and not
was_done` guards, `docs/sop/2026-08-31-dashboard.md`). Like every other `event_bus.publish` in
this codebase, this has no consumer yet — a future subscriber (Module 03, most likely, to
trigger an AI reaction to a completed canvas) is a mechanical follow-up, not a redesign.

**`ai-fill` writes a job row and nothing else.** `ai_fill_canvas`
(`app/api/v1/endpoints/business.py:76`) does not touch `BusinessCanvas` at all — it resolves the
startup, calls `job_dispatcher.enqueue(type="business.canvas.ai_fill", payload={startup_id,
canvas_type}, startup_id=...)`, commits, and returns `202` with `{job_id, status: "queued"}`.
`tests/api/test_business_canvases.py::test_ai_fill_enqueues_job_and_writes_no_canvas` asserts the
canvas table stays empty after the call — the endpoint's only side effect is the job row, so a
canvas isn't lazily created just by asking for an AI fill on a type the founder hasn't opened
yet. The FE polls the existing `GET /jobs/{id}` endpoint (`app/api/v1/endpoints/jobs.py`, no
`X-Workspace-Id` required — it scopes by the job's own `startup_id`), same contract every other
`job_dispatcher.enqueue` caller in this codebase already uses.

## What's involved

**Data model / migration**
- `alembic/versions/0011_business_canvases.py` — one new table, no lock on any existing table
  (autogenerated from the Task 1 ORM model; only revision id/`down_revision`/docstring
  hand-edited, per this project's standing migration convention).
  - `business_canvases` (+ standalone index `ix_business_canvases_startup_id`, + composite
    unique `uq_business_canvas_startup_type` on `(startup_id, type)` — one canvas row per
    startup per `CanvasType`). `startup_id` FK `ON DELETE CASCADE`; `type` is a
    `native_enum=False` VARCHAR(20), matching the codebase's standard StrEnum-column convention
    (no Postgres `CREATE TYPE`); `blocks` is JSONB; `version` is a plain `Integer` with
    `server_default="1"`.
- `app/db/models/business.py` — `BusinessCanvas`.
- `app/db/models/enums.py` — `CanvasType` (`business_model`/`lean`/`value_prop`/
  `mission_vision`/`swot`).
- Chains directly off `0010_dashboard` — `0011_business_canvases` is the current, sole alembic
  head (verified via `alembic heads`).

**Endpoints** (all under `/api/v1/business-builder`, `app/api/v1/endpoints/business.py`,
registered in `app/api/v1/api.py` with `prefix="/business-builder"`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/business-builder/overview` | any active member | read-only, creates no rows |
| GET | `/api/v1/business-builder/canvases/{type}` | any active member | lazy-creates the canvas row on first read; unknown `type` → 404 |
| PUT | `/api/v1/business-builder/canvases/{type}` | founder / team_member (editor) | full-replace save, versioned; stale `version` → 409 `CANVAS_VERSION_CONFLICT`; bad block → 422 |
| POST | `/api/v1/business-builder/canvases/{type}/ai-fill` | founder / team_member (editor) | 202, enqueues `business.canvas.ai_fill` job, writes no canvas row |

`_startup()` resolves the membership's `Startup` row (same pattern used across other endpoint
modules); `_parse_type()` converts the path `{type}` string to `CanvasType`, raising `NotFound()`
on an unrecognized value.

**Service** — `app/services/business/service.py`
- `get_or_create_canvas(db, startup, canvas_type) -> BusinessCanvas` — lazy-get/create.
- `validate_blocks(canvas_type, blocks) -> None` — per-block-kind 422 validation.
- `save_canvas(db, canvas, blocks, expected_version) -> BusinessCanvas` — validate → version
  check → full-replace merge → completion-transition event.
- `completion(canvas_type, blocks) -> dict` — derived `filled_blocks`/`total_blocks`/
  `completion_pct`/`status`.
- `serialize_canvas(canvas) -> dict` — the `{type, version, blocks, block_defs, completion}`
  response shape.
- `overview(db, startup) -> list[dict]` — the 5-row completion grid (read-only, no rows created
  for canvas types the founder hasn't opened).

**Block registry** — `app/services/business/canvas_defs.py` (`CANVAS_BLOCKS`, `BlockDef`,
`empty_blocks`).

**Errors** — one new code: `CanvasVersionConflict` / `CANVAS_VERSION_CONFLICT` (409). Reuses
`VALIDATION_ERROR` (422, malformed block), `NOT_FOUND` (404, unknown `type`), and `FORBIDDEN`
(403, mentor attempting a write) — no other new error codes.

**Events** — `business.artifact.completed` (`{startup_id, canvas_type}`), published on the
`not-complete -> complete` transition only. No consumer yet.

**Tests**
- `tests/services/business/test_canvas_defs.py` — registry shape, `empty_blocks` per type.
- `tests/services/business/test_service.py` — lazy-get/create, validation (unknown block, wrong
  kind), version-conflict raise, full-replace-not-merge semantics (pinned explicitly), completion
  transitions and the `business.artifact.completed` event, overview grid.
- `tests/api/test_business_canvases.py` — endpoint happy paths, unknown-type 404, membership
  403, editor-vs-mentor 403 on both write endpoints, stale-version 409, bad-block 422, ai-fill
  writes no canvas row.
- `e2e/test_business_builder.py` (`test_business_builder_journey`) — one live end-to-end journey
  against a real running server, capturing every response to
  `e2e/_captures/business/*.json`.

## Verification

- **Unit suite: 766 passed** (`poetry run pytest -q`) — 19 of those are business-builder-specific
  (`tests/services/business/`, `tests/api/test_business_canvases.py`); the rest is the unchanged
  baseline from every prior module.
- **Live E2E: 29 passed** (`COMPOSE_PROJECT_NAME=cofoundaz-api make e2e`, this task) —
  `e2e/test_business_builder.py::test_business_builder_journey` among them: a founder onboards →
  `GET /overview` shows all 5 canvas types `status == "start"` → `GET
  /canvases/business_model` lazy-creates it at v1 with its 9-block scaffold and matching
  `block_defs` → `PUT` with `{"blocks": {"key_partners": ["Stripe","AWS"]}, "version": 1}`
  bumps to v2 → `GET /overview` again shows `business_model` at `status == "continue"`
  (1/9 filled) while the other 4 types stay `"start"` → `POST .../ai-fill` returns 202 +
  `{job_id, status: "queued"}` → `GET /jobs/{id}` shows the same job, still `"queued"`. Every
  response captured verbatim to `e2e/_captures/business/{overview_empty,canvas_get,canvas_put,
  overview_after,ai_fill,ai_fill_job}.json` — the source for
  `docs/fe-integration-guide-business-builder.md`.
- `poetry run black --check app tests e2e`, `poetry run isort --check app tests e2e`,
  `poetry run ruff check .`, `poetry run mypy app` — all clean.
- Migration verified via the `make e2e` run above (fresh `cofoundaz_e2e` DB migrated
  `0010_dashboard -> 0011_business_canvases`); `alembic heads` confirms `0011_business_canvases`
  is the sole head.

## Operate / roll back

- No new env vars or deploy steps beyond the existing `alembic upgrade head` / `make e2e` flow.
- **Rollback:** `alembic downgrade -1` from `0011_business_canvases` drops
  `ix_business_canvases_startup_id` then `business_canvases`. **Lossy** — any canvas data written
  while `0011` was applied is destroyed on downgrade, same as every other brand-new-table
  migration in this project. Rolling back the migration without also reverting
  `app/api/v1/endpoints/business.py`'s registration in `app/api/v1/api.py` would make all four
  endpoints fail on the now-missing table; roll back both together, or accept that those
  endpoints will start raising until the code is reverted too.

## Follow-ups

**Deferred to later slices/modules (by design, not oversights):**
- **Slice 2 — typed artifacts.** Blocks are currently generic text/list JSON with no per-field
  structure beyond `"text"` vs `"list"` — richer field types (e.g. structured revenue-stream
  rows, dated milestones inside a canvas) are out of scope for Slice 1's core mechanic.
- **Slice 3 — suggestions + plan.** No suggestion engine, no cross-canvas consistency checking,
  and no plan-generation from a completed canvas exist yet — Slice 1 only gets the founder to a
  filled-in canvas, not what happens next with it.
- **Real `business.canvas.ai_fill` worker → Module 03 (AI Co-Founder).** The job is enqueued and
  persisted as `queued` and stays that way — no worker drains it, same as every other
  not-yet-consumed job in this codebase (`healthscore.initialize`, `roadmap.replan`). The FE
  guide marks this "derived/known-deferred", not a bug.
- **Version history.** `version` is a bare counter with no row-level history table — a founder
  can't see what a canvas looked like at v1 after saving v2. No `business_canvas_history` table
  was built; revisit if founders ask to see or restore prior versions.
- **No `business.artifact.completed` consumer.** The event fires on every canvas completing but
  nothing subscribes yet — same "publish now, consume later" pattern as every other
  not-yet-consumed `event_bus.publish` call in this codebase.
- **No per-block audit trail.** Unlike `write_activity`'s dashboard-facing action log, a canvas
  save does not write an activity-feed row (`mission.task.completed`-style) — "founder edited
  the Business Model canvas" doesn't show up in `GET /dashboard/activity` today. Worth
  reconsidering alongside Module 02's activity feed if founders want to see canvas edits there.
- **JSONB key order is not preserved.** Postgres JSONB does not preserve object-key insertion
  order — `blocks` in a captured response can come back in a different key order than
  `block_defs` or the request body (observed live — see the FE guide's field-nesting trap). Not
  a bug; documented so the FE doesn't build UI that assumes ordering.
