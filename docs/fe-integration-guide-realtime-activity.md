# FE Integration Guide — Live Team activity (`activity.created` on the notifications stream)

**Provenance.** Live capture on the local docker stack, 2026-10-17: the local test founder opened the stream,
then added a mission task from another request. The raw SSE text is verbatim in
`e2e/_captures/realtime_activity_live/stream_activity_created.txt`. Revocation and best-effort publishing are
unit-tested (`tests/api/notifications/test_stream.py`, `tests/platform/test_activity_realtime.py`).

## 1. What it is, in FE terms
The dashboard's **Team activity** card (PRD 02.1, realtime channel `workspace.{id}.activity`) should update
live. There is **no new connection**: the existing notifications stream (`fe-integration-guide-notifications-realtime.md`)
now also carries the workspace's activity. If you already hold the stream open for the bell, just handle one more
event type.

## 2. Connect (unchanged)
`POST /api/v1/notifications/stream-ticket` (Bearer + `X-Workspace-Id`) → `{"data": {"ticket": "…"}}`, then
`GET /api/v1/notifications/stream?ticket=…` as an `EventSource`.

## 3. Events on the stream
| `event:` | `data` | Action |
|---|---|---|
| `unread` | `{"unread": n}` | (existing) bell count |
| `notification.created` | a notification | (existing) bell |
| **`activity.created`** | **one activity item, the exact shape of `GET /dashboard/activity` `data.items[]`** | prepend it to Team activity (dedupe by `id`) |
| **`revoked`** | `{"reason": "membership_inactive"}` | the stream is closing because this user is no longer an active member. Don't reconnect. Send the user to their workspaces / sign-in |
| `: heartbeat` (comment) | — | ignore |

Live capture (verbatim):
```
event: unread
data: {"unread": 7}

: heartbeat

: heartbeat

event: activity.created
data: {"id": "77551c2e-7ac1-4138-8aa6-da9ea674389d", "action": "mission.task.added", "entity_type": "mission_task", "entity_id": "33cc724c-fbb9-4530-9451-031958871c2c", "summary": "Victor Founder added 'Realtime check task' to today's mission", "meta": null, "actor": {"id": "0a37c892-5def-4fef-af27-a822588d4171", "name": "Victor Founder"}, "created_at": "2026-10-17T22:39:04.991361+00:00"}
```

## 4. Semantics and traps
- Every active member's open stream gets every new activity in the workspace, including their **own** actions.
  Dedupe by `id` if you also add optimistic rows.
- Events are sent **after commit** only (a rolled-back action never appears), and best-effort: if Redis hiccups
  an event can be missed. Refetch `GET /dashboard/activity` on reconnect and when the tab regains focus.
- Activity written by background jobs (the worker) is streamed too.
- `actor` can be `null` (system actions). `actor.name` can be `null` (a member without a profile name). Fall back to
  "A teammate".

## 5. Verification
| Behaviour | Live? | Source |
|---|---|---|
| `activity.created` arrives on an open stream with the feed's item shape | ✅ | `stream_activity_created.txt` |
| Not sent on rollback; sent only after commit | ❌ unit | `test_after_rollback_drops_activity_without_publishing`, `test_after_commit_publishes_activity_and_clears` |
| `revoked` + close when membership is gone | ❌ unit | `test_event_stream_closes_when_membership_is_revoked` |
| Redis failure doesn't break the request | ❌ unit | `test_publish_activity_is_best_effort` |
