# FE Integration Guide — Notifications, Real-Time SSE Delivery (Module 20, Slice 4)

This guide covers **only** the new real-time delivery mechanism added in Slice 4. For everything
else about notifications — the feed, pagination, mark-read, the `type` catalog, email preferences,
and scheduled/time-based events — see
[`docs/fe-integration-guide-notifications.md`](./fe-integration-guide-notifications.md) (Slices
1–3), which this guide cross-links back to and does not repeat.

Both payloads below are pasted **verbatim** from a live capture taken by
`e2e/test_notifications_realtime.py::test_realtime_notification_delivery` running against a real
server (`scripts/e2e_run.sh`) — see `e2e/_captures/notifications_realtime/*.json`. Nothing here is
retyped from the schema, the endpoint code, or memory. The ticket value and the notification `id`/
`created_at` are real values from that ephemeral test run (they differ on every real request; the
shapes are exact).

---

## 0. What this is, in FE terms

Before Slice 4, the only way to learn a new notification exists was polling `GET /notifications` /
`GET /notifications/unread-count` (Slice 1's guide §2). Slice 4 adds a **push** channel on top of
that — the bell/feed can now update the instant a notification is created, with no poll interval to
tune. **It does not replace the feed API.** `GET /notifications` is still the only way to actually
*read* notification content or history; the SSE stream is a live, best-effort nudge, not a data
source in its own right. Treat it exactly like a "something changed, go re-check" signal for the
`notification.created` case, and as the authoritative unread count only for its own first frame
(§2).

**When to open the stream:** while the app/tab is open and the user is authenticated into a
workspace — same lifetime as you'd hold any other long-lived connection. There is still no delivery
at all while the app is fully closed (device/web push is a separate, unbuilt slice — see the SOP's
Follow-ups).

---

## 1. Connect flow

Two calls, in order:

### 1.1 `POST /api/v1/notifications/stream-ticket`

Same auth as every other notifications route — Bearer access token + `X-Workspace-Id` header. No
request body.

**Response — 200** (`e2e/_captures/notifications_realtime/stream_ticket.json`):
```json
{
  "data": {
    "ticket": "ulauvxzhxtVhm7MCycHjflYdmXGiGh_Mj0TIUh1-1eo"
  },
  "meta": null
}
```

The ticket is a random, **one-time**, short-lived token (`SSE_TICKET_TTL`, default 30 seconds — see
the SOP's "Operate" section for why). It is NOT your access token and carries no other claim beyond
"this ticket redeems to your `(user_id, startup_id)`, once." **Why a separate ticket at all:**
native browser `EventSource` cannot set an `Authorization` header, and putting the real access token
in a URL (query string) leaks it into proxy/access logs, browser history, and `Referer` headers.
The ticket keeps that exposure to a single-use, half-minute-lived opaque string instead.

### 1.2 Open the stream

```js
const ticketResp = await fetch("/api/v1/notifications/stream-ticket", {
  method: "POST",
  headers: { Authorization: `Bearer ${accessToken}`, "X-Workspace-Id": workspaceId },
});
const { data } = await ticketResp.json();
const es = new EventSource(`/api/v1/notifications/stream?ticket=${data.ticket}`);
```

`GET /api/v1/notifications/stream?ticket=...` takes **no other auth** — the ticket alone
authenticates and scopes the connection (it was minted for a specific `(user_id, startup_id)` pair
by step 1.1, and the ticket is consumed — one-time — the moment this request is made). A second
attempt to use the same ticket value fails.

**Errors on connect** (both use the standard error envelope, same as every other route):

| Status | Code | When |
|---|---|---|
| 401 | `INVALID_TICKET` | Ticket missing, expired (>30s unused), or already consumed by an earlier connect attempt |
| 403 | `FORBIDDEN` | Ticket is valid, but the caller no longer has an active membership for that workspace (e.g. removed from the team in the gap between minting and connecting) |

Neither was captured live in this task's e2e run (the journey only exercises the success path) —
both reuse the exact same `AppError`/envelope convention as every other notifications route, so
treat them like any other 401/403 you already handle elsewhere in the app (e.g. redirect to
login / drop back to a workspace picker).

---

## 2. The two event types

`EventSource` fires named events — use `addEventListener`, not the generic `onmessage` (which only
catches unnamed `message` events and would silently miss both of these):

```js
es.addEventListener("unread", (e) => {
  const { unread } = JSON.parse(e.data);
  setBellCount(unread); // authoritative — replace, don't add to, whatever the badge currently shows
});

es.addEventListener("notification.created", (e) => {
  const notification = JSON.parse(e.data);
  prependToFeed(notification); // same shape as a GET /notifications row — render with the same component
});
```

### 2.1 `event: unread` — sent once, first, authoritative

The very first frame on every connection (and every reconnection) is an `unread` event carrying the
**current, authoritative** unread count — computed fresh from the database at connect time, not a
running total you'd need to reconcile against a prior value:

```
event: unread
data: {"unread": 3}
```

Use this to (re)set the bell badge the instant the connection opens, independent of anything the
FE may have cached from a previous poll. It fires exactly once per connection, not on every new
notification — after this, watch `notification.created` (below) and increment the badge yourself,
or just re-fetch `GET /notifications/unread-count` if you'd rather not track it client-side.

### 2.2 `event: notification.created` — one per live notification

Fires once per notification, as they're created, for as long as the connection stays open:

```
event: notification.created
data: {<a full feed-item object>}
```

**Captured live** (`e2e/_captures/notifications_realtime/stream_frame.json` — a real frame received
over the open SSE connection):
```json
{
  "id": "4b5277e8-590e-463d-a5ce-5d1162e3807f",
  "type": "x.realtime.test",
  "title": "Live!",
  "body": "",
  "data": {
    "hello": "world"
  },
  "read": false,
  "created_at": "2026-09-19T11:20:05.249232+00:00"
}
```
(`type: "x.realtime.test"` is a throwaway type this e2e test invented to prove delivery end to end —
it is not a real product event. A production notification here would carry any of the real `type`
values from the main FE guide's §5 catalog, e.g. `document.shared`, `mission.ready`, etc.)

**This object is byte-identical in shape to a row from `GET /notifications`** (both are produced by
the same `serialize_notification` function server-side) — `id`, `type`, `title`, `body`, `data`,
`read`, `created_at`, nothing more, nothing renamed. **Render it with the exact same
component/mapping you already use for a feed row** — there is no separate "live notification" shape
to build a second renderer for. `read` will always be `false` on arrival (it was just created); the
main guide's §5 `type`→`data`-fields catalog applies unchanged for deciding what to deep-link to.

**Heartbeats** (not a named event — `EventSource` ignores SSE comment lines automatically, nothing
for the FE to handle): every `SSE_HEARTBEAT_INTERVAL` seconds (default 20) with no real traffic, the
server sends a `: heartbeat\n\n` comment purely to keep intermediary proxies from treating the
connection as idle and closing it. You will never see this in `addEventListener` callbacks or
`onmessage` — it's invisible at the `EventSource` API level by design.

---

## 3. Reconnect rules — read this before wiring retry logic

**`EventSource` auto-reconnects on its own** (built into the browser API — a dropped connection
retries with a backoff the browser manages) — you do not need to write reconnect logic for the
"connection dropped, try again" case. But **the ticket you used is now gone** (one-time, consumed
by the connection you just lost) and a bare reconnect to the same URL will fail with `401
INVALID_TICKET`. You must:

1. **Mint a fresh ticket per (re)connect.** On `es.onerror` (or before constructing a new
   `EventSource` at all, e.g. after your own app-level "reconnect" trigger), call
   `POST /notifications/stream-ticket` again and construct a new `EventSource` against the new
   ticket. Do not try to reuse a ticket across connections, ever — it was designed to be single-use.
2. **Re-fetch `GET /notifications` (or at least `unread-count`) on every (re)connect to reconcile.**
   There is no gap-filling on the server side — no `Last-Event-ID` support, no per-user event log
   (see the SOP's Follow-ups). Anything that happened while you were disconnected (network blip, tab
   backgrounded and throttled, ticket expired before the client got around to reconnecting) is
   **not replayed**. The `unread` frame on the new connection gives you a correct *count*, but not
   the individual rows — if your UI shows a list, not just a badge, re-fetch the feed itself.
3. **The SSE stream is live-only; the feed (`GET /notifications`) is always the source of truth.**
   Never build a UI that trusts SSE as the sole way data ever entered the client — every piece of
   state the stream can give you is also derivable (just less instantly) from the polling endpoints
   the main FE guide already documents. If Redis has a bad day (see below), the app should degrade
   to exactly the pre-Slice-4 polling experience, not break.

**What "Redis unavailable" looks like to the FE:** notification creation (the in-app row itself, via
`GET /notifications`) is completely unaffected — publish is fail-soft server-side (SOP "How"). An
already-open SSE connection simply stops receiving `notification.created` frames (still gets
heartbeats, or the connection may drop and fail to reconnect depending on what broke). There is no
special error event for this case to listen for; treat "stream open but nothing arriving for an
unusually long time" the same as "SSE isn't working right now" and fall back to your existing poll
cadence rather than trying to detect it precisely.

---

## 4. UX consequences worth designing around

- **Don't build a UI that assumes SSE delivery is guaranteed or ordered-with-respect-to-email.**
  It's a best-effort push on top of an already-durable system (the DB row + Slice 2's email job are
  the real delivery guarantees); SSE can silently miss a beat (client offline, Redis blip, ticket
  race) with no error surfaced to the FE beyond "nothing arrived."
- **One ticket = one connection.** Don't try to share a single minted ticket across multiple tabs or
  reuse it after a disconnect — mint one per `EventSource` you actually open.
- **The bell badge and the feed can now be event-driven instead of interval-polled** — you may
  choose to drop or lengthen the Slice-1-suggested 30–60s unread-count poll once SSE is wired up,
  but keep it as a fallback (e.g. poll on window-focus, or at a much longer interval) for whenever
  the stream isn't connected (page load before the ticket round-trip completes, browsers/extensions
  that block `EventSource`, the Redis-degraded case above).
- **This is the SAME notification a poll would eventually show you** — there is no "SSE-only"
  notification type and no reason to treat a pushed row as more or less trustworthy than one you
  fetched yourself.

---

## 5. Verification table

| Behaviour | Verified live? | Source |
|---|---|---|
| `POST /notifications/stream-ticket` — 200, `{ticket: <string>}` | ✅ | `stream_ticket.json` |
| `GET /notifications/stream?ticket=...` — 200, `text/event-stream`, opens successfully with a valid ticket | ✅ | asserted in `test_realtime_notification_delivery` (status-code check before reading frames) |
| Initial `event: unread` frame sent first, before any `notification.created` frame | ✅ | `test_realtime_notification_delivery` reads past this frame before triggering the notification |
| `event: notification.created` — a real notification, created and committed in a SEPARATE OS process from the one holding the stream, is delivered over the open connection via the Redis backplane | ✅ (the whole point of this task) | `stream_frame.json` |
| `notification.created` payload is byte-identical in shape to a `GET /notifications` row (`serialize_notification`) | ✅ | same source code path asserted by inspection (`app/services/notifications/service.py::serialize_notification`) used by both `_event_stream` and `list_notifications_endpoint`; the captured frame's keys match §1's feed-row shape in the main FE guide |
| Heartbeat comments keep an idle connection alive | ⚠️ unit only | `tests/api/notifications/test_stream.py::test_event_stream_emits_unread_then_notification` (fake pubsub, drives a timeout → heartbeat directly; not exercised over the full 20s live in e2e, which would make the journey needlessly slow) |
| Bad/expired ticket → `401 INVALID_TICKET` | ⚠️ unit only | `tests/api/notifications/test_stream.py::test_stream_rejects_bad_ticket` |
| Non-member (revoked membership) → `403 FORBIDDEN` | ⚠️ not captured live or unit-isolated as its own test — re-checked via the same `_active_membership` helper the endpoint always calls; no dedicated test constructs the specific "ticket valid, membership since revoked" race | `app/api/v1/endpoints/notifications.py::stream_endpoint` (code path, not test-proven in isolation) |
| Publish is fail-soft — a Redis publish failure doesn't break notification creation or the triggering request | ⚠️ unit only | `tests/platform/test_realtime.py` (fake Redis raising, asserts no exception propagates) |
| A rolled-back transaction publishes no SSE event (no phantom) | ⚠️ unit only | `tests/services/notifications/test_realtime_publish.py::test_no_publish_on_rollback` |
| Reconnect-mints-a-new-ticket / reconcile-via-feed contract (§3) | ⚠️ design contract, not independently live-tested — this task's e2e opens exactly one connection with exactly one ticket, once | design doc `docs/superpowers/specs/2026-09-19-notifications-realtime-sse-design.md`, "Reconciliation, not replay" |

The ⚠️ rows are genuine, honestly-labelled gaps in this one live journey (each is backed by a
passing unit test at the cited path, or is a direct read of the shipped code, not an invented
guess) — proving every combination live (a real Redis outage mid-stream, a real 20-second heartbeat
wait, a real membership revocation raced against an open ticket) was judged not worth the added
e2e runtime/complexity for a single task, same standard the main FE guide's own §8 applies to its
own ⚠️ rows.

---

## See also

- [`docs/fe-integration-guide-notifications.md`](./fe-integration-guide-notifications.md) — the
  feed, pagination, mark-read, the full `type` catalog, email preferences, and scheduled/time-based
  events (Slices 1–3). §7 "Delivery scope" there now points back to this guide.
- `docs/sop/2026-09-19-notifications-realtime-sse.md` — the engineering record of how this was
  built (architecture, config, rollback, follow-ups).
