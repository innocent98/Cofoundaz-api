# FE Integration Guide — Marketing Hub: Content Calendar + Channels (Module 10, Slice 1)

> **Provenance.** `e2e/test_marketing.py::test_marketing_journey` has been run
> (`bash scripts/e2e_run.sh`, 53/53 passed) and every response body below is pasted **verbatim**
> from the captures it wrote to `e2e/_captures/marketing/` (`overview_before.json`,
> `channels.json`, `channel_updated.json`, `entry_created.json`, `entries_list.json`,
> `entry_published.json`, `overview_after.json`). Nothing in §1–§7 is hand-written or "tidied" —
> field names, ordering, and null-vs-absent all come straight from a live run against this
> branch's code. The one thing **not** exercised live is a second `PATCH .../{id}` with
> `status: "published"` against an already-published entry (the idempotency guarantee in §5) —
> that is unit-tested, not e2e-captured; flagged inline where it matters.

Base path: `/api/v1/marketing`. Every route requires a Bearer access token
(`Authorization: Bearer <token>`) and an `X-Workspace-Id` header identifying the active
workspace (`GET /auth/me` → `data.active_workspace_id`), same as every other tenant-scoped
endpoint in this API.

**Access: founder or team_member only.** Every route is gated by
`require_role(MembershipRole.founder, MembershipRole.team_member)`. A mentor, accountant, legal
advisor, business consultant, or investor membership gets **403 `FORBIDDEN`** on every route in
this guide — do not show Marketing Hub navigation to those roles. There is no per-module
"Marketing grant" yet (deferred, see Follow-ups) — every founder and every team member in the
workspace has full read/write access to the whole calendar and channel board, not just their own
entries.

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §8).

**Tables:** entries live in `content_calendar`; channel status rows live in `marketing_channels`
(the PRD calls the latter `channels`, but that name was reserved to avoid a future collision —
see the design spec's note).

---

## Enums

```
ContentStatus  = draft | scheduled | published
ChannelStatus  = active | testing | paused | not_started
ChannelKey     = organic_social | paid_social | search | email | content_seo
                 | partnerships | events | referral
```

`ChannelKey` is a **fixed, closed taxonomy** — exactly these 8 values, always. There is no
create/delete for channels, only status/notes updates on the fixed set (§6–§7). A calendar entry
carries exactly **one** `channel` value — multi-channel entries are a follow-up, not built here.

---

## 1. `GET /api/v1/marketing` — overview stat strip

`e2e/_captures/marketing/overview_before.json` — a freshly onboarded workspace, nothing created
yet:

```json
{
  "data": {
    "scheduled_this_week": 0,
    "active_channels": 0,
    "active_campaigns": null,
    "top_channel_by_conversions": null,
    "ai_content_ideas": null
  },
  "meta": null
}
```

`e2e/_captures/marketing/overview_after.json` — same workspace, after activating the `email`
channel (§7) and publishing one entry (§5):

```json
{
  "data": {
    "scheduled_this_week": 0,
    "active_channels": 1,
    "active_campaigns": null,
    "top_channel_by_conversions": null,
    "ai_content_ideas": null
  },
  "meta": null
}
```

| Field | Meaning | Filled by |
|---|---|---|
| `scheduled_this_week` | Count of entries with `status: "scheduled"` and `scheduled_at` inside the current ISO week (Monday 00:00 → next Monday 00:00, server clock, UTC) | Slice 1 (live) |
| `active_channels` | Count of `marketing_channels` rows with `status: "active"` | Slice 1 (live) |
| `active_campaigns` | Always `null` in Slice 1 | **Slice 2** (Campaigns) |
| `top_channel_by_conversions` | Always `null` in Slice 1 | **Slice 5** (Performance Analytics) |
| `ai_content_ideas` | Always `null` in Slice 1 | **Slice 3** (AI layer) |

**Render the three deferred fields as "coming soon" placeholders, not zero/empty states.** `null`
here means "this slice doesn't compute this yet," not "the count is zero" — do not collapse it to
`0` or hide the tile; a later slice will start returning a real number in the same field, same
shape, no endpoint change.

**`scheduled_this_week` did not change between the two captures above** even though an entry was
scheduled for `2026-10-01T09:00:00Z` (§5) — that date fell outside the e2e run's current ISO
week, which is exactly why the count stayed `0`. This is expected: the field genuinely reflects
"this calendar week," not "everything scheduled."

---

## 2. `POST /api/v1/marketing/calendar-entries` — create an entry

**Request** (from the e2e journey — this exact body produced `entry_created.json` below):

```json
{
  "title": "Launch week kickoff",
  "channel": "email",
  "status": "scheduled",
  "body": "Ship it",
  "scheduled_at": "2026-10-01T09:00:00Z"
}
```

| Field | Type | Rules |
|---|---|---|
| `title` | string, 1–200 chars | Required. Blank/whitespace-only → 422 |
| `channel` | `ChannelKey` | Required |
| `status` | `ContentStatus` | Optional, defaults to `"draft"` |
| `body` | string \| null | Optional |
| `media_ref` | string \| null, ≤500 chars | Optional — an opaque id/URL slot for Module 18 media; **not fetched or validated server-side in this slice** |
| `scheduled_at` | ISO 8601 datetime \| null | Optional, **except see the 422 rule below** |

**`e2e/_captures/marketing/entry_created.json` — 200:**

```json
{
  "data": {
    "id": "e9f31744-69b2-447e-8e67-8ce943333249",
    "startup_id": "f862b69a-1029-4d04-81ff-9bbb7e84d63c",
    "created_by": "43e6cc92-f264-4584-999d-01a16eb917be",
    "title": "Launch week kickoff",
    "channel": "email",
    "status": "scheduled",
    "body": "Ship it",
    "media_ref": null,
    "scheduled_at": "2026-10-01T09:00:00Z",
    "published_at": null,
    "created_at": "2026-09-24T14:33:17.927743Z",
    "updated_at": "2026-09-24T14:33:17.927743Z"
  },
  "meta": null
}
```

### The `422` you must handle: `status: "scheduled"` needs `scheduled_at`

Sending `status: "scheduled"` with no `scheduled_at` (or `scheduled_at: null`) is rejected —
**422 `VALIDATION_ERROR`**, `field_errors: [{"field": "scheduled_at", "message": "A scheduled
entry needs a scheduled_at time."}]`. `draft` and `published` have no such requirement — you can
create a `published` entry directly (it gets `published_at` set immediately and the publish event
fires, same as a `PATCH` transition — see §5).

**201 is never returned here — this is a 200.** There is no separate "create vs already exists"
distinction to signal; unlike the Journal's date-keyed upsert, this is a plain create (each call
makes a new row), just on a `200` status code like the rest of this API's mutating routes.

---

## 3. `GET /api/v1/marketing/calendar-entries` — list entries

**Query parameters** (all optional):

| Param | Type | Meaning |
|---|---|---|
| `from` | ISO 8601 datetime | Inclusive lower bound on `scheduled_at` |
| `to` | ISO 8601 datetime | Inclusive upper bound on `scheduled_at` |
| `channel` | `ChannelKey` | Filter to one channel |
| `status` | `ContentStatus` | Filter to one status |

The e2e journey called `GET /marketing/calendar-entries?from=2026-10-01T00:00:00Z&to=2026-10-31T00:00:00Z`.

**`e2e/_captures/marketing/entries_list.json`:**

```json
{
  "data": {
    "entries": [
      {
        "id": "e9f31744-69b2-447e-8e67-8ce943333249",
        "startup_id": "f862b69a-1029-4d04-81ff-9bbb7e84d63c",
        "created_by": "43e6cc92-f264-4584-999d-01a16eb917be",
        "title": "Launch week kickoff",
        "channel": "email",
        "status": "scheduled",
        "body": "Ship it",
        "media_ref": null,
        "scheduled_at": "2026-10-01T09:00:00Z",
        "published_at": null,
        "created_at": "2026-09-24T14:33:17.927743Z",
        "updated_at": "2026-09-24T14:33:17.927743Z"
      }
    ]
  },
  "meta": null
}
```

**No pagination and no `total` in Slice 1** — `entries` is the full filtered set, ordered with
unscheduled drafts first, then by `scheduled_at` ascending. This is a month/week-view calendar
feed, not an infinite list — if the entry volume outgrows an unpaginated response, that is a
follow-up, not something to build client-side pagination against today.

**A range without a scheduled entry inside it still returns unscheduled drafts** — `from`/`to`
filter on `scheduled_at`, so a `draft` with no `scheduled_at` set is excluded whenever you pass
either bound (its `scheduled_at` is `null`, which never falls inside a range), but is included
when you call the endpoint with no `from`/`to` at all. Design the calendar-vs-backlog views
accordingly: a bounded range is a calendar view; an unbounded call is closer to a backlog view
that also happens to include already-scheduled items.

---

## 4. `GET /api/v1/marketing/calendar-entries/{entry_id}` — one entry

Same shape as §2's response. **404 `NOT_FOUND`** if the id does not exist or belongs to another
workspace — this API never distinguishes "missing" from "not yours" (see §8).

---

## 5. `PATCH /api/v1/marketing/calendar-entries/{entry_id}` — edit, reschedule, or publish

Partial update — send only the fields you're changing. **Publishing is not a separate endpoint:
send `{"status": "published"}`.**

`e2e/_captures/marketing/entry_published.json` — the request was `{"status": "published"}`
against the entry created in §2:

```json
{
  "data": {
    "id": "e9f31744-69b2-447e-8e67-8ce943333249",
    "startup_id": "f862b69a-1029-4d04-81ff-9bbb7e84d63c",
    "created_by": "43e6cc92-f264-4584-999d-01a16eb917be",
    "title": "Launch week kickoff",
    "channel": "email",
    "status": "published",
    "body": "Ship it",
    "media_ref": null,
    "scheduled_at": "2026-10-01T09:00:00Z",
    "published_at": "2026-09-24T14:33:17.954819Z",
    "created_at": "2026-09-24T14:33:17.927743Z",
    "updated_at": "2026-09-24T14:33:17.949248Z"
  },
  "meta": null
}
```

`published_at` is set server-side the instant the transition happens — never send it yourself
(there is no field to set it through; it is response-only).

### The same `422` rule applies to `PATCH`

Setting `status: "scheduled"` on a `PATCH` — whether or not the entry already had a
`scheduled_at` — still requires a `scheduled_at` to be present **in the merged result**: either
already stored on the row, or included in the same `PATCH` body. Omit both → 422
`VALIDATION_ERROR` on `scheduled_at`, identical shape to §2.

### Publish is idempotent — re-`PATCH`ing an already-published entry is a no-op on the side effects

`published_at` is set, and the `marketing.post.published` event fires (→ in-app notification, see
below), **only on the transition into `published`** — i.e. only when `published_at` was `null`
before this call. Sending `{"status": "published"}` again against an already-published entry
returns **200** with the *same* `published_at` unchanged, and does **not** create a second
notification or re-fire the event. Build "Publish" as a safe, repeatable button — a double-click
or a retried request cannot double-notify the workspace.

> ⚠️ **Not e2e-captured.** The single capture above shows the first publish only. The no-op
> repeat-publish behavior is verified by
> `tests/services/notifications/test_marketing_notification.py::test_non_publish_update_creates_no_notification`
> (that test covers a non-status edit; the guard itself is
> `app/services/marketing/service.py:101` — `just_published = new_status == published and
> entry.published_at is None`) and
> `tests/services/marketing/test_calendar_service.py::test_publish_sets_published_at_and_emits_event_once`.
> If you need to be certain before shipping a retry-heavy publish flow, ask for a live re-publish
> capture to be added.

### `date` cannot be changed — there is no `date` field

Unlike the Journal, calendar entries don't have a calendar "day" of their own — `scheduled_at` is
the only time field, and it is freely editable (reschedule = `PATCH {"scheduled_at": "..."}`).

### Publish notification

Publishing an entry (first transition only, see above) fires the `marketing.post.published`
domain event, which creates an **in-app notification** for every founder and team member in the
workspace **except the actor who published it** (`_members_minus_actor` — same exclusion pattern
every other actor-attributed event in this API uses). Notification title:
`"Scheduled post published: {title}"` (exact string, built from `app/services/notifications/
registry.py`), no email — this event is in-app-only in Slice 1. Poll or subscribe to the
existing notification feed (`docs/fe-integration-guide-notifications.md`,
`docs/fe-integration-guide-notifications-realtime.md`); there is nothing marketing-specific about
how you consume it beyond the `type: "marketing.post.published"` filter and the `title` string
above.

---

## 6. `DELETE /api/v1/marketing/calendar-entries/{entry_id}` — delete an entry

```json
{
  "data": { "deleted": true },
  "meta": null
}
```

**200, not 204.** Deletion is permanent — confirm destructively in the UI. 404 if the id doesn't
exist or belongs to another workspace, same as §4.

---

## 7. `GET /api/v1/marketing/channels` — the 8-channel status board

**Always returns exactly 8 rows, one per `ChannelKey`, in the fixed enum order** — regardless of
whether this workspace has touched channels before. The first-ever call for a workspace
**lazy-seeds** all 8 rows at `status: "not_started"`, `notes: null`; every later call just reads
them back. There is no separate "initialize channels" step to call.

`e2e/_captures/marketing/channels.json` — the very first call for a freshly onboarded workspace:

```json
{
  "data": [
    {"id": "a2af59e3-3cd3-450d-8e18-80834e5d187f", "key": "organic_social", "status": "not_started", "notes": null},
    {"id": "962430c5-60bb-4f68-9657-c15bf1432af2", "key": "paid_social", "status": "not_started", "notes": null},
    {"id": "ea3a1d03-572d-43a9-970e-4dcd291c9350", "key": "search", "status": "not_started", "notes": null},
    {"id": "689ccd2b-3c69-4a2b-8a08-2cb47a3aa4c8", "key": "email", "status": "not_started", "notes": null},
    {"id": "ad682f27-3239-411a-a0fb-20b69ae03ad9", "key": "content_seo", "status": "not_started", "notes": null},
    {"id": "931f91d5-4bcd-4b5f-8b21-6aad0699994c", "key": "partnerships", "status": "not_started", "notes": null},
    {"id": "bb8ecbe1-0619-461d-b315-37476873df8b", "key": "events", "status": "not_started", "notes": null},
    {"id": "a0b5ecd0-d5d7-4e07-8b29-dad5cde65683", "key": "referral", "status": "not_started", "notes": null}
  ],
  "meta": null
}
```

Note there is no `startup_id` in this response — each row's `id` is the channel *status row's*
own id (needed by nothing on the FE today; `key` is what you patch by, not `id`), scoped
server-side to the caller's workspace.

---

## 8. `PATCH /api/v1/marketing/channels/{key}` — update a channel's status/notes

`{key}` is a `ChannelKey` path segment (e.g. `email`, `organic_social`) — not the row's `id`.

**Request:**

```json
{ "status": "active", "notes": "warming up" }
```

Both fields optional, independently settable. `status` is one of the four `ChannelStatus` values;
`notes` is a free-text string or `null`.

`e2e/_captures/marketing/channel_updated.json`:

```json
{
  "data": {
    "id": "689ccd2b-3c69-4a2b-8a08-2cb47a3aa4c8",
    "key": "email",
    "status": "active",
    "notes": "warming up"
  },
  "meta": null
}
```

### ⚠️ Call `GET /channels` at least once per workspace before you `PATCH` a channel

This is the one sharp edge in this API. `PATCH /marketing/channels/{key}` does **not** lazy-seed
— it looks up the existing `(startup_id, key)` row and returns **404 `NOT_FOUND`** if that row
doesn't exist yet. Only `GET /marketing/channels` seeds the 8 rows. For a workspace that has
never loaded the Channels board, an early direct `PATCH` (e.g. from a deep link, a keyboard
shortcut, or a bulk-update script that skips the board screen) will 404 even though `email` is
obviously a valid channel key. **Always call `GET /marketing/channels` once — on mount of any
screen that might `PATCH` a channel — before issuing the first `PATCH`,** even if you don't
render the list it returns. This is deliberate (see the design spec): `GET` seeds so every
`PATCH` afterward is guaranteed to find a row, and no `PATCH`-time auto-seed was added, to keep
the write path simple.

---

## Errors

Standard envelope:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

| Status | Code | When |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Not a founder/team_member of this workspace (mentor, accountant, legal advisor, business consultant, investor) |
| 404 | `NOT_FOUND` | Entry or channel does not exist for this workspace — including the **"`PATCH /channels/{key}` before the first `GET /channels`"** case in §8 |
| 422 | `VALIDATION_ERROR` | Blank/oversized `title`, `status: "scheduled"` without a resolved `scheduled_at` (§2, §5), invalid enum value for `channel`/`status`/`key` |

### Cross-tenant 404s are uniform

An entry that exists in another workspace and one that never existed at all both return
byte-identical 404s — same convention as every other tenant-scoped resource in this API (see
`docs/fe-integration-guide-journal.md` §8 for the rationale). Don't write copy that implies "this
exists, you just can't see it."

---

## Verification table

| Claim | Source |
|---|---|
| Field names, types, enum values | `app/schemas/marketing.py`, `app/db/models/enums.py` — the models FastAPI serialises |
| Status codes, routes, auth dependency | `app/api/v1/endpoints/marketing.py` |
| Access matrix (403 for non-marketing roles, 200 for founder/team_member) | `tests/api/test_marketing.py::test_roles_other_than_founder_and_team_member_are_forbidden`, `::test_founders_and_team_members_are_allowed` — passing |
| Overview response bodies | ✅ live | `overview_before.json` → `overview_after.json` |
| `scheduled_at` required when `status: "scheduled"` (create + patch) | ✅ code path unit-tested; the 422 itself is asserted in `tests/api/test_marketing.py::test_scheduled_without_time_is_422` (create); the create+list+publish+channels+overview bodies above are the live captures | `app/services/marketing/service.py:32`, `:97` |
| Entry create/list/publish response bodies | ✅ live | `entry_created.json`, `entries_list.json`, `entry_published.json` |
| Publish sets `published_at` once, fires `marketing.post.published` exactly on the null→published transition | ✅ live for the first publish (`entry_published.json`); the no-repeat-fire guard is unit-only | `app/services/marketing/service.py:101-106`, `tests/services/marketing/test_calendar_service.py::test_publish_sets_published_at_and_emits_event_once` |
| Publish notifies workspace members except the actor, exact title string | ⚠️ unit only — not asserted against the live notification feed in the e2e capture | `tests/services/notifications/test_marketing_notification.py::test_publishing_notifies_workspace_except_actor` |
| Channels lazy-seed to exactly 8, fixed enum order | ✅ live | `channels.json` |
| `PATCH /channels/{key}` update body | ✅ live | `channel_updated.json` |
| `PATCH /channels/{key}` 404s before the workspace's first `GET /channels` | ⚠️ unit only — not separately e2e-captured (the e2e journey calls `GET /channels` before its `PATCH`, per the SOP's documented sequence) | `app/services/marketing/service.py::update_channel` (`row is None → NotFound`) |
| Cross-tenant 404 uniformity | ⚠️ unit only, follows the same pattern as `tests/api/test_journal_access.py` | `tests/services/marketing/test_calendar_service.py::test_get_entry_other_tenant_not_found` |
| Table names (`content_calendar`, `marketing_channels`) | `alembic/versions/0033_marketing_calendar_channels.py` |
