# FE Integration Guide — Calendar (Module 19)

> **Provenance.** Every response body in this guide is pasted verbatim from a live capture
> (`scripts/e2e_run.sh` → `e2e/test_calendar.py` → `e2e/_captures/calendar/`, run 2026-10-12, 66/66
> e2e passing). IDs, dates and timestamps in the bodies are **placeholders from that test run**.
> Behaviour that the live run did **not** exercise is labelled inline and listed in the
> verification table at the end (§5); those rules are covered by unit tests only.

Base path: `/api/v1/calendar`. Every route requires a Bearer access token and an
`X-Workspace-Id` header naming the active workspace (`GET /auth/me` →
`data.active_workspace_id`), the same as every other tenant-scoped endpoint. There are **no
public routes** in this module.

**Access:** founders and team members only. Mentor, **accountant**, legal advisor, business
consultant and investor receive **403** on every route. Unauthenticated → **401**. Unverified
email → **403** `EMAIL_NOT_VERIFIED`.

> Note the accountant exclusion — it differs from Finance, where accountant *is* granted. A
> calendar is not finance-scoped (spec D4). Ask before assuming the sets match.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}`.

---

## 0. Five things the mock prototype will mislead you about

The prototype at `app/(dashboard)/calendar/` is a mock — `useState` and hard-coded arrays, no
network. It contradicts the real API in five places. Read this section before wiring anything.

**1. Times come back as `HH:MM`, 24-hour.** The mock shows three different shapes: Month has no
time at all, Week has `'09:00 AM'`, and Agenda has `'09:00'`. **None of those come off the wire
except the last.** The API always returns `"14:00"` — never `"02:00 PM"`, never `"14:00:00"`.
Format for display yourself.

**2. There is no `'All day'` time string.** The Agenda mock puts the literal text `'All day'` in
its `time` field. The API instead returns `all_day: true` with `start_time: null`. Render the
label from the boolean.

**3. `type` is always lowercase.** `CalendarEventItem.type` in the mock is lowercase
(`'meeting'`) but `AgendaItemData.type` is capitalized (`'Meeting'`). The mock contradicts
itself. The API returns lowercase values only: `milestone | meeting | deadline | task`. Map to
display capitals in the component.

**4. `due_on` is an ISO date, not a sentence.** `MilestoneItemData.dueDate` in the mock is
pre-formatted prose — `'Due Friday, Jul 31'`, and `'Completed Jun 20'` for finished ones. The API
returns `"2026-07-31"`. Build the sentence on the front end, including swapping the prefix to
"Completed" when `calendar_status === "complete"`.

**5. The Milestones tab reads one store and writes to another.** The list is a read-only
projection of **Roadmap** milestones. The "+ Milestone" button creates a **calendar event** with
`type: "milestone"`. So an item created with that button **will not appear in the list above
it** — it appears on Month / Week / Agenda. This is deliberate (spec D11/D13); unioning the two
sources is a v2 enhancement.

### Fields with no backend source

These exist in the mock and are **not returned by any endpoint**. Do not wait on them.

| Mock field | Where | Status |
|---|---|---|
| `MilestoneItemData.tasksInfo` (`"11 of 13 tasks complete"`) | Milestones cards | Not backed. Nothing in the schema counts a milestone's tasks for this view. |
| `AgendaItemData.subtitle` (`"With Daniel"`, `"Roadmap milestone"`, `"From your mission"`) | Agenda rows | Not backed. It is provenance from the cross-module aggregation deferred out of v1. `description` is the nearest real field, but it is free text the user typed, not a source label. |
| `"3 of 7 complete this quarter"` | Milestones header | Not backed. Derivable from the projection later if wanted; left out of v1. |
| All `*Class` / `borderColor` / `iconType` fields | everywhere | Presentation only, never backend. |

### Not in v1

The **Sync tab** (`calendar/sync/page.tsx`) has nothing to call — no Google/Outlook/Apple
endpoints exist, and its three preference toggles are not persisted. Treat that screen as
not-yet-wired. Recurring events and multi-day events are also out of v1 (a single `event_date`,
no `end_date`).

---

## 1. Events

### `GET /api/v1/calendar/events?date_from=&date_to=&type=` → **200**

**This one endpoint powers Month, Week and Agenda.** They differ only in the range they ask for:
Month sends the visible month, Week sends a week, Agenda sends the next N days. Grouping into
"Today / Tomorrow / Wed Jul 29" is the front end's job — the API returns a flat, ordered list.

| Param | Meaning |
|---|---|
| `date_from` | inclusive lower bound on `event_date` (`>=`). Optional. |
| `date_to` | inclusive upper bound on `event_date` (`<=`). Optional. |
| `type` | one of `milestone \| meeting \| deadline \| task`. Omit for all — that is the `All` chip. |

Both boundaries are **inclusive**: an event dated exactly `date_from` or exactly `date_to` is
returned. A backwards range (`date_to` before `date_from`) returns an empty list, not an error.
There is no pagination in v1.

**Ordering:** `event_date` ascending, then `start_time` ascending with **untimed and all-day
events first** within a day, then creation order. So a day renders as: all-day items, then
untimed items, then timed items in clock order — which is what the Agenda expects.

> ⚠️ *Not exercised live:* the live run never put two events on the same day, so the **within-day**
> ordering (all-day → untimed → timed) is covered by unit tests only. Cross-day ordering is live.

Captured: `e2e/_captures/calendar/events_month.json`

```json
{
  "data": {
    "events": [
      {
        "id": "168cd4b3-6b31-4aa7-8ab0-9173412d8c33",
        "title": "Interview 3 gig workers",
        "type": "task",
        "event_date": "2026-10-01",
        "start_time": null,
        "end_time": null,
        "all_day": false,
        "description": null,
        "created_by": "496bf9e0-877c-4b01-9a03-1a7cab410afb",
        "created_at": "2026-10-12T10:41:51.852391Z",
        "updated_at": "2026-10-12T10:41:51.852391Z"
      },
      {
        "id": "932c1784-512a-47e0-94a3-e1078572fadb",
        "title": "Partner call, Sahel Fund",
        "type": "meeting",
        "event_date": "2026-10-15",
        "start_time": "11:00",
        "end_time": "11:45",
        "all_day": false,
        "description": "Bring the latest traction deck.",
        "created_by": "496bf9e0-877c-4b01-9a03-1a7cab410afb",
        "created_at": "2026-10-12T10:41:51.831723Z",
        "updated_at": "2026-10-12T10:41:51.831723Z"
      },
      {
        "id": "65e7bd31-f035-4d3a-9ced-cd471f0993a7",
        "title": "Grant deadline, GIZ Digital Inclusion Fund",
        "type": "deadline",
        "event_date": "2026-10-20",
        "start_time": null,
        "end_time": null,
        "all_day": true,
        "description": null,
        "created_by": "496bf9e0-877c-4b01-9a03-1a7cab410afb",
        "created_at": "2026-10-12T10:41:51.843692Z",
        "updated_at": "2026-10-12T10:41:51.843692Z"
      },
      {
        "id": "3ebc7cec-be36-4dc7-8188-353cb4df4ee9",
        "title": "Close validation milestone",
        "type": "milestone",
        "event_date": "2026-10-28",
        "start_time": null,
        "end_time": null,
        "all_day": true,
        "description": null,
        "created_by": "496bf9e0-877c-4b01-9a03-1a7cab410afb",
        "created_at": "2026-10-12T10:41:51.860598Z",
        "updated_at": "2026-10-12T10:41:51.860598Z"
      }
    ]
  },
  "meta": null
}
```

That capture is a Month range (`date_from=2026-10-01&date_to=2026-10-31`) over four events: untimed,
timed, all-day and milestone-typed. `events_week.json` and `events_filtered_by_type.json` have the same
row shape.

Empty calendar (captured, `events_other_workspace_empty.json`):

```json
{
  "data": {
    "events": []
  },
  "meta": null
}
```

### `POST /api/v1/calendar/events` → **200**

**Only three fields are required**, which is exactly what the create modal collects:

```json
{ "title": "Partner call, Sahel Fund", "type": "meeting", "event_date": "2026-07-15" }
```

| Field | Required | Notes |
|---|---|---|
| `title` | yes | 1–200 characters. |
| `type` | yes | `milestone \| meeting \| deadline \| task`. |
| `event_date` | yes | `YYYY-MM-DD`. |
| `start_time` | no | `HH:MM`. Omit or `null` for an untimed event. |
| `end_time` | no | `HH:MM`. Requires `start_time`. |
| `all_day` | no | defaults to `false`. See below. |
| `description` | no | free text, up to 2 000 characters. |

**`all_day` is never inferred.** An event with no times is *untimed*, which is not the same as
all-day — a deadline with no time should not block out the whole day. If you want an all-day
event, send `all_day: true` explicitly. The "+ Milestone" button on the Milestones tab should
send `{"title": …, "type": "milestone", "event_date": …, "all_day": true}`, since its modal
collects no time.

The response is the created event, with the same shape as a list row:

Captured: `e2e/_captures/calendar/event_created_timed.json`

```json
{
  "data": {
    "id": "932c1784-512a-47e0-94a3-e1078572fadb",
    "title": "Partner call, Sahel Fund",
    "type": "meeting",
    "event_date": "2026-10-15",
    "start_time": "11:00",
    "end_time": "11:45",
    "all_day": false,
    "description": "Bring the latest traction deck.",
    "created_by": "496bf9e0-877c-4b01-9a03-1a7cab410afb",
    "created_at": "2026-10-12T10:41:51.831723Z",
    "updated_at": "2026-10-12T10:41:51.831723Z"
  },
  "meta": null
}
```

An all-day create (`event_created_all_day.json`) returns `all_day: true` with both times `null`. An untimed
create (`event_created_untimed.json`) returns `all_day: false` with both times `null`. The "+ Milestone"
body above is captured as `event_created_milestone_type.json`.

### `GET /api/v1/calendar/events/{event_id}` → **200**

The single event, with the same body as the create response (`event_detail.json`). **404** for an id that
belongs to another workspace, never 403, so you can't use this to discover whether someone else's
event exists. Captured:

Captured: `e2e/_captures/calendar/event_cross_tenant_404.json`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### `PATCH /api/v1/calendar/events/{event_id}` → **200**

Send only what changed. Three rules worth knowing:

- **Omitting a field leaves it alone.** `{"title": "Renamed"}` changes the title and nothing else.
- **An explicit `null` clears a nullable field.** `{"start_time": null, "end_time": null}` is how
  you turn a timed event into an untimed one. Same for `description`.
- **An explicit `null` on a required field is a 422.** `title`, `type`, `event_date` and `all_day`
  cannot be nulled.

Moving a meeting by an hour (`{"start_time": "12:00", "end_time": "12:45"}`):

Captured: `e2e/_captures/calendar/event_patched.json`

```json
{
  "data": {
    "id": "932c1784-512a-47e0-94a3-e1078572fadb",
    "title": "Partner call, Sahel Fund",
    "type": "meeting",
    "event_date": "2026-10-15",
    "start_time": "12:00",
    "end_time": "12:45",
    "all_day": false,
    "description": "Bring the latest traction deck.",
    "created_by": "496bf9e0-877c-4b01-9a03-1a7cab410afb",
    "created_at": "2026-10-12T10:41:51.831723Z",
    "updated_at": "2026-10-12T10:41:51.953694Z"
  },
  "meta": null
}
```

Clearing both times (`{"start_time": null, "end_time": null}`) gives `event_time_cleared.json`: the
same body with `"start_time": null, "end_time": null` and a new `updated_at`. `all_day` stays `false`,
so the event becomes **untimed**, not all-day.

The time rules are checked against the **merged result**, not just your payload. Sending
`{"start_time": "11:00"}` against an event whose stored `end_time` is `"10:00"` is a **422**, even
though the payload looks fine on its own.

### `DELETE /api/v1/calendar/events/{event_id}` → **200**

Captured: `e2e/_captures/calendar/event_deleted.json`

```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

**404** for another workspace's id. There is no soft delete and no undo.

### Validation errors

All of these are **422** with `code: "VALIDATION_ERROR"`. ⚠️ **The two live captures below
break the same rule but have different shapes**, depending on whether the rule failed on the request
body alone (POST) or on the merged result (PATCH).

**POST, rule broken in the payload itself** (`all_day: true` with a `start_time`):

Captured: `e2e/_captures/calendar/event_all_day_with_time_422.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, an all-day event may not carry a start or end time"
      }
    ]
  }
}
```

**PATCH, rule broken only after merging with the stored row**:

Captured: `e2e/_captures/calendar/event_patch_merged_conflict_422.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "an all-day event may not carry a start or end time",
    "field_errors": [
      {
        "field": "start_time",
        "message": "an all-day event may not carry a start or end time"
      }
    ]
  }
}
```

> 🪤 **Field-nesting trap. Handle both:**
> - On **POST** (and on any PATCH whose payload is self-contradictory), the cross-field time rules come
>   back with **`field: ""`**, a **`"Value error, "` prefix** on the message, and the **generic**
>   top-level `message`. There's no field to highlight, so show the rule at form level, not on an
>   input. Strip the `"Value error, "` prefix if you show the text.
> - On **PATCH merged-result** conflicts you get a real `field` (`"start_time"`), no prefix, and the
>   top-level `message` **is** the rule text.
> - Single-field errors (`title`, `type`, `description`) come from standard request validation and
>   should name their field. *Not exercised live*: no capture exists for them.

| What you sent | Why it is refused |
|---|---|
| `all_day: true` with `start_time` or `end_time` | An all-day event has no clock time. Refused rather than silently dropping the time you sent. |
| `end_time` without `start_time` | An end with no beginning. |
| `end_time` earlier than `start_time` | Equal is allowed (a zero-length marker); earlier is not. |
| `title: ""` | 1 character minimum. |
| `type: "birthday"` | Not one of the four. |
| `description` over 2 000 characters | Cap. |
| `null` for `title`, `type`, `event_date` or `all_day` on PATCH | Those columns cannot be null. |

Only the **all-day-with-a-time** row (POST) and the merged-result conflict (PATCH) were exercised live.
The other rows are covered by unit tests only, and their exact `field` / `message` strings weren't
captured.

---

## 2. Milestones (read-only)

### `GET /api/v1/calendar/milestones` → **200**

A **read-only projection of Roadmap milestones**. This module owns no milestone table — editing a
milestone happens in Roadmap, not here. There is no POST, PATCH or DELETE on this path.

Ordered by `due_on` ascending with **undated milestones last**. Completed milestones are
included.

Captured: `e2e/_captures/calendar/milestones.json`

```json
{
  "data": {
    "milestones": [
      {
        "id": "d74baf60-adf5-43e3-ba3b-4486e3cb25a6",
        "title": "Validate demand",
        "due_on": "2026-10-26",
        "status": "todo",
        "calendar_status": "not_started",
        "progress": 0
      },
      {
        "id": "18be6486-7eb1-48bc-b8d9-b31c067ef47d",
        "title": "Pricing test",
        "due_on": "2026-11-16",
        "status": "todo",
        "calendar_status": "not_started",
        "progress": 0
      },
      {
        "id": "37cdfef1-d0fb-4943-b6b5-f16b9bc890ac",
        "title": "Core flow",
        "due_on": "2026-12-21",
        "status": "todo",
        "calendar_status": "not_started",
        "progress": 0
      }
    ]
  },
  "meta": null
}
```

> ⚠️ *Not exercised live:* the e2e roadmap only had untouched, future-dated milestones, so this
> capture shows **`not_started` only**. The `at_risk`, `on_track` and `complete` derivations below,
> `due_on: null` sorting last, and a non-zero `progress` are covered by unit tests only.

### The two status fields

`status` is Roadmap's own value: `todo | in_progress | done`. It has **no notion of risk**.

`calendar_status` is derived for the badge, and maps to the four labels the UI shows:

| `calendar_status` | Badge | When |
|---|---|---|
| `complete` | Complete | `status` is `done` — whatever the due date. A finished milestone is never at risk. |
| `at_risk` | At risk | not done, and `due_on` is in the past. |
| `on_track` | On track | not done, not overdue, `status` is `in_progress`. |
| `not_started` | Not started | not done, not overdue, `status` is `todo`. |

Checked in that order: overdue outranks in-progress, and a milestone with `due_on: null` can
never be `at_risk`. "Today" is the current UTC date, so a milestone due today is not yet overdue.

Both fields are returned so you can badge from `calendar_status` without having to reverse the
derivation, and still show or filter on the raw Roadmap status if a screen needs it.

A workspace with no roadmap returns `{"data": {"milestones": []}, "meta": null}` (*not captured live*;
unit-tested).

---

## 3. Dates and times — how they are stored

There is **no timezone anywhere in this app**, for any workspace. So the calendar does not
convert: a time is stored exactly as you send it and returned exactly as stored.

- `event_date` is a plain calendar date. `start_time` / `end_time` are plain wall-clock times.
- No offsets, no `Z`, no UTC conversion on either field.
- If a user in Lagos enters 2pm, every client sees `"14:00"`. If a user in London enters 2pm,
  every client also sees `"14:00"`. They are *different moments*; the API does not pretend
  otherwise, because it has no way to know.

This is deliberate (spec D6). The alternative — converting between timezones without knowing
anyone's timezone — is how a 2pm meeting renders as 1pm. It is also reversible: when a workspace
timezone eventually lands, every stored value is still the correct local time and only its
interpretation changes.

`created_at` and `updated_at` are different: those are real instants and come back as UTC
timestamps with a `Z`.

---

## 4. Capture map

Every body in this guide comes from one of these files in `e2e/_captures/calendar/` (captured
2026-10-12). Re-run `scripts/e2e_run.sh` to regenerate them.

| Section | Capture |
|---|---|
| `POST /events`, timed | `event_created_timed.json` |
| `POST /events`, all-day | `event_created_all_day.json` |
| `POST /events`, untimed | `event_created_untimed.json` |
| `POST /events`, milestone-typed | `event_created_milestone_type.json` |
| `GET /events`, Month range | `events_month.json` |
| `GET /events`, Week range | `events_week.json` |
| `GET /events?type=` | `events_filtered_by_type.json` |
| `GET /events/{id}` | `event_detail.json` |
| `PATCH /events/{id}` | `event_patched.json` |
| `PATCH /events/{id}`, clearing a time | `event_time_cleared.json` |
| `POST /events` 422, all-day with a time | `event_all_day_with_time_422.json` |
| `PATCH /events/{id}` 422, merged conflict | `event_patch_merged_conflict_422.json` |
| `DELETE /events/{id}` | `event_deleted.json` |
| `GET /events` after a delete | `events_after_delete.json` |
| `GET /milestones` | `milestones.json` |
| `GET /events/{id}` cross-tenant 404 | `event_cross_tenant_404.json` |
| `GET /events` from another workspace | `events_other_workspace_empty.json` |

---

## 5. Verification

| Behaviour | Status |
|---|---|
| Create timed / all-day / untimed / milestone-typed event: body and 200 | ✅ verified live |
| List by Month range, Week range and `type=` filter; cross-day ordering | ✅ verified live |
| Within-day ordering (all-day → untimed → timed) | ⚠️ unit tests only |
| Get one event; cross-workspace **404** shape | ✅ verified live |
| PATCH partial update; explicit `null` clears times | ✅ verified live |
| 422 all-day-with-time on POST (`field: ""`, `"Value error, "` prefix) | ✅ verified live |
| 422 merged-result conflict on PATCH (`field: "start_time"`) | ✅ verified live |
| Other validation rows (`end_time` without start, end < start, empty title, bad type, description cap, null required field) | ⚠️ unit tests only, strings not captured |
| DELETE `{"deleted": true}`; event gone from the following list | ✅ verified live |
| Other workspace sees an empty list | ✅ verified live |
| Milestones projection: shape, `not_started`, `due_on` ascending | ✅ verified live |
| `at_risk` / `on_track` / `complete`, undated-last, empty roadmap | ⚠️ unit tests only |
| RBAC: 403 for mentor/accountant/legal/consultant/investor | ⚠️ API tests only (`tests/api/test_calendar.py`), not in the calendar e2e |
| Unauthenticated → 401 | ⚠️ the API test accepts **401 or 403**, so the exact code isn't pinned; handle both |
| `EMAIL_NOT_VERIFIED` 403 | ⚠️ shared auth dependency, not exercised for calendar routes |
| Routes registered in OpenAPI | ✅ verified live (`e2e/test_smoke.py`) |

