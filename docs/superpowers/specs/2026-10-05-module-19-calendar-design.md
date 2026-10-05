# Module 19 — Calendar

**Status:** draft, pending lead review · **Branch:** `feat/calendar` (off `develop` @ `2ad0698`)
**Author:** Victoria · **Date:** 2026-10-05

## Context

**Authority:** `Cofoundaz_Technical_PRD.md` → Module 19. The handoff brief
`docs/handoff/module-19-calendar.md` orients the work; it is not the spec.

A calendar / planning workspace — the "where does my time go, and what's due" layer. Founders
create and view **events** across Month / Week / Agenda views, filter them by type, and read their
**roadmap milestones** on a Milestones tab. External calendar sync, cross-module aggregation and
reminders are out of v1.

All six senior checkpoints were settled with the lead before this spec, plus the "+ Milestone"
question raised by the FE cross-check (§Decisions, D1-D13). Nothing is open.

### FE cross-check (per the standing rule)

Read on 2026-10-05 from `../cofoundaz` (mock prototype — `useState` + hard-coded `*_DATA` arrays,
no network calls, so shapes and field names are the signal, not endpoints):

- `hooks/useCalendarApi.ts`, `app/(dashboard)/calendar/page.tsx`,
  `app/(dashboard)/calendar/milestones/page.tsx`, `app/(dashboard)/calendar/sync/page.tsx`.
- **Month / Week / Agenda are tabs on one page.** Milestones and Sync are separate routes, not
  tabs of it. The hook's `CalendarSubTab` union lists all five; the page only uses three.
- **Event types are exactly `milestone | meeting | deadline | task`**, lowercase, on
  `CalendarEventItem.type`. The filter chips are `All | Milestone | Meeting | Deadline | Task` and
  map onto the `type` query param (`All` = omit it).
- **The create-event modal collects three fields only:** title (required), type (four buttons),
  date (required). No time input, no description, no all-day toggle. So `start_time`, `end_time`,
  `all_day` and `description` must be optional on create from day one.
- **Time appears in three different shapes:** Month has none, Week has `'09:00 AM'`, Agenda has
  `'09:00'` and the literal string `'All day'`. The API returns one shape (`HH:MM`, D6) and the FE
  formats; `all_day: true` is what produces the "All day" label.
- **`MilestoneItemData`** = `title`, `dueDate`, `status`, `progress`, `tasksInfo` + presentation-only
  fields (`badgeClass`, `progressBarClass`, `iconType`, `cardBorderClass`). `dueDate` is a
  pre-formatted sentence (`'Due Friday, Jul 31'`, `'Completed Jun 20'`), not a date — the API
  returns ISO and the FE builds the sentence.
- **`AgendaItemData.type` is capitalized** (`'Meeting'`) while `CalendarEventItem.type` is
  lowercase. The mock contradicts itself; the API returns lowercase enum values throughout.
- **Three fields have no backend source:** `MilestoneItemData.tasksInfo`
  (`"11 of 13 tasks complete"`), `AgendaItemData.subtitle` (`"With Daniel"`, `"Roadmap milestone"`,
  `"From your mission"` — provenance from the deferred aggregation), and the Milestones header
  `"3 of 7 complete this quarter"`. All three are omitted and labelled not-backed in the FE guide.
- **No recurrence anywhere** in the UI or the mock data. v1 is single events only (D10).
- The Sync page is pure local `useState` — three connect buttons and three preference toggles.
  Nothing for it to call in v1 (D3).
- The repo carries an `openapi.json` — a staging snapshot, 87 paths, **no calendar paths**. Nothing
  to conform to; it will need regenerating once this ships.

## Goal

One new table, five event routes, one read-only milestones projection. The single list endpoint
(date range + optional type) powers Month, Week and Agenda; grouping and formatting are the FE's.

## Global constraints (carried verbatim where they bind)

- `success_response` envelope on every response; `AppError(code, message, http_status,
  field_errors=)` for failures, via a local `_validation` helper copied from
  `app/services/finance/errors.py`.
- `require_role(MembershipRole.founder, MembershipRole.team_member)` + `get_verified_user`.
  `startup_id` comes from the returned `Membership`, **never from the request body or query**.
- `get_db()` never commits. Write endpoints call `db.commit()`; services only `flush()`.
- Cross-tenant reads return a uniform `404`, never `403` — a workspace must not be able to detect
  another workspace's row ids.
- `Enum(CalendarEventType, native_enum=False, length=20, values_callable=…)` so the database stores
  enum **values**; `index=True` on every FK.
- Migration number taken against the live `develop` head at build time. Head today is
  `0045_finance_model`, so `0046_calendar` — re-checked immediately before push, renumbered if
  anything merges first. One alembic head, `alembic check` clean.
- No new dependencies.

## Design

### 1. `calendar_events` table (migration `0046_calendar`)

`UUIDMixin` + `TimestampMixin`, plus:

| Column | Type | Notes |
|---|---|---|
| `startup_id` | `UUID` FK `startups.id` `ondelete=CASCADE` | not null |
| `created_by` | `UUID` FK `users.id` `ondelete=SET NULL` | **nullable**, `index=True` — matches `expenses`, `budgets`, `marketing`; a deleted user must not delete the workspace's calendar |
| `title` | `String(200)` | not null |
| `type` | `Enum(CalendarEventType, native_enum=False, length=20)` | not null |
| `event_date` | `Date` | not null |
| `start_time` | `Time` | nullable, naive (D6) |
| `end_time` | `Time` | nullable, naive (D6) |
| `all_day` | `Boolean` | not null, `default=False`, `server_default=false` |
| `description` | `Text` | nullable |

Indexes: `ix_calendar_events_startup_id` (house rule: `index=True` on every FK) and a composite
`ix_calendar_events_startup_id_event_date` for the range query (D7).

New enum in `app/db/models/enums.py`:

```python
class CalendarEventType(enum.StrEnum):
    milestone = "milestone"
    meeting = "meeting"
    deadline = "deadline"
    task = "task"
```

### 2. Events CRUD — `/api/v1/calendar`

| Route | Does |
|---|---|
| `POST /calendar/events` | Create. Returns the event. |
| `GET /calendar/events?date_from=&date_to=&type=` | List in range, optional type filter. |
| `GET /calendar/events/{id}` | One event; `404` if not this workspace's. |
| `PATCH /calendar/events/{id}` | Partial edit. |
| `DELETE /calendar/events/{id}` | `{"deleted": true}`. |

All six routes behind `_calendar = require_role(founder, team_member)` (D4). Writes commit.

**Field rules** (`app/schemas/calendar.py`, mirroring `TransactionCreate`/`TransactionUpdate`):

- `title` — required on create, `min_length=1`, `max_length=200`, stripped.
- `type` — required on create, one of the four enum values.
- `event_date` — required on create.
- `start_time` / `end_time` / `description` / `all_day` — optional on create. `description`
  `max_length=2000` (D8).
- `end_time` without `start_time` → **422** (an end with no beginning).
- `end_time` earlier than `start_time` → **422**. Equal is allowed (a zero-length marker).
- `all_day: true` **with** either time → **422** (D9), rather than silently discarding what the
  caller sent.
- **PATCH explicit-null rule** (the Finance `model_validator(mode="after")` pattern): `title`,
  `type`, `event_date` and `all_day` are `NOT NULL` on the row, so an explicit `null` for any of
  them is rejected as 422 rather than reaching the database as a 500. `start_time`, `end_time` and
  `description` are nullable, so explicit `null` **clears** them — that is how a caller un-sets a
  time. Omitting a field is always a no-op.
- PATCH re-runs the cross-field rules above against the **merged** result, not the payload alone,
  so an edit cannot leave a row in a state a create would have refused.

**List semantics:**

- `date_from` and `date_to` are independent and optional, exactly as Finance's transaction list —
  `>=` and `<=` on `event_date`. `type` omitted means all types (the `All` chip).
- Order: `event_date ASC`, then `start_time ASC NULLS FIRST`, then `created_at ASC`. Untimed and
  all-day events sort to the top of their day, which is how the Agenda renders them.
- No pagination and no span cap in v1 (D7), matching the shipped Finance list. Noted as a
  follow-up, not a v1 gap.

### 3. Milestones projection — `GET /calendar/milestones` (read-only)

No new table. `RoadmapMilestone` has **no `startup_id`**; it scopes through
`phase_id → RoadmapPhase.roadmap_id → Roadmap.startup_id`, so the read is a three-table join,
filtered on the membership's `startup_id`:

```
RoadmapMilestone → RoadmapPhase (phase_id) → Roadmap (roadmap_id)   WHERE Roadmap.startup_id = …
```

This mirrors `_due_overdue_milestones` in `app/worker/scheduler.py:85`. Two deliberate differences
from that function:

- It also filters `Startup.deleted_at.is_(None)` because the scheduler scans **every** workspace.
  A request-scoped read already has an active `Membership` for this workspace, so that join is not
  carried over.
- It takes "today" from `_local(now)` — `now.astimezone(ZoneInfo(settings.SCHEDULER_TIMEZONE))`.
  This read takes it from `datetime.now(UTC).date()` instead, per D6. The two agree today
  (`SCHEDULER_TIMEZONE` defaults to `"UTC"`) but would diverge if that setting changed, and the
  calendar's clock should not depend on a scheduler config value.

**Per milestone, returned:** `id`, `title`, `due_on` (ISO date or `null`), `status` (the raw
`RoadmapStatus` value), `calendar_status` (derived, below), `progress`.

`calendar_status` derivation — the four labels the FE badges, as values:

| Condition | Value | FE label |
|---|---|---|
| `status == done` | `complete` | Complete |
| `status != done` and `due_on` is set and `due_on < today` | `at_risk` | At risk |
| `status == in_progress` | `on_track` | On track |
| otherwise (`status == todo`) | `not_started` | Not started |

Checked in that order: overdue beats in-progress, and a completed milestone is never "at risk"
however old its due date. A milestone with no `due_on` can never be `at_risk`.

Both the raw `status` and the derived `calendar_status` are returned — the raw value costs nothing
and keeps the FE from having to reverse the derivation if the labels change.

Order: `due_on ASC NULLS LAST`, then `created_at ASC`. Completed milestones are included (the mock
shows one, "Completed Jun 20").

Not returned: `tasksInfo` — no backend source (§FE cross-check), omitted rather than faked, and
labelled not-backed in the FE guide.

**This route is read-only, and the screen above it is not.** The Milestones tab lists this
projection but its "+ Milestone" button calls `POST /calendar/events` (D11). The two are different
stores: a milestone created from that button lands on the Month / Week / Agenda grids as a
milestone-typed, all-day event and **never appears in this list**. The FE guide says so in those
words, because "Save milestone" on a screen headed "Milestones" invites exactly the wrong
assumption. Unioning the two sources is deferred to v2.

### 4. Dates, times and timezone (D6)

There is **no timezone field anywhere in the codebase** — `Startup` has `country` only. Honest
per-workspace conversion is therefore impossible in v1, so none is attempted:

- `event_date` is a `Date`; `start_time` / `end_time` are naive `Time`. Both are **local
  wall-clock**, stored exactly as sent and returned exactly as stored.
- Returned as `YYYY-MM-DD` and `HH:MM`. No offsets, no `Z`, no conversion anywhere in the stack.
- Range filters compare on `event_date`. Anything needing "today" uses `datetime.now(UTC).date()`,
  consistent with Finance's `cashflow.py` / `runway.py`.
- Rationale, stated in the FE guide: a stored-and-returned wall clock cannot render 2pm as 1pm,
  and it is reversible — when a workspace timezone lands, every stored value is still the correct
  local time and only interpretation changes.

## Data flow / tenancy / transactions

1. Request → `require_role(founder, team_member)` → `Membership` (or `403`).
2. Service functions take `startup_id` as a keyword argument and filter on it in every query.
   Nothing reads a workspace id off the request.
3. A read for an id outside the workspace raises `NotFound()` → uniform `404`.
4. Services `flush()` only. The endpoint calls `db.commit()` on create, patch and delete; the two
   reads never commit.
5. No events published in v1 (D5) — `event_bus.publish(db, …)` has no consumer for the calendar
   until the reminder scheduler exists.

## Components / files

**New**

- `app/db/models/calendar.py` — `CalendarEvent`
- `app/schemas/calendar.py` — `EventCreate`, `EventUpdate`, `EventResponse`, `MilestoneResponse`
- `app/services/calendar/__init__.py`
- `app/services/calendar/errors.py` — `_validation`, copied from `app/services/finance/errors.py`
- `app/services/calendar/service.py` — events CRUD + list
- `app/services/calendar/milestones.py` — the projection and its derivation
- `app/api/v1/endpoints/calendar.py` — the six routes
- `alembic/versions/0046_calendar.py`
- `tests/db/test_calendar_models.py`, `tests/test_calendar_migration.py`
- `tests/services/calendar/test_events.py`, `tests/services/calendar/test_milestones.py`
- `tests/api/test_calendar.py`
- `e2e/test_calendar.py`
- `docs/fe-integration-guide-calendar.md`, `docs/sop/<date>-calendar.md`

**Touched**

- `app/db/models/enums.py` — `+ CalendarEventType`
- `app/db/models/__init__.py` — export `CalendarEvent`
- `app/api/v1/api.py` — `include_router(calendar.router, prefix="/calendar", tags=["calendar"])`
- `e2e/test_smoke.py` — the six route shapes
- `docs/checklist/PROJECT_CHECKLIST.md`

## Review focus (inputs the spec implies but happy-path tests may miss)

- Range boundaries: an event exactly on `date_from` and exactly on `date_to` must be included.
- `date_to` earlier than `date_from` → empty list, not an error (matches Finance).
- Ordering within one day: all-day and untimed events before timed ones.
- `all_day: true` with a time → 422, both on create and on PATCH.
- An event created with no times and no `all_day` stays `all_day = false` (untimed, not all-day) —
  the API must not infer one from the other (D12).
- PATCH clearing a time with explicit `null`, versus omitting it (no-op).
- PATCH that would make `end_time < start_time` using the **merged** row, e.g. patching only
  `start_time` to a value later than the existing `end_time`.
- Cross-tenant: `GET`/`PATCH`/`DELETE` another workspace's event id → `404`.
- RBAC: mentor / accountant / investor → `403` on every route.
- Milestones projection: a milestone belonging to another workspace must never appear; the
  three-table join is the only thing preventing it.
- `calendar_status` for each of: done-and-overdue (→ `complete`, not `at_risk`), todo-and-overdue,
  in_progress-and-overdue, `due_on is None`.
- A workspace with no roadmap at all → empty list, not a 500.

## Decisions (all settled with the lead)

| # | Decision |
|---|---|
| D1 | **Milestones are a read-only projection of `RoadmapMilestone`, in v1.** No new table, no writes; edits stay in Roadmap. The three-table join is the tenancy boundary. |
| D2 | **No cross-module aggregation.** v1 serves the calendar's own events; the milestones read is the only reach outside the module. Agenda's "From your mission" / "Sales pipeline" rows are out. |
| D3 | **External sync deferred.** No Google/Outlook/Apple OAuth, and `calendar_connections` is not modelled speculatively. The FE Sync tab has nothing to call in v1 and is labelled as such in the FE guide. |
| D4 | **`require_role(founder, team_member)` for reads and writes alike.** Accountant is out of v1 — a calendar is not finance-scoped, and widening later is cheap where narrowing after the FE ships is not. |
| D5 | **No domain events in v1.** Nothing consumes `calendar.event.created` until the reminder scheduler exists; added then, not now. |
| D6 | **Naive local wall-clock.** `Date` + optional naive `Time` + `all_day`; stored as sent, returned as `YYYY-MM-DD` + `HH:MM`, no conversion; "today" from `datetime.now(UTC).date()`. No timezone field exists to do better, and this is reversible when one lands. |
| D7 | **Composite `(startup_id, event_date)` index, kept alongside the plain `startup_id` FK index.** The composite serves the range query; the single index is kept because "index=True on every FK" is a house rule and the redundancy costs one small index. No pagination or span cap in v1, matching the shipped Finance list. |
| D8 | **`description` capped at 2 000 characters.** Free-text notes, members-only, so the cap is about accidents rather than abuse. |
| D9 | **`all_day: true` with a time is a 422, not a silent discard.** Dropping data the caller sent is worse than refusing it. |
| D10 | **Single events only.** No recurrence, no RRULE, no expansion — there is no recurrence UI in the prototype. |
| D11 | **The Milestones "+ Milestone" button creates a calendar event, not a roadmap milestone.** `POST /calendar/events` with `type: "milestone"` and `all_day: true` (the modal collects no time). It never touches Roadmap, so D1's read-only ruling stands. Confirmed with the lead: the submit handler is named `handleCreateEvent` and the heading reads "Create calendar milestone". |
| D12 | **`all_day` is declared by the caller, never inferred from missing times.** An event with no `start_time` is *untimed*, which is not the same as *all-day* — "VAT filing due" has no time but does not block out a day. The FE sends `all_day: true` explicitly from the "+ Milestone" modal; the column default stays `false`. |
| D13 | **The Milestones tab reads one store and writes to another, by design.** The list is the Roadmap projection; the button creates a calendar event. A milestone created there appears on Month / Week / Agenda as a milestone-typed event and **does not join the list above it**. Stated explicitly in the FE guide so the two senses of "milestone" are not conflated. Unioning the two sources into one list is a v2 enhancement. |

## Out of scope / deferred (each with its reason)

| Deferred | Reason |
|---|---|
| External calendar sync (Google / Outlook / Apple) | OAuth + third-party APIs with no infrastructure in the repo. Same line drawn on Finance's integrations slice. |
| Cross-module aggregation into the Agenda | A large integration across missions, roadmap, finance and sales. v1 is the calendar's own store (D2). |
| Reminder scheduler ("3 days / 1 day before") | A scheduled job. Scheduler infrastructure exists (`scheduled_run.py`, the roadmap-overdue tick) but v1 defers it, as Finance deferred invoice reminders. |
| Sync preferences (block focus time, remind before deadlines, show weekends) | Three local toggles on a page with nothing else to call. Not core. |
| Recurring events | No recurrence UI anywhere in the prototype (D10). |
| `tasksInfo`, `AgendaItemData.subtitle`, "N of M complete this quarter" | No backend source. Omitted rather than faked, and labelled not-backed in the FE guide. |
| Pagination / a span cap on the events list | Consistent with the shipped Finance list; revisit if a workspace's calendar grows large. |
| Unioning roadmap milestones and milestone-typed calendar events into one Milestones list | v2 enhancement (D13). v1 keeps the two stores visibly separate. |
| Regenerating the FE repo's `openapi.json` | Not this module's work. Raised with the lead, who is tracking it as a follow-up. |
