# SOP — Founder Dashboard (Module 02)

**What shipped** — The founder's home screen: `GET /dashboard/summary` (a single 9-section
aggregation of everything the founder already has — greeting, Health Score, today's mission,
upcoming roadmap milestones, KPIs, calibration, and three honest AI-empty-states) and
`GET /dashboard/activity` (a keyset-paginated team activity feed). No new domain logic — this
is an in-process aggregation BFF (backend-for-frontend) composing Modules 04 (Mission), 05
(Roadmap), 06 (Health Score), and 07 (Assessment), plus one new durable primitive:
`activity_log` + `write_activity()` (mirroring the existing `write_audit()` pattern), wired at
8 action sites across mission, roadmap, invitations, and assessment endpoints. One new table
(`activity_log`, migration `0010_dashboard`).

Commits (branch `feat/dashboard`): `52636c3`..`521e139` (Tasks 1–6) + this task's SOP/FE-guide/
checklist commit (Task 7 — final task of the 7-task plan,
`.superpowers/sdd/2026-08-31-dashboard/`).

## Why

By the time a founder finishes onboarding, four modules already know things about them —
Health Score has a number, Mission has today's 1–3 tasks, Roadmap has a plan with due dates,
Assessment knows whether calibration is done — but nothing brought them together. A founder
opening the app had to visit four separate screens to answer "what does my day look like."
Module 02's job: one aggregation endpoint that reads what already exists, composes it into a
single home-screen payload, and is honest about the two things that don't exist yet (an AI
briefing/risk/opportunity feed — Module 03 — and financial KPIs — Modules 09–11) rather than
faking them. A second endpoint, activity, answers "what has my team been doing" — which
required a genuinely new capability: nothing in the codebase durably recorded founder-visible
events before this module.

## How

**Aggregation BFF, not a new domain service.** `get_summary(db, startup, user)`
(`app/services/dashboard/service.py:102`) builds a dict literal by calling into the existing
services directly — `get_overview` (Health Score), `get_or_generate_today` + `serialize_mission`
+ `streak` (Mission) — plus two dashboard-local queries (`_upcoming`, `_tasks_done_this_week`)
and one direct read (`latest_completed_result` for calibration). There is no
`app/schemas/dashboard.py` — unlike Mission/Roadmap/Health Score, the response is a plain dict
built and returned as-is (FastAPI serializes it via the standard envelope). This is a deliberate
divergence from the rest of the codebase's Pydantic-response convention: the dashboard's shape
is a read-only composition of already-typed sections (each section's own service already
validates/types its own data before ever reaching this layer), so a dashboard-level schema would
mostly duplicate types that live in `app/schemas/mission.py` /
`app/services/health_score/service.py` for no additional safety. Worth reconsidering if the
dashboard ever needs to accept a request body.

**Per-section resilience — one card's failure can't 500 the page.** `_section(fn)`
(`service.py:43`) wraps `health`, `mission`, `upcoming`, and `kpis` in a bare
`try/except Exception` that returns `{"error": True}` on failure rather than propagating. This
is the one place in the codebase that deliberately swallows an exception without a Sentry
capture or re-raise — a conscious tradeoff for a page that aggregates four independently-owned
subsystems: a bug or outage in, say, Health Score's `get_overview` must not blank the whole
dashboard when Mission and Roadmap are fine. `greeting` and `calibration` are cheap/pure enough
they're built unwrapped; `briefing`/`risks`/`opportunities` are static dicts with no failure
mode at all. See Follow-ups for the observability gap this creates.

**`activity_log` + `write_activity()`, deliberately mirroring `write_audit()`.**
`app/db/models/activity.py` (`ActivityLog`) and `app/platform/activity.py` (`write_activity`)
are new, but structurally copy the existing audit-log pattern (`app/core/audit.py`
`write_audit`) rather than inventing a new persistence idiom: same `(startup_id, actor_user_id,
action, entity_type, entity_id, summary, meta)` shape, same `db.add` + `db.flush` (no commit —
the caller's own `db.commit()` covers it), same soft, un-FK'd `entity_id` (the migration's
docstring spells out why: an activity row must survive its referenced entity being edited,
regenerated, or deleted — the same rationale `mission_tasks.roadmap_task_id` already uses).
`activity_log` is a distinct table from `audit_log`, not a rename or a merge — audit is an
internal/compliance trail; activity is the founder-facing "what happened" feed this endpoint
serves. They happen to share a shape because that shape is already proven, not because they're
the same concept.

**Eight call sites, not a generic event-bus subscriber.** `write_activity()` is called
explicitly at each of the 8 places a founder-visible action actually happens — see What's
involved below. This was a deliberate choice over subscribing to the existing in-process
`event_bus` (which already publishes `mission.task.completed`, `roadmap.milestone.completed`,
etc.): the event bus has no consumer/persistence layer yet (every module's SOP notes this,
e.g. `docs/sop/2026-08-26-todays-mission.md` Follow-ups), so wiring activity logging through it
would mean building that consumer machinery as a side effect of this module. Explicit call sites
are more code to touch per future action, but ship today without a new subsystem; migrating to
an event-bus consumer once one exists is a mechanical follow-up, not a redesign.

**The `assessment.completed` activity is gated on `claimed`, not on the call succeeding.**
`complete_assessment` (`app/services/assessment/service.py:133`) already returns
`(result, claimed)` — `claimed` is `True` only for the caller whose atomic UPDATE actually won
the `in_progress -> completed` transition (see that function's own docstring, added for this
reason). `post_complete_assessment` (`app/api/v1/endpoints/assessments.py:116`) writes the
activity row only when `claimed` is `True`. Without this gate, a client retrying an already-
completed `POST /complete` (idempotent by design — it just returns the same result) would write
a duplicate "completed the startup assessment" row into the feed on every retry. The same
`was_done` guard pattern is used for `mission.task.completed`
(`app/api/v1/endpoints/mission.py`) so re-completing an already-done task doesn't duplicate
either.

**Keyset pagination on `(created_at, id)`, base64-opaque cursor.** `get_activity`
(`service.py:146`) orders `ActivityLog.created_at DESC, ActivityLog.id DESC` and pages with a
`WHERE (created_at, id) < (cursor_ts, cursor_id)` compound predicate — not `OFFSET`, which would
skip or duplicate rows if new activity is written between page reads. The cursor is
`base64(f"{created_at.isoformat()}|{row_id}")` — opaque to the client, decoded and validated on
the way in (`_decode_cursor`, `service.py:132`) with a malformed cursor raising `422
VALIDATION_ERROR` rather than a 500 or a silent full-reset. `limit` is clamped to `1..50`
server-side (`max(1, min(limit, 50))`) regardless of what the query string requests, in addition
to FastAPI's own `Query(ge=1, le=50)` — belt-and-suspenders since the service function is also
unit-tested directly, independent of the endpoint's query validation.

**Actor resolution via one outer join, never per-row.** `get_activity` outer-joins
`UserProfile` on `actor_user_id` in the same query that fetches the page (`service.py:150-155`)
rather than resolving each actor's name with a follow-up query per row — avoiding an N+1 over
the page. `actor` is `null` when `actor_user_id` is `null` (a system-authored row — no call site
in this module writes one yet, but `ActivityLog.actor_user_id` is nullable by design and
`test_activity_system_row_has_null_actor` exercises the shape).

**`upcoming` window is intentionally narrow — 7 days, no override.** `UPCOMING_WINDOW_DAYS = 7`
(`service.py:19`) is a hardcoded module constant, not a per-workspace setting. `_upcoming`
(`service.py:50`) selects non-`done` roadmap milestones with `due_on` inside `[today, today+7]`
across all phases of the caller's roadmap. This is why the capture shows `upcoming: []` — see
the FE guide's honesty note for the mechanics.

## What's involved

**Data model / migration**
- `alembic/versions/0010_dashboard.py` — one new table, no lock on any existing table
  (autogenerated from the Task 1 ORM model; only revision id/`down_revision`/docstring
  hand-edited, per the same convention every prior migration in this project follows).
  - `activity_log` (+ composite index `ix_activity_log_startup_created` on
    `(startup_id, created_at, id)`, matching the query's own sort/filter columns exactly).
    `startup_id` FK `ON DELETE CASCADE`; `actor_user_id` FK to `users.id`, no cascade (a
    deleted user blocks, not cascades — consistent with `roadmap_milestones.owner_id`);
    `entity_id` is a bare UUID with **no** FK (see How, above).
- `app/db/models/activity.py` — `ActivityLog`.
- Chains directly off `0009_mission` — `0010_dashboard` is the current, sole alembic head
  (verified via `alembic heads`).

**Endpoints** (both under `/api/v1/dashboard`, `app/api/v1/endpoints/dashboard.py`, registered
in `app/api/v1/api.py` at `prefix="/dashboard"`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/dashboard/summary` | any active member | commits after read — `get_or_generate_today` may lazily materialise today's mission |
| GET | `/api/v1/dashboard/activity` | any active member | `cursor`/`limit` query params, `limit` clamped 1–50 |

Both gate on `require_workspace` (any active member — founder, team_member, **and mentor** can
read the dashboard; there are no dashboard writes in this module). `_startup()` resolves the
membership's `Startup` row and 404s if somehow missing — the same pattern used across other
endpoint modules.

**Service** — `app/services/dashboard/service.py`
- `get_summary(db, startup, user) -> dict` — the 9-section aggregation.
- `get_activity(db, startup, cursor, limit) -> dict` — keyset pagination.
- `_section`, `_upcoming`, `_tasks_done_this_week`, `_mission_section`, `_salutation`,
  `_first_name`, `_encode_cursor`, `_decode_cursor` — internal helpers.
- `UPCOMING_WINDOW_DAYS = 7`.

**Platform** — `app/platform/activity.py` (`write_activity`), `app/db/models/activity.py`
(`ActivityLog`).

**The 8 `write_activity` call sites** (all additive edits to existing, stable endpoint code —
the only production-code edits in this module besides the new dashboard/activity files):

| Action | Site | Gate |
|---|---|---|
| `mission.task.added` | `app/api/v1/endpoints/mission.py:162` (`POST /missions/tasks`) | unconditional |
| `mission.task.completed` | `mission.py:191` (`PATCH /missions/tasks/{id}`, `action=complete`) | `was_done` guard — idempotent re-complete doesn't duplicate |
| `mission.task.snoozed` | `mission.py:202` (`action=snooze`) | unconditional |
| `mission.task.rejected` | `mission.py:229` (`action=reject`) | unconditional |
| `roadmap.milestone.completed` | `app/api/v1/endpoints/roadmap.py:481` (`PATCH /roadmap/milestones/{id}`) | `now_done and not was_done` guard |
| `roadmap.replanned` | `roadmap.py:666` (`POST /roadmap/replan/apply`) | `result["applied"]` non-empty |
| `member.joined` | `app/api/v1/endpoints/invitations.py:28` (`POST /invitations/accept`) | unconditional |
| `assessment.completed` | `app/api/v1/endpoints/assessments.py:116` (`POST /assessments/{id}/complete`) | `claimed` (atomic-claim gate, see How) |

**`complete_assessment`'s return-shape change** — `app/services/assessment/service.py:133`
changed from returning `dict[str, Any]` to `tuple[dict[str, Any], bool]` (`result, claimed`),
so `post_complete_assessment` can gate the new activity write on which caller actually won the
completion race. This is a signature change to an existing, already-shipped function — its one
existing call site (`post_complete_assessment`) was updated in the same commit; no other caller
exists.

**Errors** — reuses the existing ad-hoc `VALIDATION_ERROR` `AppError` shape (422 — malformed
activity cursor). No new error codes.

**Tests**
- `tests/services/test_dashboard_summary.py` — section shape, upcoming-window filtering,
  `tasks_done_this_week` date logic, per-section failure isolation.
- `tests/api/test_dashboard_summary.py` — endpoint happy path, membership/verified-email gates.
- `tests/api/test_dashboard_activity.py` — newest-first ordering + pagination, empty workspace,
  cross-tenant isolation, null-actor system row, malformed-cursor 422 (two cases: invalid
  base64, and valid base64 with no `|` separator).
- `e2e/test_dashboard.py` (`test_dashboard_journey`) — one live end-to-end journey against a
  real running server, capturing every response to `e2e/_captures/dashboard/*.json`.
- `tests/factories.py` gained `create_activity` (Task 1).

## Verification

- **Unit suite: 745 passed** (`poetry run pytest -q`) — unchanged from Task 6's baseline; this
  task added no unit tests, docs only.
- **Live E2E: 28 passed** (`make e2e`, verified in Task 6, `test_dashboard.py::test_dashboard_journey`
  among them) — founder onboards to stage `validation` (roadmap auto-generates inline) →
  completes the kickoff assessment with deliberately weak, unconditional-only answers (same
  fixture as `e2e/test_health_score.py`) so Health Score lands in a real `ok` state →
  `GET /dashboard/summary` returns exactly the 9 documented top-level keys, `health.status ==
  "ok"` with an int score, `mission` non-null with 1–3 roadmap-drawn tasks, `upcoming` a list,
  `calibration.assessment_complete is True`, `briefing`/`risks`/`opportunities` all
  `status: "empty"` → completes one mission task → `GET /dashboard/activity` shows that
  completion as the newest item, attributed to the founder by id and by name. Both responses
  captured verbatim to `e2e/_captures/dashboard/{summary,activity}.json` — the source for
  `docs/fe-integration-guide-dashboard.md`. Not re-run for this docs-only task per the brief.
- `poetry run black --check app tests`, `poetry run isort --check app tests`,
  `poetry run ruff check .`, `poetry run mypy app` — all clean (re-verified in this task, see
  below).
- Migration verified via every `make e2e` run (fresh `cofoundaz_e2e` DB migrated
  `0009_mission → 0010_dashboard`); `alembic heads` confirms `0010_dashboard` is the sole head.

## Operate / roll back

- No new env vars or deploy steps beyond the existing `alembic upgrade head` / `make e2e` flow.
- **Rollback:** `alembic downgrade -1` from `0010_dashboard` drops `ix_activity_log_startup_created`
  then `activity_log`. **Lossy** — any activity data written while `0010` was applied is
  destroyed on downgrade, same as every other brand-new-table migration in this project (`0004`
  through `0009`). Rolling back the migration without also reverting the 8 `write_activity` call
  sites would make every one of those endpoints fail on the now-missing table; roll back both
  together, or accept that those endpoints will start raising until the code is reverted too.

## Follow-ups

**Deferred to later modules (by design, not oversights):**
- **AI briefing / risks / opportunities → Module 03.** All three are static empty-state dicts
  today (`_BRIEFING_EMPTY`, `_RISKS_EMPTY`, `_OPPS_EMPTY`, `service.py:21-26`) — no AI panel,
  no risk-detection logic, no opportunity-scanning exists yet. The response shape
  (`{"status": "empty" | ?, "message": ...}`) is stable so Module 03 can populate a non-`empty`
  status without an FE contract change, but nothing in this module drives that transition.
- **Financial KPIs (`revenue`, `runway`, `pipeline_value`, `campaign_performance`) → Modules
  09–11.** All four are hardcoded `null` in `get_summary`'s `kpis` section — no financial
  tracking, no CRM/pipeline module, no campaign module exists yet. Only `tasks_done_this_week`
  is a real, live-computed value in v1.
- **Realtime activity delivery → Module 20.** The activity feed is pull-only
  (`GET /dashboard/activity`, keyset-paginated) — there is no websocket/push/polling-hint
  contract yet. A founder must refresh to see new team activity.
- **Widget-level role/grant filtering** — every section of `get_summary` is currently visible to
  every active member (founder, team_member, mentor) with no per-widget role gate. Whether a
  mentor should see, e.g., financial KPIs once they're real (Modules 09–11) is an open product
  question deferred to those modules rather than pre-decided here.
- **`kpi_snapshots` / `briefings` tables deliberately not built.** Both were candidate schema
  additions during design but were deferred — v1 needs no persistence for briefing/risk/
  opportunity content (it's static) and no persistence for KPI history (only one live value,
  `tasks_done_this_week`, computed fresh on every read). Building either table now would be
  speculative ahead of Modules 03/09–11's actual requirements.
- **`_section`'s bare `except Exception: pass`-equivalent has no Sentry capture.** This is a
  deliberate exception to the codebase's usual "never swallow, log + Sentry-capture before
  re-raising or returning a sanitised error" rule (see project conventions) — the tradeoff
  favors page resilience over per-card observability. A future pass should at minimum log the
  swallowed exception (with `request_id`) so an ops dashboard can see which section is
  degrading, without changing the founder-facing behavior of `{"error": true}`.
- **No dedicated `app/schemas/dashboard.py`.** The summary/activity response bodies are plain
  dicts, not Pydantic response models — see How, above, for the reasoning. Revisit if the
  dashboard ever needs a typed request body (e.g. per-widget preferences).
- **`write_activity` call sites are manual, not event-bus-driven.** Every future founder-visible
  action (new modules, new endpoints) needs its own explicit `write_activity()` call — there is
  no automatic "anything published to `event_bus` also lands in the activity feed" bridge.
  Revisit once the event bus gains a real persistent consumer (Module 20) — at that point,
  activity logging could migrate to a bus subscriber instead of scattered call sites.
- **Workspace-timezone base date** — `_upcoming`'s `date.today()` and `_tasks_done_this_week`'s
  7-day lookback both use the server's local/UTC date/time, not a workspace-configured timezone
  (no such setting exists yet), same caveat every other module in this codebase already carries
  (Mission, Roadmap).
