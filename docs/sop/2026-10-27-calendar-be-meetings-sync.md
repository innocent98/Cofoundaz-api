# SOP — Calendar BE: feed, meetings, reminders, Google sync

**What shipped** — the rest of PRD Module 19 on the API: a merged **feed** (`GET /calendar/feed`), **meetings** (attendees,
agenda, invite email + `.ics`, invited-only access for non-founder roles, post-meeting AI summary and "Add all to tasks"),
editable **reminder rules** with the PRD defaults and the notifications they fire, and **Google Calendar sync** (per-user
OAuth, encrypted tokens, pull + push, disconnect with revoke, a worker job every 15 minutes). Migration
`0079_calendar_meetings_sync`. Branch `feature/calendar-be` → `develop`. Gap list: `cofoundaz/docs/checklist/calendar-prd-gaps.md`
(rows B1 to B13). FE guide: `docs/fe-integration-guide-calendar-v2.md` (live-captured; Google itself is a fake in it, see its
verification table).

## Why

The Calendar API was events CRUD plus a milestone projection, and the FE calls none of it (all three pages are static mock).
The PRD asks for five source layers in one view, meetings with invites and a post-meeting summary, per-type reminders, and
two-way Google sync.

## How — decisions and rejected alternatives

| Topic | Decision |
|---|---|
| **Meeting assistant (D-CAL-1)** | No meeting bot. The user pastes notes or a transcript; a worker job (`ai.calendar.meeting_summary`) returns summary + decisions + action items (strict JSON schema, budget-metered, a failed run can be retried). Rejected: a bot vendor (paid per minute, consent and storage issues). |
| **What is pushed (D-CAL-2)** | Only `calendar_events` the user created or is invited to. Roadmap milestones, marketing posts and compliance dates are *not* pushed. Pulled Google events are private to the connecting user. |
| **Busy blocks** | Teammates get `layer: "busy"`, `title: "Busy"`, `ref: null`. The title is removed in the feed query (`feed._external`), not hidden by the client. |
| **Connection is per user** | `calendar_connections` unique `(user_id, provider)`. A workspace-level connection would expose one person's private calendar to the team. |
| **OAuth state** | A signed JWT (`typ=calendar_oauth`, 10 min) **and** a single-use nonce stored on a `pending` connection row. The callback is public (Google redirects the browser), so identity comes only from the state. The nonce is cleared before the code exchange, so a replay, a forged state, or a state for a user with no pending row all land on `?error=invalid_state`. The membership is re-checked at callback time. |
| **Token storage** | Fernet via the Finance helper (`FINANCE_CONNECTIONS_ENCRYPTION_KEY`, one provider-secret key per environment). Access + refresh tokens are ciphertext; nothing in any response or `last_sync_summary` carries a token, ciphertext, nonce or sync token. |
| **Refresh and dead grants** | Access token refreshed under the row lock 60 s before expiry. A rejected refresh (`invalid_grant`, consent withdrawn, or the 7-day expiry of an app in Testing) flags `needs_attention` + a "reconnect" message and the scheduler stops syncing it. Provider outages only set `last_error`. A sync never raises into the worker (no retry storm on a dead grant). |
| **Sync mechanics** | Pull: `syncToken` incremental, 410 → one full resync; a full listing deletes rows Google no longer has. Our own pushed events carry `extendedProperties.private.cofoundazEventId` and are skipped on pull. Push: body hash per `(event, connection)`; edit → `PATCH`, delete → a `calendar.sync.push_delete` job (the link row dies with the event, so the external id travels in the job). Google is always called with `sendUpdates=none` (D-CAL-4). |
| **Invites** | Cofoundaz emails external attendees `{Founder} invited you: {title} · {date}` with an `.ics` (UTC times, stable UID, SEQUENCE bumped on edits). Internal members get an in-app notification, never mail. Header injection: CR/LF stripped from every ICS property; HTML escaped in mail bodies. |
| **Reminders** | Rules per workspace (`milestone` 7d+1d, `compliance` 14d+7d+1d, `meeting` 15 min; `deadline` events use the compliance rule; `task` has none). The scheduler scans events and roadmap milestones, claims one `ScheduledRun` per `(item, offset)`, never announces more than 12 h late. Private events remind only their creator and invitees. The existing `roadmap.milestone.due_soon` job (2 days) keeps running. |
| **Access (D-CAL-6)** | Founder + team member manage. Mentor, accountant, legal advisor, consultant, investor reach only `GET /meetings[/{id}]` for meetings they are invited to (by user id or email), read-only, without the transcript. Everything else is a uniform 404. |
| **Visibility** | `team` (default) or `private` (creator + invitees) on every event and meeting, enforced in list, get, patch, delete, feed and reminders. |
| **Time model (D-CAL-5)** | Per-event IANA `timezone` (default `Africa/Lagos`), naive wall-clock times kept; ICS/Google conversions use the zone. `end_date` adds multi-day events. |
| **Outlook** | Paths reserved (`/sync/outlook/...` → 501 `FEATURE_NOT_ENABLED`); nothing built (decision 20). |

## What's involved

| Area | Where |
|---|---|
| Migration | `alembic/versions/0079_calendar_meetings_sync.py` (new columns on `calendar_events`; tables `calendar_attendees`, `calendar_meetings`, `calendar_reminder_rules`, `calendar_connections`, `calendar_external_events`, `calendar_event_links`) |
| Models / schemas | `app/db/models/calendar.py`, `app/schemas/calendar.py` |
| Services | `app/services/calendar/{google,stub_transport,crypto,connections,sync,feed,meetings,invites,ics,assistant,reminders}.py` |
| Routes | `app/api/v1/endpoints/calendar.py` (31 routes under `/api/v1/calendar`) |
| Worker | `app/worker/handlers/calendar.py` (`calendar.sync`, `calendar.sync.push_delete`, `calendar.invites`, `ai.calendar.meeting_summary`, `scheduled.calendar.reminder`); scheduler `_due_calendar_syncs`, `_due_calendar_reminders` |
| Notifications | `calendar.reminder`, `calendar.meeting.invited`, `calendar.meeting.summary_ready` (in-app only) in `notifications/registry.py` |
| Config | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FINANCE_CONNECTIONS_ENCRYPTION_KEY` (existing), `API_PUBLIC_URL`, `APP_BASE_URL`, `CALENDAR_SYNC_INTERVAL_MINUTES` (15), `CALENDAR_PROVIDER_STUB` (dev/e2e only, ignored in production) |

## Verification

Unit/API: `tests/api/test_calendar_sync.py` (32), `test_calendar_meetings.py` (26), `test_calendar_feed_reminders.py` (23), plus the
existing calendar and notification suites. Live e2e `e2e/test_calendar_meetings_sync.py` against a real uvicorn + Postgres + Redis,
with the Google fake in both processes; captures in `e2e/_captures/calendar_v2/`. **Not verified:** the real Google consent screen,
real token refresh and real Calendar API responses (headless; no real credentials in tests). The first real connect needs a Google
test user.

## Operate / roll back

- Required env per environment: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FINANCE_CONNECTIONS_ENCRYPTION_KEY`, `API_PUBLIC_URL`
  (the callback URL is built from it and must match the redirect URIs registered in Google: `https://api.cofoundaz.com/api/v1/calendar/sync/google/callback`,
  the staging host, and `http://localhost:8000`). Without the first three, connect answers 503 `CALENDAR_SYNC_NOT_CONFIGURED` and
  everything else keeps working.
- Google OAuth consent screen in "Testing": only listed test users connect, refresh tokens die after 7 days (D-CAL-3).
- The worker must be restarted after deploy (it does not hot-reload).
- Roll back: `alembic downgrade 0076_documents_signer_link_audit` drops the new tables and columns; connections and imported events are lost.

## Follow-ups

- Real Google round trip with a test user (Adebayo), then Google verification (D-CAL-3).
- Compliance layer data once Legal ships; Microsoft/Outlook sync.
- A meeting-bot vendor if the paste-notes flow is not enough.
- Dashboard "Upcoming" and global search still read their own sources (gap B14).
