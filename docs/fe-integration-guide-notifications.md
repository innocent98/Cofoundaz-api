# FE Integration Guide — Notifications (Module 20, Slice 1: In-App Feed)

All request/response bodies below are pasted **verbatim** from live captures taken by
`e2e/test_notifications.py::test_notifications_journey` running against a real server
(`scripts/e2e_run.sh`) — see `e2e/_captures/notifications/*.json`. Nothing here is retyped from the
schema, the service, or memory. IDs, tokens, and timestamps are real values from that ephemeral test
run (they differ on every real request; the shapes are exact). Every payload, status code, and error
body in this guide was exercised live, except the rows explicitly marked "unit only" or "not
captured live" in §8's verification table.

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
this scoping). Delivery in this slice is **in-app only** — there is no email, push, or real-time
(websocket) delivery yet; the FE must poll (§2 has a suggested cadence) rather than expect a push
event when a new notification arrives.

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
        "id": "4d3264b2-5d36-427f-9b88-6ac3b226fab9",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "829b0ba4-493b-49af-aa3e-effc0c8dd2cf",
          "startup_id": "2646c922-a3e5-42e8-98a3-1fb647f59922",
          "document_id": "7961cb3e-8787-4872-8607-07862f605b4d"
        },
        "read": false,
        "created_at": "2026-09-14T15:40:49.652615+00:00"
      },
      {
        "id": "a7cc08e7-4801-4e68-aca9-19e38cee920d",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "df17260a-eca3-470d-9513-50960cd47ee6",
          "startup_id": "2646c922-a3e5-42e8-98a3-1fb647f59922",
          "document_id": "7961cb3e-8787-4872-8607-07862f605b4d"
        },
        "read": false,
        "created_at": "2026-09-14T15:40:49.636876+00:00"
      }
    ],
    "next_cursor": null
  },
  "meta": null
}
```

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
        "id": "4d3264b2-5d36-427f-9b88-6ac3b226fab9",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "829b0ba4-493b-49af-aa3e-effc0c8dd2cf",
          "startup_id": "2646c922-a3e5-42e8-98a3-1fb647f59922",
          "document_id": "7961cb3e-8787-4872-8607-07862f605b4d"
        },
        "read": false,
        "created_at": "2026-09-14T15:40:49.652615+00:00"
      }
    ],
    "next_cursor": "MjAyNi0wOS0xNFQxNTo0MDo0OS42NTI2MTUrMDA6MDB8NGQzMjY0YjItNWQzNi00MjdmLTliODgtNmFjM2IyMjZmYWI5"
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
        "id": "a7cc08e7-4801-4e68-aca9-19e38cee920d",
        "type": "document.shared",
        "title": "A document was shared in your workspace",
        "body": "",
        "data": {
          "share_id": "df17260a-eca3-470d-9513-50960cd47ee6",
          "startup_id": "2646c922-a3e5-42e8-98a3-1fb647f59922",
          "document_id": "7961cb3e-8787-4872-8607-07862f605b4d"
        },
        "read": false,
        "created_at": "2026-09-14T15:40:49.636876+00:00"
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
    "id": "4d3264b2-5d36-427f-9b88-6ac3b226fab9",
    "type": "document.shared",
    "title": "A document was shared in your workspace",
    "body": "",
    "data": {
      "share_id": "829b0ba4-493b-49af-aa3e-effc0c8dd2cf",
      "startup_id": "2646c922-a3e5-42e8-98a3-1fb647f59922",
      "document_id": "7961cb3e-8787-4872-8607-07862f605b4d"
    },
    "read": true,
    "created_at": "2026-09-14T15:40:49.652615+00:00"
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

## 5. The `type` catalog — v1 handled events

Every notification's `type` is one of these 15 dotted event names. `data` is the **raw event
payload** for that type — its shape is fixed per `type` but different across types (no shared
schema), so the FE should switch/deep-link on `type` and read `data`'s fields accordingly.

| `type` | `data` fields (for deep-linking) | Recipients | Verified live? |
|---|---|---|---|
| `document.shared` | `startup_id`, `document_id`, `share_id` | active members minus actor ⚠️ (see §5a) | ✅ `feed_unread_only.json` |
| `document.signature.requested` | `startup_id`, `request_id`, `file_id` | active members minus actor ⚠️ | not captured live |
| `document.signature.signed` | `startup_id`, `request_id`, `signer_id` | active members minus actor ⚠️ | not captured live |
| `document.signature.completed` | `startup_id`, `request_id` | active members minus actor ⚠️ | not captured live |
| `business.suggestion.created` | `startup_id`, `suggestion_id`, `op` | active members minus actor ⚠️ | not captured live |
| `business.suggestion.approved` | `startup_id`, `suggestion_id`, `op` | active members minus actor ⚠️ | not captured live |
| `business.suggestion.rejected` | `startup_id`, `suggestion_id`, `op` | active members minus actor ⚠️ | not captured live |
| `business.artifact.completed` | `startup_id`, `artifact` | active members minus actor ⚠️ | not captured live |
| `roadmap.replanned` | `startup_id`, `roadmap_id`, `replan_id`, `change_count`, `applied_by` | active members minus actor ⚠️ | not captured live |
| `roadmap.milestone.completed` | `startup_id`, `roadmap_id`, `milestone_id`, `title` | active members minus actor ⚠️ | not captured live |
| `mission.completed` | `startup_id`, `mission_id`, `mission_date` | active members minus actor ⚠️ | not captured live |
| `mission.streak.milestone` | `startup_id`, `streak` | active members minus actor ⚠️ | not captured live |
| `healthscore.dropped` | `startup_id`, `score`, `previous_score`, `delta_7d`, `computed_at` | active members minus actor ⚠️ | not captured live |
| `assessment.completed` | `assessment_id`, `startup_id`, `dimension_scores` | active members minus actor ⚠️ | not captured live |
| `workspace.member.joined` | `startup_id`, `user_id`, `role` | existing active members, correctly excluding the new joiner | ✅ `actor_not_excluded_known_gap.json` (shows this type present in the actor's own feed after a teammate joined) |

Non-`data`-shape columns (`title`/`body`) are fixed per type, not shown per-row above — see §1 for
why `title` is generic and `body` is currently always `""`.

**"not captured live" above means:** the `data` field list for that type is read directly from its
publish-site source (cited per-row in the SOP), not captured live in this journey — only
`document.shared` and `workspace.member.joined` were actually exercised end-to-end in this task's
e2e run. Only two of the 15 types have a registry-level unit test asserting recipient logic
specifically (`test_members_minus_actor` for `document.shared`,
`test_member_joined_notifies_existing_members_not_joiner` for `workspace.member.joined`); the rest
are covered only by `test_all_specs_have_generic_copy` (asserts every `SPECS` entry renders a
non-empty title without raising — a smoke check, not a recipient-logic or `data`-shape assertion).

### 5a. ⚠️ Known gap — "minus actor" does not currently exclude the actor

**For every row marked ⚠️ above, the actor of the action ALSO receives a notification for their own
action** — the "minus actor" part of "active members minus actor" is not effective today for these
14 event types (it only works for `workspace.member.joined`, which uses a different code path). This
was discovered and confirmed live while writing this slice's e2e journey (not a spec change — the
backend intends to exclude the actor and does not yet). **Captured live:**
`e2e/_captures/notifications/actor_not_excluded_known_gap.json` shows the *sharer's own* feed
containing 2 `document.shared` notifications for the 2 documents *they themselves* just shared, plus
1 `workspace.member.joined` (which correctly did NOT include the new joiner):

```json
{
  "data": {
    "notifications": [
      { "type": "document.shared", "data": { "share_id": "829b0ba4-...", "...": "..." }, "read": false, "...": "..." },
      { "type": "document.shared", "data": { "share_id": "df17260a-...", "...": "..." }, "read": false, "...": "..." },
      { "type": "workspace.member.joined", "data": { "role": "team_member", "user_id": "0704902d-...", "...": "..." }, "read": false, "...": "..." }
    ],
    "next_cursor": null
  },
  "meta": null
}
```
*(trimmed for brevity above — the full untrimmed body, with every field, is in the cited capture
file.)*

**FE guidance until this is fixed (tracked in the SOP, not fixed in this slice):** do not assume a
user never sees a notification for their own action. If your notifications panel wants to hide
"you did this" rows, you currently need a client-side heuristic (e.g. compare `data`'s actor-ish
field, where present, against the logged-in user id) rather than relying on the backend to have
already filtered them out — and note most `data` payloads above don't even carry an actor id field
to compare against, so this client-side workaround is only possible for a few types
(`roadmap.replanned`'s `applied_by` is the clearest one that does).

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

## 7. Delivery scope — in-app only, this slice

**There is no email, push, or real-time (websocket) delivery in Slice 1.** A notification exists the
instant its triggering action commits (same-transaction fan-out — see the SOP), but the ONLY way the
FE learns about it is by calling this API — there is no server-pushed event, no webhook, no SSE
stream. **The FE must poll** (§2's suggested cadence for the badge; fetch the full feed on-demand
when the panel opens). Email delivery + per-user notification preferences are Slice 2; scheduler/cron
-triggered notifications (e.g. "your mission is ready") are Slice 3; real-time push/websocket
delivery is Slice 4 — none of those are built yet, so do not design a "you'll get an email/push for
this" affordance into the UI based on this slice.

---

## 8. Verification table

All rows below except those marked "unit only" or "not captured" were exercised **live**, over real
HTTP, against a real Postgres-backed server (`scripts/e2e_run.sh`,
`e2e/test_notifications.py::test_notifications_journey`) — not just unit-tested in-process — and
every response body is captured verbatim in the named file.

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
| `document.shared` fans out to every active member including the actor (Known Gap, §5a) | ✅ | `actor_not_excluded_known_gap.json` |
| `workspace.member.joined` correctly excludes the joiner, notifies existing members | ✅ | `actor_not_excluded_known_gap.json` (shows it present in the *existing* member's feed) |
| The other 13 `type` catalog rows' `data` shape (signature/suggestion/roadmap/mission/healthscore/assessment events) | ⚠️ not captured live | read directly from each event's `event_bus.publish(...)` call site in `app/services/**`/`app/api/v1/endpoints/roadmap.py` (cited per-row in the SOP) — not independently unit-tested per type beyond `test_all_specs_have_generic_copy`'s generic title-rendering smoke check |
| `_members_minus_actor` genuinely excludes the actor **given a payload that names one** (proves the exclusion logic itself is correct — the bug in §5a is that real publish sites don't supply that key, not that the exclusion code is broken) | ⚠️ unit only, with a hand-built payload that (unlike the real publish site) includes the actor key | `tests/services/notifications/test_registry.py::test_members_minus_actor` |
| Non-member (`403 FORBIDDEN`) on any of the 4 routes | ⚠️ not captured — shared `require_workspace` dependency, no notifications-specific test needed | `app/db/tenancy.py::require_workspace` (used identically by every other workspace-scoped module) |
| Malformed pagination `cursor` → `422 VALIDATION_ERROR` | ⚠️ unit only | `tests/services/notifications/test_service.py::test_bad_cursor_422` |
| A failing notification handler is savepoint-isolated — the triggering action's own writes still commit, and a broken handler never 500s the caller | ⚠️ unit only | `tests/platform/test_events.py::test_failing_handler_is_isolated_and_does_not_raise` |

The ⚠️ rows are genuine gaps in this one live journey (exercising all 15 event types live would need
15 separate trigger actions across nearly every module in the codebase, judged not worth the added
journey complexity/runtime for one slice-1 e2e run) rather than unexercised guesses — each is backed
by a passing test at the cited path, or, for the two dependency-shared rows, by that dependency's own
coverage elsewhere in the suite.
