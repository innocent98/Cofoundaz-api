# FE Integration Guide — Notifications (Module 20, Slices 1–3: In-App Feed + Email + Scheduler)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_notifications.py::test_notifications_journey` (Slice 1),
`e2e/test_notifications_email.py::test_email_delivery_and_preferences` (Slice 2), and
`e2e/test_notifications_scheduler.py::test_scheduled_mission_ready_notification` (Slice 3) running
against a real server (`scripts/e2e_run.sh`) — see `e2e/_captures/notifications/*.json`,
`e2e/_captures/notifications_email/*.json`, and `e2e/_captures/notifications_scheduler/*.json`.
Nothing here is retyped from the schema, the service, or memory. IDs, tokens, and timestamps are
real values from that ephemeral test run (they differ on every real request; the shapes are exact).
Every payload, status code, and error body in this guide was exercised live, except the rows
explicitly marked "unit only" or "not captured live" in §8's verification table — Slice 3 adds two
such rows (`roadmap.milestone.overdue`, `assessment.quarterly.due`): neither is naturally reachable
from a fresh e2e signup (see §10), so their `data` shape is sourced from the handler unit tests
(`tests/worker/test_scheduled_handlers.py`), cited inline, not invented.

Base path: `/api/v1`. All four routes require a Bearer access token
(`Authorization: Bearer <token>`) + `X-Workspace-Id` header — same convention as every other
workspace-scoped module. There is no public/unauthenticated route in this slice (unlike Documents
Sharing/E-signature).

Every success response is the standard envelope `{"data": …, "meta": null}`. Errors drop
`data`/`meta` and return `{"error": {…}}` (see §6).

---

## 0. The model in one paragraph

A `notification` is a **per-(user, workspace) row**, not a per-event row. When a workspace event
fires (a document gets shared, a suggestion gets approved, a teammate joins, …), the backend fans it
out server-side into one row per recipient — if 3 members should see it, 3 separate rows exist, each
independently readable/mark-readable/deletable-in-effect by only that one user. **Your feed and your
teammate's feed for "the same event" are different rows with different `id`s** — never assume
marking your own copy read affects anyone else's (§4a has a live-captured cross-user 404 proving
this scoping). Delivery is **in-app always** + **email, opt-out, gated by category** (§9); there is
still no real-time (websocket/push) delivery — the FE must poll (§2 has a suggested cadence) rather
than expect a push event when a new notification arrives. As of Slice 3, some events are fired by a
**scheduler**, not by another user's action — §10 covers what that means for the FE (same feed/poll
model, no new API shape, but "no user action happened" is a UX fact worth designing around).

---

## 1. `GET /api/v1/notifications` — the feed

Query params: `unread` (bool, default `false`), `limit` (int, default `20`, server-clamped to
`[1, 50]` — sending `100` silently gets you `50`, not a 422), `cursor` (opaque string, from a prior
response's `next_cursor`).

**Response shape** — always `{"notifications": [...], "next_cursor": string | null}`. Rows are
newest-first (`created_at desc`, `id desc` as a tiebreak).

**Empty feed** (before any event — `e2e/_captures/notifications/feed_before_any_event.json`):

```json
{
  "data": {
    "notifications": [],
    "next_cursor": null
  },
  "meta": null
}
```

**Populated feed, `unread=true`** — two real `document.shared` notifications for the same recipient,
from two separate share actions (`e2e/_captures/notifications/feed_unread_only.json`):

```json
{
  "data": {
    "notifications": [
      {
        "id": "44f41e6d-cba4-4a07-b853-1d2ee6d6ebcf",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "6d2bb593-8d73-4ea1-9f34-21c013b73227",
          "startup_id": "e93df778-2eee-4f1a-9a1e-c35e7b1e4769",
          "document_id": "9ca6f88c-50a5-4712-8387-4c1a8aa403b1",
          "shared_by_id": "817852f1-40b8-41da-827b-fd7fdb46f886"
        },
        "read": false,
        "created_at": "2026-09-14T16:16:00.287901+00:00"
      },
      {
        "id": "24c17596-423e-4d64-ac03-2966818e5b9e",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "5315072c-2c86-48b9-b604-fde094c5944a",
          "startup_id": "e93df778-2eee-4f1a-9a1e-c35e7b1e4769",
          "document_id": "9ca6f88c-50a5-4712-8387-4c1a8aa403b1",
          "shared_by_id": "817852f1-40b8-41da-827b-fd7fdb46f886"
        },
        "read": false,
        "created_at": "2026-09-14T16:16:00.277251+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

Note `data.shared_by_id` — every `document.shared` row now carries the sharer's user id (added by the
actor-exclusion fix, §5a); the recipient's own feed never contains a row for their own share.

**Row shape:** `id`, `type` (a dotted event name — see §5's catalog), `title` (fixed, generic per
`type` — see the note below), `body` (currently always `""` for every v1 type — do not render an
empty second line, treat `body` as reserved-for-future rather than a real subtitle today), `data`
(the raw event payload — its shape varies **per `type`**, see §5), `read` (bool), `created_at`
(ISO 8601 UTC).

**`title` is generic per event type, not per-instance.** `"A document was shared in your workspace"`
does not name the document, the sharer, or the recipient — every `document.shared` notification, for
every document, for every sharer, has this exact same `title` string. **If your UI wants a richer
line (e.g. "Ada shared 'Business Plan' with you"), you must build it client-side from `type` + `data`
+ a lookup** (e.g. `data.document_id` against a document you already have loaded) — the backend does
not do this interpolation in v1 (tracked as a follow-up in the SOP, not built here).

---

## 2. `GET /api/v1/notifications/unread-count` — the bell badge

No query params. `{"unread": <int>}`.

**Before any event** (`unread_count_before.json`):
```json
{ "data": { "unread": 0 }, "meta": null }
```

**After two unread events** (`unread_count_after_two_events.json`):
```json
{ "data": { "unread": 2 }, "meta": null }
```

**After marking one of two read** (`unread_count_after_single_read.json`):
```json
{ "data": { "unread": 1 }, "meta": null }
```

**After `read-all`** (`unread_count_after_read_all.json`):
```json
{ "data": { "unread": 0 }, "meta": null }
```

**Suggested polling cadence:** this endpoint is cheap (a single `COUNT` scoped by two indexed
columns) and is the right one to poll for the bell badge — poll it, not the full feed, on an
interval (e.g. every 30–60s, or on window-focus) since there is no push/websocket delivery in this
slice. Only fetch the full feed (§1) when the user actually opens the notifications panel.

---

## 3. Keyset pagination — the `next_cursor` cadence

`GET /notifications?limit=1` returns exactly 1 row plus a non-null `next_cursor` when more rows
exist. Pass that value back verbatim as `?cursor=...` to get the next page; the **last** page's
`next_cursor` is `null` — that is your "no more pages" signal, not an empty `notifications` array (a
page can be non-empty with `next_cursor: null` if it's the last page).

**Page 1**, `limit=1` (`e2e/_captures/notifications/feed_page1_limit1.json`):
```json
{
  "data": {
    "notifications": [
      {
        "id": "44f41e6d-cba4-4a07-b853-1d2ee6d6ebcf",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "6d2bb593-8d73-4ea1-9f34-21c013b73227",
          "startup_id": "e93df778-2eee-4f1a-9a1e-c35e7b1e4769",
          "document_id": "9ca6f88c-50a5-4712-8387-4c1a8aa403b1",
          "shared_by_id": "817852f1-40b8-41da-827b-fd7fdb46f886"
        },
        "read": false,
        "created_at": "2026-09-14T16:16:00.287901+00:00"
      }
    ],
    "next_cursor": "MjAyNi0wOS0xNFQxNjoxNjowMC4yODc5MDErMDA6MDB8NDRmNDFlNmQtY2JhNC00YTA3LWI4NTMtMWQyZWU2ZDZlYmNm"
  },
  "meta": null
}
```

**Page 2**, same `limit=1`, `cursor=<page 1's next_cursor>`
(`e2e/_captures/notifications/feed_page2_cursor.json`):
```json
{
  "data": {
    "notifications": [
      {
        "id": "24c17596-423e-4d64-ac03-2966818e5b9e",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "5315072c-2c86-48b9-b604-fde094c5944a",
          "startup_id": "e93df778-2eee-4f1a-9a1e-c35e7b1e4769",
          "document_id": "9ca6f88c-50a5-4712-8387-4c1a8aa403b1",
          "shared_by_id": "817852f1-40b8-41da-827b-fd7fdb46f886"
        },
        "read": false,
        "created_at": "2026-09-14T16:16:00.277251+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

**`cursor` is opaque — do not decode, construct, or persist-and-mutate it client-side.** It is
base64 of an internal `"{created_at}|{id}"` pair, but that encoding is an implementation detail, not
a contract; treat it as an opaque token you only ever pass straight back to the same endpoint. A
malformed cursor (hand-edited, truncated, or from a different collection) gets
`422 VALIDATION_ERROR` — see §6.

**Infinite-scroll pattern:** fetch with no `cursor` for the first page, append `notifications` as the
user scrolls, and stop requesting once a response's `next_cursor` is `null`.

---

## 4. Marking read

### 4a. `POST /api/v1/notifications/{notification_id}/read` — mark ONE read

No request body. Returns the updated, serialized notification.

**Response — 200** (`e2e/_captures/notifications/mark_read.json`):
```json
{
  "data": {
    "id": "44f41e6d-cba4-4a07-b853-1d2ee6d6ebcf",
    "type": "document.shared",
    "title": "A document was shared in your workspace",
    "body": "",
    "data": {
      "share_id": "6d2bb593-8d73-4ea1-9f34-21c013b73227",
      "startup_id": "e93df778-2eee-4f1a-9a1e-c35e7b1e4769",
      "document_id": "9ca6f88c-50a5-4712-8387-4c1a8aa403b1",
      "shared_by_id": "817852f1-40b8-41da-827b-fd7fdb46f886"
    },
    "read": true,
    "created_at": "2026-09-14T16:16:00.287901+00:00"
  },
  "meta": null
}
```

**Idempotent** — marking an already-read notification read again still returns 200 with `read: true`
(does not error, does not bump/change anything else) — `e2e/_captures/notifications/
mark_read_idempotent.json` is byte-identical in shape to the first mark-read response above (only
`created_at` for the row is unchanged; there is no separate "already read" signal). Safe to fire
optimistically from the FE without checking current state first.

**Cross-user scoping — a 404, not a 403.** Trying to mark a notification `id` that belongs to a
*different user's* copy of the same event (§0 — each recipient gets their own row) returns a plain
`404 NOT_FOUND`, identical in shape to marking a completely unknown/random id
(`e2e/_captures/notifications/mark_read_404_cross_user.json` vs.
`e2e/_captures/notifications/mark_read_404_unknown.json` — both byte-identical error bodies). **This
is deliberately uniform** (same convention as Documents Sharing's public-open 404) — the API never
tells a caller "that id exists but isn't yours." Build one generic "notification not found" FE state,
not a distinct "forbidden" one.

### 4b. `POST /api/v1/notifications/read-all` — mark every unread row read

No request body, no query params — always scoped to the caller's own `(user, workspace)`. Returns
`{"marked": <int>}`, the count actually flipped (rows already read are not re-counted).

**Response — 200** (`e2e/_captures/notifications/read_all.json`, called with exactly 1 unread row
remaining):
```json
{ "data": { "marked": 1 }, "meta": null }
```

**`read-all` marks, it does not delete.** The full (non-`unread`-filtered) feed still shows every
row afterward, now all with `read: true`
(`e2e/_captures/notifications/feed_full_after_read_all.json` — 2 rows, both `read: true`); only the
`unread=true`-filtered feed goes empty
(`e2e/_captures/notifications/feed_unread_empty_after_read_all.json`). If your UI has a "Clear all"
action that implies rows disappear, that's a client-side filter/animation choice, not a server-side
delete — there is no delete endpoint for notifications in this slice.

---

## 5. The `type` catalog — v1 + Slice 3 handled events

Every notification's `type` is one of these 18 dotted event names (15 from Slices 1–2, 3 new from
Slice 3 — §10). `data` is the **raw event payload** for that type — its shape is fixed per `type`
but different across types (no shared schema), so the FE should switch/deep-link on `type` and read
`data`'s fields accordingly.

| `type` | `data` fields (for deep-linking) | Recipients | Verified live? |
|---|---|---|---|
| `document.shared` | `startup_id`, `document_id`, `share_id`, `shared_by_id` | active members minus actor ✅ (see §5a) | ✅ `feed_unread_only.json` |
| `document.signature.requested` | `startup_id`, `request_id`, `file_id`, `created_by` | active members minus actor ✅ | not captured live |
| `document.signature.signed` | `startup_id`, `request_id`, `signer_id` | all active members (passive/system event — no member actor) | not captured live |
| `document.signature.completed` | `startup_id`, `request_id` | all active members (passive/system event — no member actor) | not captured live |
| `business.suggestion.created` | `startup_id`, `suggestion_id`, `op`, `created_by` | active members minus actor ✅ | not captured live |
| `business.suggestion.approved` | `startup_id`, `suggestion_id`, `op`, `actor_id` | active members minus actor ✅ | not captured live |
| `business.suggestion.rejected` | `startup_id`, `suggestion_id`, `op`, `actor_id` | active members minus actor ✅ | not captured live |
| `business.artifact.completed` | `startup_id`, `artifact` | all active members (passive/system event — no member actor) | not captured live |
| `roadmap.replanned` | `startup_id`, `roadmap_id`, `replan_id`, `change_count`, `applied_by` | active members minus actor ✅ | not captured live |
| `roadmap.milestone.completed` | `startup_id`, `roadmap_id`, `milestone_id`, `title`, `actor_id` | active members minus actor ✅ | not captured live |
| `mission.completed` | `startup_id`, `mission_id`, `mission_date` | all active members (passive/system event — no member actor) | not captured live |
| `mission.streak.milestone` | `startup_id`, `streak` | all active members (passive/system event — no member actor) | not captured live |
| `healthscore.dropped` | `startup_id`, `score`, `previous_score`, `delta_7d`, `computed_at` | all active members (passive/system event — no member actor) | not captured live |
| `assessment.completed` | `assessment_id`, `startup_id`, `dimension_scores` | all active members (passive/system event — no member actor) | not captured live |
| `workspace.member.joined` | `startup_id`, `user_id`, `role` | existing active members, correctly excluding the new joiner | ✅ `actor_excluded_from_own_action.json` (shows this type present in the actor's own feed after a teammate joined) |
| `mission.ready` | `startup_id`, `mission_id` | all active members (scheduled/system event — no member actor) | ✅ `mission_ready_feed.json` |
| `roadmap.milestone.overdue` | `startup_id`, `milestone_id` | all active members (scheduled/system event — no member actor) | ⚠️ unit only — `tests/worker/test_scheduled_handlers.py::test_overdue_publishes_only_if_still_overdue` (asserts the exact payload published) |
| `assessment.quarterly.due` | `startup_id` | all active members (scheduled/system event — no member actor) | ⚠️ unit only — `tests/worker/test_scheduled_handlers.py::test_quarterly_publishes` (asserts the exact payload published) |

Non-`data`-shape columns (`title`/`body`) are fixed per type, not shown per-row above — see §1 for
why `title` is generic and `body` is currently always `""`. The 3 Slice-3 rows are **scheduled/system
events**, same "no member actor" recipient shape as `mission.completed`/`healthscore.dropped`/etc. —
see §10 for what triggers each one and why 2 of the 3 could not be captured live in this e2e run.

**"not captured live" above means:** the `data` field list for that type is read directly from its
publish-site source (cited per-row in the SOP), not captured live in this journey — only
`document.shared` and `workspace.member.joined` were actually exercised end-to-end in this task's
e2e run. Only two of the 15 types have a registry-level unit test asserting recipient logic
specifically (`test_members_minus_actor_real_document_shared_payload` for `document.shared`,
`test_member_joined_notifies_existing_members_not_joiner` for `workspace.member.joined`); the rest
are covered only by `test_all_specs_have_generic_copy` (asserts every `SPECS` entry renders a
non-empty title without raising — a smoke check, not a recipient-logic or `data`-shape assertion).

### 5a. ✅ Actor exclusion — "minus actor" now correctly excludes the actor

**Spec decision D2** (recipients = active members minus the actor) now holds for every event that
has a member actor. Each user-initiated publish site puts an actor-identifying key into its event
payload (`shared_by_id`, `created_by`, or `actor_id` — `applied_by` for `roadmap.replanned`, which
already carried it), and `_actor()` in `app/services/notifications/registry.py` resolves it, so
`_members_minus_actor` genuinely drops the acting user from the recipient list. The 7 events marked
✅ "active members minus actor" above are covered by this fix; `workspace.member.joined` was already
correct via its own dedicated `_existing_members` code path (unchanged). The remaining events marked
"passive/system event" have no member actor at all (they're triggered by missions, health-score
recompute, signature completion, etc., not by one member acting on another) — "notify all active
members" is the correct, intended behavior for those, not a gap.

**Captured live (`document.shared`):** A shares the same document twice. B's feed
(`e2e/_captures/notifications/feed_unread_only.json`) shows both rows with `shared_by_id` now
present in `data`, pointing at A:

```json
{
  "data": {
    "notifications": [
      {
        "type": "document.shared",
        "data": {
          "share_id": "6d2bb593-8d73-4ea1-9f34-21c013b73227",
          "startup_id": "e93df778-2eee-4f1a-9a1e-c35e7b1e4769",
          "document_id": "9ca6f88c-50a5-4712-8387-4c1a8aa403b1",
          "shared_by_id": "817852f1-40b8-41da-827b-fd7fdb46f886"
        },
        "read": false,
        "...": "..."
      }
    ]
  }
}
```

A's own feed (`e2e/_captures/notifications/actor_excluded_from_own_action.json`), captured right
after both shares, contains **zero** `document.shared` rows — only the unrelated
`workspace.member.joined` notification from B's earlier invite acceptance:

```json
{
  "data": {
    "notifications": [
      {
        "id": "b20cc02b-01f7-439a-9c8a-1e69a01f220e",
        "type": "workspace.member.joined",
        "title": "A new member joined your workspace",
        "body": "",
        "data": {
          "role": "team_member",
          "user_id": "97394be8-ed1e-4154-bba1-79d79a4398f4",
          "startup_id": "e93df778-2eee-4f1a-9a1e-c35e7b1e4769"
        },
        "read": false,
        "created_at": "2026-09-14T16:16:00.184056+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```
*(both bodies pasted verbatim from the cited capture files — the `document.shared` excerpt above is
trimmed to one row for brevity; the full two-row body is in `feed_unread_only.json`.)*

**FE guidance:** the backend now filters out "you did this" rows for every event with a member
actor, so the notifications panel does not need a client-side heuristic to hide a user's own
actions on the 7 events above (or on `workspace.member.joined`). For the passive/system events
(missions, health score, etc.) there is no actor to exclude — every active member, including one
whose own mission/health-score triggered the event, is an intended recipient.

---

## 6. Errors

Standard envelope:
```json
{ "error": { "code": "NOT_FOUND", "message": "Not found.", "field_errors": [] } }
```

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | — | Missing/invalid access token | not captured in this journey — same auth dependency as every other module |
| 403 | `FORBIDDEN` | Non-member workspace (`X-Workspace-Id` the caller isn't an active member of) | not captured live — same `require_workspace` gate as every other module, no dedicated notifications test needed beyond the shared dependency's own coverage |
| 404 | `NOT_FOUND` | Unknown notification id, OR a real id that belongs to a different user/workspace (uniform — §4a) | ✅ `mark_read_404_unknown.json`, `mark_read_404_cross_user.json` |
| 422 | `VALIDATION_ERROR` | Malformed/tampered pagination `cursor` | not captured live — see §8 |

---

## 7. Delivery scope — in-app immediate, email async (Slice 2 adds email)

**In-app is still the only delivery the FE can poll or render a feed from** — a notification row
exists the instant its triggering action commits (same-transaction fan-out, unchanged from Slice 1),
and the ONLY way the FE *reads* it is by calling this API; there is no server-pushed event, webhook,
or SSE stream. **The FE must still poll** (§2's suggested cadence for the badge; fetch the full feed
on-demand when the panel opens).

**As of Slice 2, an email may ALSO be sent for the same event — but never synchronously, and never
guaranteed to arrive before (or even shortly after) the in-app row is visible.** See §9 for the full
preferences contract, the category catalog, and what "asynchronous & best-effort" means concretely
for UI design. **As of Slice 3, some events are fired by a scheduler, not a user action** — §10
covers the three triggers. Real-time push/websocket delivery is still Slice 4, unbuilt — do not
design a "you'll get a push for this" affordance based on this doc.

---

## 9. Preferences & email (Slice 2)

New in Slice 2: two routes for reading/writing a member's own per-workspace email preferences, and a
background worker (separate `worker` process/container, NOT the API process) that actually sends the
email. Both routes require the same auth as every other route in this doc (Bearer token +
`X-Workspace-Id`) and scope strictly to `(membership.user_id, membership.startup_id)` — exactly like
the feed routes in §1–4; there is no cross-user or cross-workspace read/write surface.

### 9.1 The model in one paragraph

Preferences are **per-(user, workspace)**, same granularity as notifications themselves — a member
who belongs to two workspaces has two independent preference rows, one per workspace. There are two
independent controls: `master_email` (bool — a single global email on/off switch) and `categories`
(one bool per category in the catalog below). **Both must be true for a given event's email to send**
— `master_email: true` AND `categories.<that event's category>: true`. Turning `master_email` off
mutes every category's email without touching the individual category toggles underneath it (they
keep whatever value they had — flipping `master_email` back on later restores exactly the per-category
mix the member had before). **In-app delivery (§1–4) ignores preferences entirely** — every category
toggle and `master_email` govern the email channel ONLY; a member who turns everything off still sees
every notification in their in-app feed/badge, just never gets emailed about it.

A brand-new member (no preferences row written yet) gets **all-defaults**: `master_email: true`,
every category `true` — this is an **opt-out model** (everything mailed by default; the member turns
categories off), not opt-in. `GET`ting before ever `PUT`ting returns these defaults synthesized
in-memory, not a 404 — there is no "no preferences set yet" error state for the FE to handle.

### 9.2 The category catalog

| Category key | Events it covers | Default |
|---|---|---|
| `documents` | `document.shared`, `document.signature.requested`, `document.signature.signed`, `document.signature.completed` | `true` |
| `business` | `business.suggestion.created`, `business.suggestion.approved`, `business.suggestion.rejected`, `business.artifact.completed` | `true` |
| `roadmap_missions` | `roadmap.replanned`, `roadmap.milestone.completed`, `mission.completed`, `mission.streak.milestone`, `mission.ready`*, `roadmap.milestone.overdue`* | `true` |
| `health_assessment` | `healthscore.dropped`, `assessment.completed`, `assessment.quarterly.due`* | `true` |
| `team` | `workspace.member.joined` | `true` |

\* Added in Slice 3 (§10) — scheduled/system events, no new category, folded into the existing
`roadmap_missions`/`health_assessment` buckets.

This is the exact same event→category map §5's `type` catalog uses for deep-linking (`app/services/
notifications/categories.py::EVENT_CATEGORY`) — a category toggle in a Settings UI maps 1:1 onto a
group of `type` rows the FE already renders per §5's table. There is no 6th "everything else"
category; every one of the 18 v1+Slice-3 event types in §5 falls under exactly one of these 5.

### 9.3 `GET /api/v1/notifications/preferences`

No request body, no query params. Returns the effective (defaults-merged) preferences.

**Response — 200**, captured after B had already turned `documents` off (§9.4) — this is the shape
for ANY state, defaults included; only the boolean values change
(`e2e/_captures/notifications_email/preferences_get.json`):
```json
{
  "data": {
    "master_email": true,
    "categories": {
      "documents": false,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true
    }
  },
  "meta": null
}
```

**All 5 category keys are always present in the response**, regardless of whether the member has
ever `PUT` any of them — do not treat a missing key as "off"; there is no missing-key case.

### 9.4 `PUT /api/v1/notifications/preferences`

Request body: `{"master_email"?: bool, "categories"?: {<category key>: bool, ...}}` — both fields are
optional and independent; send only what changed (a **partial merge**, not a full replace — omitted
category keys keep their current value, they do not reset to default). Returns the same effective
shape as the `GET` above (not a 204 — always re-read the response body rather than assuming your `PUT`
body is now the full state, since it may have been a partial update).

**Request** (turning only `documents` off; `master_email` and every other category untouched):
```json
{ "categories": { "documents": false } }
```

**Response — 200** (`e2e/_captures/notifications_email/preferences_documents_off.json`):
```json
{
  "data": {
    "master_email": true,
    "categories": {
      "documents": false,
      "business": true,
      "roadmap_missions": true,
      "health_assessment": true,
      "team": true
    }
  },
  "meta": null
}
```

Note `master_email` is still `true` and the other 4 categories are untouched `true` — proof the merge
is genuinely partial, not a full-object replace that happened to default the rest back to `true`.

**Unknown category key → `422 VALIDATION_ERROR`.** Sending any key outside the 5-row catalog above
(typo, stale FE build against a since-renamed category, hand-crafted request) is rejected outright —
NONE of the request's keys are applied, not even the valid ones alongside the bad one (request-level
validation, before the service layer ever runs). Live capture, `PUT` with
`{"categories": {"not_a_category": false}}`
(`e2e/_captures/notifications_email/preferences_put_unknown_category_422.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      { "field": "categories", "message": "Value error, unknown categories: ['not_a_category']" }
    ]
  }
}
```
Build the Settings UI as a **fixed set of 5 toggles from the catalog above**, never as a
dynamically-keyed form that could send an arbitrary string — there is no forward-compatible "unknown
category is just ignored" behavior to lean on.

### 9.5 Email is asynchronous & best-effort — in-app is immediate

**This is the single most important behavioral difference the FE must design around.** When an event
fires (e.g. a document gets shared):
1. The in-app notification row is created **synchronously, in the same transaction as the triggering
   action** (unchanged from Slice 1 — §0/§7) — it is visible via `GET /notifications` the instant the
   triggering request returns `201`/`200`.
2. If the recipient's preferences allow email for that event's category (§9.1), an
   `email.notification` **job is enqueued in the same transaction** — but NOT sent yet. It sits
   `queued` in the jobs table.
3. A **separate background `worker` process** (`python -m app.worker`, its own container in
   docker-compose — NOT the API process, NOT a thread inside the request that shared the document)
   polls that queue (`WORKER_POLL_INTERVAL`, default 2s) and sends the actual email whenever it next
   picks the job up.

**Consequences for the FE:**
- **Never block a "share"/"invite"/etc. UI flow on an email arriving** — there is no callback, no
  webhook, no status field on the triggering response that says "email sent." The triggering request
  returning success only means the in-app row (and, if applicable, the job) was queued — not that
  anyone's inbox has anything yet.
- **Do not build a "email sent" checkmark or read-receipt UI** — the API exposes no per-notification
  email-delivery status (sent/pending/failed) anywhere. The `notifications` row (§1) has no email-related
  field at all; email delivery is entirely a fire-and-forget side effect the FE cannot observe via this
  API in Slice 2.
- **A queued email can be delayed arbitrarily** (worker restart, backlog, retry/backoff on a
  transient send failure — up to `WORKER_MAX_ATTEMPTS` attempts with exponential backoff, capped at
  1h between attempts, per the SOP) — do not assume "seconds" as an SLA in copy or UX (e.g. don't
  write "check your email now").
- **Preferences changes are NOT retroactive.** Turning a category off only gates the enqueue-time
  check for events that fire AFTER the toggle — an email already enqueued (or already sent) before the
  toggle flipped is unaffected. There is no "cancel pending email" behavior.
- **The email CTA's base URL is `APP_BASE_URL`** (a deploy-time env var — the FE's own origin, e.g.
  `https://app.cofoundaz.com`), not `SERVER_HOST`/the API's own origin — falls back to `SERVER_HOST`
  only if `APP_BASE_URL` is unset (e2e capture above shows the fallback: `http://localhost/documents`,
  since e2e never sets `APP_BASE_URL`). The deep link path is category-based (`/documents`,
  `/business-builder`, `/roadmap`, `/health`, `/team`), same buckets as §9.2's catalog, not a link to
  the specific document/suggestion/etc. — same "generic per type, not per instance" limitation as the
  in-app `title` (§1).

### 9.6 What the email itself looks like

Captured live — the actual delivered message from the file email backend for a real
`document.shared` event, B's mailbox, after the worker drained the queue
(`e2e/_captures/notifications_email/delivered_email.json`):
```json
{
  "to": "teammate-70c82e43bc6a@example.com",
  "subject": "A document was shared in your workspace",
  "html": "<div style=\"font-family:system-ui,sans-serif;max-width:520px\"><h2>A document was shared in your workspace</h2><p></p><p><a href=\"http://localhost/documents\" style=\"display:inline-block;padding:10px 16px;background:#4f46e5;color:#fff;border-radius:6px;text-decoration:none\">Open Cofoundaz</a></p><hr><p style=\"font-size:12px;color:#666\">Manage your notification preferences in Settings.</p></div>",
  "sent_at": "2026-09-15T13:43:21.226520+00:00"
}
```
`subject` is the notification's `title` verbatim (same generic per-type copy as §1 — not
per-instance), `body` (currently always `""` per §1) is rendered but empty, and the CTA button links
to the category's deep-link path under `APP_BASE_URL` per §9.5. The email footer's "Manage your
notification preferences in Settings" line is static copy, not a real link — the FE owns wiring an
actual Settings deep link there if that's wanted; the backend does not construct one.

---

## 10. Scheduled / time-based notifications (Slice 3)

New in Slice 3: three notification types that fire **with no user action at all** — a background
scheduler (a throttled tick inside the existing `worker` process, `SCHEDULER_INTERVAL` seconds
apart, default 60s) detects due work and enqueues it into the SAME `jobs` table Slice 2's worker
already drains. There is no new route, no new auth model, no new response shape — these three types
just start appearing in the same `GET /notifications` feed (§1) and the same `unread-count` (§2) as
every other type, and follow the exact same email-gating contract as §9 (in-app always fires;
email is opt-out per category, `master_email` AND the category must both be `true`).

### 10.1 The three triggers

| `type` | Fires when | Category (email gate, §9.2) |
|---|---|---|
| `mission.ready` | Once per workspace per calendar day, the first time the scheduler ticks past `MISSION_GEN_HOUR` (default `6`, i.e. 06:00) in `SCHEDULER_TIMEZONE` (default `UTC`) — pre-generates that workspace's mission for the day if it doesn't exist yet, then notifies | `roadmap_missions` |
| `roadmap.milestone.overdue` | Once per milestone, ever — the first tick after a roadmap milestone's `due_on` passes with the milestone still not `done` | `roadmap_missions` |
| `assessment.quarterly.due` | Once per workspace per calendar quarter — only for a workspace with a prior **completed** assessment whose `completed_at` is ≥ `QUARTERLY_REASSESS_DAYS` (default 90) days old; a workspace that has never completed an assessment is never targeted by this trigger | `health_assessment` |

**"Once" is enforced server-side by a claim ledger** (`scheduled_runs`, unique on
`(task_key, scope_key, period_key)`) — the FE cannot cause a duplicate by polling more, retrying a
request, or refreshing; if the same `(workspace, day)` (or `(milestone)`, or `(workspace, quarter)`)
already fired, it will not fire again, full stop, until the next period rolls over (or, for overdue,
never again — it is genuinely once-per-milestone, not once-per-period).

### 10.2 `mission.ready` — verified live

Captured after onboarding a founder to a generated roadmap, then running the scheduler at 07:00 UTC
and draining the worker (`e2e/_captures/notifications_scheduler/mission_ready_feed.json`):

```json
{
  "data": {
    "notifications": [
      {
        "id": "aa573d19-c98d-4a90-86f8-da501b958604",
        "type": "mission.ready",
        "title": "Today's mission is ready",
        "body": "",
        "data": {
          "mission_id": "459c2c52-aa0a-455f-883a-013920696c2f",
          "startup_id": "8a56df9c-0ee0-49aa-8f87-07c71eee40cc"
        },
        "read": false,
        "created_at": "2026-09-18T22:31:57.840751+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

`data.mission_id` is the same id `GET /api/v1/mission/today` (Module 04) returns — the FE can deep
link straight into Today's Mission from this notification without a lookup. Fires for **every**
active member of the workspace, not just the founder (§5's "all active members" recipient rule) — a
teammate opening the app also sees this notification, even though they didn't trigger it.

### 10.3 `roadmap.milestone.overdue` and `assessment.quarterly.due` — verified by unit test, NOT captured live

**Neither is reachable from a fresh e2e signup without contortion**, and per this task's own
constraint, an unreachable-live shape is honestly labelled here rather than faked:
- `roadmap.milestone.overdue` needs a roadmap milestone whose `due_on` is already in the past and
  whose `status` is not `done` — no e2e journey in this suite backdates a milestone's due date.
- `assessment.quarterly.due` needs a **completed** assessment more than `QUARTERLY_REASSESS_DAYS`
  (90) days old — unreachable from a same-run signup without manipulating the clock or writing
  directly to the DB, neither of which is a live HTTP journey.

Both handlers are exercised at the unit level instead (`tests/worker/test_scheduled_handlers.py`),
which asserts the **exact payload** each one publishes — reproduced verbatim below, not re-derived
from the schema:

**`roadmap.milestone.overdue`** (`test_overdue_publishes_only_if_still_overdue`) — `event_bus.publish`
is called with:
```json
{ "startup_id": "<startup uuid>", "milestone_id": "<milestone uuid>" }
```
The same test file also proves the handler **re-checks** at run time and suppresses the publish (no
notification, no email) if the milestone was marked `done` OR deleted between enqueue and the
handler actually running (`test_overdue_suppresses_publish_when_milestone_already_done`,
`test_overdue_suppresses_publish_when_milestone_missing`) — a milestone a teammate just finished will
NOT generate a stale "overdue" notification even if it was already queued.

**`assessment.quarterly.due`** (`test_quarterly_publishes`) — `event_bus.publish` is called with:
```json
{ "startup_id": "<startup uuid>" }
```
No `assessment_id` in the payload — the FE cannot deep-link to a specific past assessment from this
notification; the correct action is always "start a new assessment" (Module 07), not "open assessment
X".

### 10.4 UX consequences the FE must design around

- **These are the first notification types with genuinely "the system did this, not a person" as
  the entire story.** Copy/iconography that implies "someone did X" (an avatar, "X shared...") is
  wrong for all 3 — treat them as system/reminder notifications, visually distinct if your design
  language has that affordance (Slices 1–2's 15 types already include several "passive/system"
  events with the same property — §5a — so this is a continuation, not a new pattern).
- **`mission.ready` can arrive for a workspace at any hour** from the FE's perspective — it fires the
  first time the scheduler ticks past 06:00 workspace-tz, but the tick itself is throttled
  (`SCHEDULER_INTERVAL`, default 60s) and only runs inside the worker's poll loop, so "06:00 sharp" is
  not a real guarantee — do not write copy implying a precise delivery time.
- **`roadmap.milestone.overdue` fires exactly once per milestone, forever** — there is no daily/weekly
  re-nudge (explicit v1 scope decision, design doc §11 D5). If the FE wants a persistent "N overdue
  milestones" indicator, it must compute that itself from the roadmap data (`due_on` vs. today,
  `status != done`) rather than relying on a fresh notification arriving again — the notification is
  a one-time nudge, not an ongoing badge source.
- **`assessment.quarterly.due` never fires for a workspace that has never completed an assessment** —
  do not read "no quarterly-due notification yet" as "this workspace is up to date"; it may simply
  never have finished its first assessment. Cross-reference `GET /assessments` (Module 07) if the FE
  needs to distinguish "never assessed" from "recently assessed" in its own UI.
- **Email timing inherits Slice 2's async/best-effort contract unchanged** (§9.5) — a scheduled
  event's email is enqueued the moment the handler publishes, then sent whenever the worker next
  polls; same no-SLA, no-delivery-status guidance applies.

---

## 8. Verification table

All rows below except those marked "unit only" or "not captured" were exercised **live**, over real
HTTP, against a real Postgres-backed server (`scripts/e2e_run.sh`,
`e2e/test_notifications.py::test_notifications_journey` for Slice 1,
`e2e/test_notifications_email.py::test_email_delivery_and_preferences` for Slice 2, and
`e2e/test_notifications_scheduler.py::test_scheduled_mission_ready_notification` for Slice 3) — not
just unit-tested in-process — and every response body is captured verbatim in the named file.

| Behaviour | Verified live? | Source |
|---|---|---|
| `GET /notifications` — empty feed before any event, `next_cursor: null` | ✅ | `feed_before_any_event.json` |
| `GET /notifications?unread=true` — exactly the unread rows, correct `type`/`data`/`read: false` | ✅ | `feed_unread_only.json` |
| `GET /notifications/unread-count` — 0 → 2 → 1 → 0 across the journey | ✅ | `unread_count_before.json`, `unread_count_after_two_events.json`, `unread_count_after_single_read.json`, `unread_count_after_read_all.json` |
| Keyset pagination: `limit=1` returns 1 row + non-null `next_cursor`; following the cursor returns the other row + `next_cursor: null` | ✅ | `feed_page1_limit1.json`, `feed_page2_cursor.json` |
| `POST /notifications/{id}/read` — 200, `read: true` | ✅ | `mark_read.json` |
| Marking an already-read notification read again is idempotent (200, still `read: true`) | ✅ | `mark_read_idempotent.json` |
| Unknown notification id → `404 NOT_FOUND` | ✅ | `mark_read_404_unknown.json` |
| Another user's own row (same event, different recipient) → `404 NOT_FOUND`, not leaked as existing | ✅ | `mark_read_404_cross_user.json` |
| `POST /notifications/read-all` → `{marked: N}`, zeroes unread-count, does not delete rows (full feed still shows them, now `read: true`) | ✅ | `read_all.json`, `unread_count_after_read_all.json`, `feed_full_after_read_all.json`, `feed_unread_empty_after_read_all.json` |
| `document.shared` fans out to active members MINUS the actor (spec D2, §5a) — sharer's own feed has zero rows for their own shares, other member gets both | ✅ | `actor_excluded_from_own_action.json` (sharer's feed), `feed_unread_only.json` (other member's feed, `data.shared_by_id` present) |
| `workspace.member.joined` correctly excludes the joiner, notifies existing members | ✅ | `actor_excluded_from_own_action.json` (shows it present in the *existing* member's feed) |
| The other 13 `type` catalog rows' `data` shape (signature/suggestion/roadmap/mission/healthscore/assessment events) | ⚠️ not captured live | read directly from each event's `event_bus.publish(...)` call site in `app/services/**`/`app/api/v1/endpoints/roadmap.py` (cited per-row in the SOP) — not independently unit-tested per type beyond `test_all_specs_have_generic_copy`'s generic title-rendering smoke check |
| `_members_minus_actor` excludes the actor **using the real `document.shared` payload shape** (`{startup_id, document_id, share_id, shared_by_id}`, as actually produced by `create_share`) | ✅ | `tests/services/notifications/test_registry.py::test_members_minus_actor_real_document_shared_payload` |
| Non-member (`403 FORBIDDEN`) on any of the 4 routes | ⚠️ not captured — shared `require_workspace` dependency, no notifications-specific test needed | `app/db/tenancy.py::require_workspace` (used identically by every other workspace-scoped module) |
| Malformed pagination `cursor` → `422 VALIDATION_ERROR` | ⚠️ unit only | `tests/services/notifications/test_service.py::test_bad_cursor_422` |
| A failing notification handler is savepoint-isolated — the triggering action's own writes still commit, and a broken handler never 500s the caller | ⚠️ unit only | `tests/platform/test_events.py::test_failing_handler_is_isolated_and_does_not_raise` |
| `GET /notifications/preferences` — all-defaults for a brand-new member (`master_email: true`, every category `true`) | ✅ | `tests/api/test_notification_preferences.py::test_get_returns_defaults` (unit); live shape confirmed via the `PUT` round-trip below |
| `PUT /notifications/preferences` — partial merge (`{"categories": {"documents": false}}` leaves `master_email` and the other 4 categories untouched) | ✅ | `preferences_documents_off.json` |
| `GET /notifications/preferences` — reflects the `PUT` above on a fresh `GET` | ✅ | `preferences_get.json` |
| `PUT /notifications/preferences` with an unknown category key → `422 VALIDATION_ERROR`, no partial apply | ✅ | `preferences_put_unknown_category_422.json` |
| `document.shared` for a recipient with `documents` email ON → an `email.notification` job is enqueued in the SAME transaction as the in-app row, then actually sent once the worker (`runner.run_once`, drained in-process) claims it — real delivery via the file email backend, correct `subject`/deep-link | ✅ | `delivered_email.json` |
| Turning `documents` email OFF, then a second `document.shared` → a new in-app row still appears (in-app ignores preferences) but NO new `email.notification` job is enqueued / no new email is delivered after another drain | ✅ | asserted in `test_email_delivery_and_preferences` via `mailbox.count_for` before/after — no new capture file needed for a non-event (nothing new to paste) |
| A rolled-back triggering action leaves no `email.notification` job queued (transactional correctness — enqueue happens in the same transaction as the in-app row, so a rollback undoes both) | ⚠️ unit only | `tests/services/notifications/test_email_enqueue.py::test_rolled_back_action_leaves_no_email_job` |
| Worker job claim (`SELECT ... FOR UPDATE SKIP LOCKED`), retry/backoff on handler failure, stale-`RUNNING` reaper, unknown job type → terminal failure | ⚠️ unit only | `tests/worker/test_runner.py` |
| Email HTML/subject is escaped (no header-injection via CR/LF in `subject`, no unescaped HTML in a title/body containing `<script>`/`&`/quotes) | ⚠️ unit only | `tests/worker/test_email_handler.py::test_render_email_escapes_html` |
| Worker entrypoint (`python -m app.worker`) registers the email handler and runs `run_once` on a poll loop until a SIGTERM-set stop flag | ⚠️ unit only | `tests/worker/test_entrypoint.py` |
| Scheduler tick claims + enqueues due mission/overdue/quarterly jobs exactly once per `(task, scope, period)` via the `scheduled_runs` ledger | ⚠️ unit only | `tests/worker/test_scheduler.py` |
| `mission.ready` — founder onboards to a generated roadmap → scheduler tick past `MISSION_GEN_HOUR` → mission generated + notification created for the founder → feed shows it → a second same-day tick does NOT duplicate it | ✅ | `mission_ready_feed.json`; no-duplicate assertion in `test_scheduled_mission_ready_notification` itself (no second capture — nothing new to paste for a non-event) |
| `roadmap.milestone.overdue` — publishes `{startup_id, milestone_id}`; re-checked and suppressed if the milestone is done or deleted before the handler runs | ⚠️ unit only — not reachable from a fresh e2e signup without backdating a milestone | `tests/worker/test_scheduled_handlers.py::test_overdue_publishes_only_if_still_overdue`, `::test_overdue_suppresses_publish_when_milestone_already_done`, `::test_overdue_suppresses_publish_when_milestone_missing` |
| `assessment.quarterly.due` — publishes `{startup_id}`; only for workspaces with a prior completed assessment ≥ `QUARTERLY_REASSESS_DAYS` old | ⚠️ unit only — not reachable from a fresh e2e signup without an assessment >90 days old | `tests/worker/test_scheduled_handlers.py::test_quarterly_publishes` |
| All 3 scheduled events notify every active member and map to the correct email category (`roadmap_missions`/`roadmap_missions`/`health_assessment`) | ⚠️ unit only (registry-level, all 3 events in one test) | `tests/services/notifications/test_scheduled_events.py::test_scheduled_events_notify_active_members_and_map_categories` |

The ⚠️ rows are genuine gaps in this one live journey (exercising all 18 event types live would need
18 separate trigger actions across nearly every module in the codebase — for the 2 Slice-3 rows,
specifically backdating a milestone or an assessment, which isn't a live HTTP journey at all — judged
not worth the added journey complexity/runtime, or not achievable live, for one e2e run) rather than
unexercised guesses — each is backed by a passing test at the cited path, or, for the two
dependency-shared rows, by that dependency's own coverage elsewhere in the suite.
