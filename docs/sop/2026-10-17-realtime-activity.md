# SOP — Live Team activity over the existing stream

**What shipped (one line):** New workspace activity is pushed live as `activity.created` on the existing
notifications SSE stream (PRD `workspace.{id}.activity`, decision D-DASH-2), and the stream now closes if the
member is removed. Branch `feat/realtime-activity` → PR to `develop`.

## Why
Dashboard gap #17: Team activity only changed on reload.

## How
| Decision | Why | Rejected |
|---|---|---|
| Reuse the notifications stream: subscribe to `notif:{startup}:{user}` **and** `activity:{startup}` | One connection, existing ticket auth (user's decision) | A second SSE endpoint / WebSocket |
| `write_activity` queues the event in `session.info["pending_activity"]`; published by the existing `after_commit` listener, dropped on `after_rollback` | Never stream something that didn't happen | Publishing inline |
| `serialize_activity` shared by `GET /dashboard/activity` and the event | The FE can prepend the event unchanged | Two shapes |
| Membership re-checked on a timer (every `SSE_HEARTBEAT_INTERVAL`) inside the stream; `revoked` + close when inactive | The stream now carries workspace data, so a removed member must stop receiving it (commit security review) | Ticket-time check only |

## What's involved
`app/platform/activity.py` (`serialize_activity`, queue), `app/platform/realtime.py` (`activity_channel`,
`publish_activity`, multi-channel `subscription`), `app/db/session.py` (listeners), `app/api/v1/endpoints/notifications.py`
(two channels, `revoked`), `app/services/dashboard/service.py` (shared serializer). Tests:
`tests/platform/test_activity_realtime.py` (new), `tests/api/notifications/test_stream.py`. Guide:
`docs/fe-integration-guide-realtime-activity.md`. No migration.

## Verification
Fresh-DB pytest + gates + e2e on the PR tree (see the PR). Live: the founder's open stream received
`activity.created` about 1s after a mission task was added.

## Operate / roll back
Needs Redis (already required by the stream). Revert the PR to roll back. Clients just stop getting the new event.

## Follow-ups
- A membership-removal endpoint (Team page) could also publish an immediate revocation instead of waiting for the next check.
