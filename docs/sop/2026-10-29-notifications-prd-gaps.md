# SOP: Notifications PRD gaps (BE)

**What shipped:** the API behind the whole Notifications page as PRD'd and designed: server-side inbox filters and counts, `priority` and
`module` on every row, archive / restore / archived list, a per-category Preferences grid (In app + Email, critical alerts locked on), working
digest emails (daily / weekly) and quiet hours. Migration `0081_notifications_gaps`. Branch `feature/notifications-be-gaps`. Closes N1 to N8
in `cofoundaz/docs/checklist/notifications-prd-gaps.md`. FE guide: `docs/fe-integration-guide-notifications-inbox.md` (live-captured).

## Why

The page filtered the 50 rows it had loaded (so "Urgent" was always empty: the client never produced that category), "dismiss" only removed a
row from local state (it came back on reload), the preferences covered 5 email-only switches, and digest mode and quiet hours (stored by the
Settings pass, PR #195) did nothing at all. PRD 20.1 to 20.4 call for archive/restore, chips, a channel grid with locked critical alerts, and a
digest email.

## How: decisions and rejected alternatives

| Topic | Decision |
|---|---|
| **Critical set (D-NOT-1)** | `categories.CRITICAL_TYPES`: `billing.payment.failed`, `finance.runway.low`, `finance.invoice.overdue`, `document.signature.requested`, `sales.sending.paused`. They are always delivered in-app and by email, ignoring master switch, category switch, digest and quiet hours. `roadmap.milestone.overdue` is deliberately NOT critical: its recipients are every member, and a critical alert ignores opt-outs (Opus review). This is the PRD's locked-ON list; it is a constant, not a column, so changing it is a one-line edit. |
| **Categories (D-NOT-2)** | 5 rows became 12 (finance, marketing, sales, validation, academy, ai_insights, billing added). New rows email **OFF by default** (billing ON, it is account mail) so nobody starts receiving new kinds of email unasked; In app is ON everywhere. Events are classified by exact map, then by prefix, so Billing's events (PR #196) land in the right row without editing this file. Journal nudges have no row (in-app only, own switches). |
| **Two switches** | `categories` stays the EMAIL switch per category (backward compatible); the new `in_app` is a second JSON map. Rejected: a per-type matrix (PRD says rows grouped by module; ~45 types is noise). |
| **In-app off** | The two switches are independent: with In app off the row is stored `hidden` (when email or the digest still wants it; no row at all when both are off) and every inbox read path (list, counts, unread, read-all, archive, bell, realtime) skips it. Critical types ignore it. Found by the Opus review: the first version skipped creating the row, which silently killed that row's email too. |
| **Email routing** | One function, `preferences.email_route`: critical -> now; else needs master + category switch; quiet hours -> skip; digest user -> digest; else now. |
| **Quiet hours (D-NOT-5)** | Non-critical **email is skipped** during the window (not deferred: jobs have no run-at; the items remain in-app and in the next digest). In-app unaffected. The PRD's "Push pauses" does not apply: there is no push. |
| **Digest** | `scheduled.notifications.digest`: once per (user, workspace) per local day at `digest_time` (weekly: on `digest_day`), claimed in `scheduled_runs` (scope = user hex + workspace hex, 64 chars: the column limit). The handler re-reads preferences, gathers non-critical, non-archived, email-enabled rows since `last_digest_at` (window capped at 2 periods) and sends one email; an empty digest is not sent but the cursor still moves. `last_digest_at` is on the preferences row. |
| **Archive** | `archived_at` on `notifications`; archive also sets `read_at`. Archived rows are excluded from list, unread count, read-all and counts. |
| **Priority/module** | Derived from the type (no column), serialised on every row and on the realtime payload. |
| **Push, Announcements (D-NOT-3/4)** | Push channel not built (no device tokens): no column. The Announcements tab has no PRD section and no API: it is removed from the page, not faked. |

## What's involved

`app/services/notifications/{categories,preferences,service,registry,digest}.py`, `app/api/v1/endpoints/notifications.py` (+ `counts`, `archive`,
`restore`), `app/schemas/notification.py`, `app/db/models/{notification,notification_preference}.py`, `app/worker/{scheduler,handlers/scheduled,handlers/email}.py`,
migration `0081` (chained after Journal `0080_journal_gaps`). Tests:
`tests/api/test_notifications_gaps.py` (17) plus updated `test_preferences.py`, `test_learning_notifications.py`, `test_sales_notifications.py`
(the "in-app only, no category" assertions became "opt-in category, email default off"). Live: `e2e/test_notifications_gaps.py`,
captures in `e2e/_captures/notifications_gaps/`.

## Verification

Unit: notifications, preferences, scheduler, worker and email suites green on fresh scratch Postgres DBs; black, isort, ruff, mypy clean; migration
round trip and `alembic check` clean. Live: the new journey plus the existing `test_notifications.py` and `test_notifications_email.py` passed against a real
server, including a digest email built by the scheduler and sent by the worker. Not live: quiet-hours suppression, weekly weekday, empty digest (unit tests).

## Operate / roll back

No new env vars. The digest uses the existing email backend. `alembic downgrade 0080_journal_gaps` drops the three columns (revert the code
first). **Behaviour change to expect:** critical alerts now email (they were in-app only before), and the five original categories keep their email
defaults. If a critical type proves noisy, remove it from `CRITICAL_TYPES`.

## Follow-ups

Push channel and device tokens; announcements (needs an owner and a product call); per-type muting; deferring quiet-hour email instead of skipping;
`GET /notifications` `module=` filter if chips by module return; the FE-side hrefs per type could move server side.
