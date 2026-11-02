# Admin Tickets: founder Help intake and staff Support

**What shipped:** support tickets end to end on the API. Founders open, list, read and reply to tickets (the Settings > Help entry, P4); support staff work a queue with tabs, filters, SLA chips, assign, priority, internal notes, canned replies, escalate, resolve and reopen; the dashboard "Needs attention" count (A9). Branch `feature/admin-tickets`, migration `0087_support_tickets` (on `0086_admin_danger_zone`). Gap rows A16, A17, A18, A9, A3 (per-page search, P13), decision P4. PR: see the checklist entry.

**Why:** the Support tab was a mock and there was no way for a founder to reach support, so the queue would have been empty (A18/P4).

**How, and the calls made**

| Call | Decision |
|---|---|
| T1 | SLA = first staff reply deadline by priority: P1 1h, P2 4h, P3 24h, P4 72h. States `ok`, `due_soon` (<= 1h left), `breached`, then `met` / `late` once answered. Founders cannot set priority; billing and account default to P2, the rest P3. |
| T2 | No AI triage in v1. |
| T3 | Scope `support` (and Super Admin) sees every ticket and may assign to any active support staff. No step-up TOTP (staff already signed in with MFA; actions are reversible). Reason (10+) required on escalate and optional on reopen. Replying to or resolving an unassigned ticket assigns it to the actor. |
| T4 | No attachments (storage and scanning are not cheap or safe yet). Text only, 5000 chars. |
| T5 | Redis counters: 5 tickets/hour, 20/day, 30 founder replies/hour per user (429 `RATE_LIMITED`). Not slowapi, so it is testable and per user. |
| T6 | A member sees tickets they opened; the workspace's founder role sees all of its workspace's tickets; anything else is 404. |
| T7 | A founder reply on a resolved ticket reopens it within 14 days and restarts the clock; after that 409 `TICKET_CLOSED`. |
| T8 | Escalate = P1 + `escalated` + staff-only note + audit with reason. Resolve emails the founder (optional closing message). |

Design points: internal notes live in their own table (`support_ticket_notes`) so a founder query cannot return one by missing a filter; founder serializers never read staff identities (authors show as "Cofoundaz Support"); every mutation locks the ticket row, writes one audit row (ids and field changes, never message text) in the same commit, and mails only after the commit; any mail error is logged, never raised (the change is already committed). Message timestamps are set from Python time because `now()` is the transaction start and would tie. The dashboard count is a new route (`/admin/dashboard/attention`) so the KPI payload and its guide stay unchanged. Rejected: slowapi limits (not per-purpose, hard to test), a single messages table with an `internal` flag (one missed filter leaks notes), step-up on every action (friction for a reversible action).

**What's involved**
- `alembic/versions/0087_support_tickets.py` (creates `support_ticket_no_seq` explicitly: `create_table` does not create a sequence on a non-key column; found by the live e2e), `app/db/models/support.py`, enums `TicketStatus/Priority/Category`.
- `app/services/support/{core,founder,emails}.py`, `app/services/staff/support.py`, `app/schemas/support.py`.
- Routes: `app/api/v1/endpoints/support.py` (`/support/tickets...`), `app/api/v1/endpoints/admin.py` (`/admin/support/...`, `/admin/dashboard/attention`), `app/api/v1/api.py`.
- Email links point at `{APP_BASE_URL}/settings/help/{ticket_id}` (the FE must serve it).
- Audit actions: `ticket.assigned|replied|note_added|priority_changed|escalated|resolved|reopened`, `canned.created|updated|deleted`.

**Verification**
- `tests/api/test_staff_support.py` (45): founder validation, rate limit, IDOR (other workspace, teammate vs workspace founder), notes and staff identity never in founder responses, the guard matrix for all 15 staff routes (non-staff 404, no-MFA 404, wrong scope 403), queue filters and search, SLA states, every action with audit rows, email content and escaping, mail failure, left-workspace creator, reopen window.
- `e2e/test_admin_tickets.py`: live journey (founder opens, staff note, reply with email, founder replies, escalate, resolve with email, founder reopens, staff reopen, audit). Captures in `e2e/_captures/admin_tickets/`; guide `docs/fe-integration-guide-admin-tickets.md`.
- Migration: upgrade, downgrade, upgrade and `alembic check` clean on a scratch DB.
- One Opus review: one Critical (the sequence was missing in migrated databases; fixed, and it also failed the live e2e), three Important (email to a creator who left the workspace; priority change on a reopened ticket breached the SLA; non-delivery mail errors became 500s), one Minor (rate-limit key without expiry). All fixed with tests.

**Operate / roll back**
- Nothing to configure; mail uses the existing sender. Roll back: revert the PR and `alembic downgrade 0086_admin_danger_zone` (drops the four tables and the sequence; all tickets are lost).
- Grant `support` scope through the existing staff invite.

**Follow-ups**
- FE: Settings > Help and the Support tab (brief in the FE repo, `docs/fe-handoff/admin-support-tickets.md`).
- Attachments, AI triage, an email-in channel, SLA on resolution time, a staff email to the assignee on a founder reply, and a "de-escalate" route are not built.
- The queue is sorted by latest activity; a priority/SLA-first sort would need a compound cursor.
