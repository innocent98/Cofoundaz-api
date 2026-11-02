# SOP: Finance invoice automation (PDF, overdue, reminders, paid and runway events)

| | |
|---|---|
| **What shipped** | Finance BE slice (b): gap rows 8, 9, 10, 24 of `cofoundaz/docs/checklist/finance-prd-gaps.md`. Invoice PDF (download and email attachment), a daily overdue scan with one-shot overdue notification and automatic client reminders, a manual remind endpoint, `finance.invoice.paid` and `finance.runway.updated` events with notifications, NGN-only invoices. Branch `feature/finance-invoice-automation`, migration `0066_finance_invoice_automation` (stacked on `0065_finance_payments_core`). Commits `7f02511c` (plan), `93c48e6d` (PDF, attachments, columns), `1ef060e4` (overdue and reminders), `da16f492` (events and notifications), plus the docs/e2e commit. PR: pending, see the checklist entry. |
| **Why** | Overdue was only a derived badge: nothing told the team an invoice was late, `auto_remind` was a stored flag that did nothing, "Download PDF" had no server side, and paying an invoice or crossing a runway threshold raised no event. |

## How (decisions)
| Decision | Why | Rejected |
|---|---|---|
| Dependency-free PDF writer (`invoice_pdf.py`): PDF 1.4, Helvetica, Latin-1, uncompressed text, paginated, PAID stamp, `(`, `)` and `\` escaped, non-Latin-1 replaced with `?` | no new package, lock change or audit surface; text stays searchable in tests | `reportlab` (large dependency, CVE surface, and a font story we do not need yet); `weasyprint` (system libraries in the image) |
| Three bookkeeping columns on `invoices` (`last_reminded_at`, `reminder_count` default 0, `overdue_notified_at`) | spacing, cap and the one-shot event are all derivable from the row; no new table | a per-reminder history table (more joins and a migration for no current screen) |
| Scheduler scans once a day per invoice (`ScheduledRun` claim, task `finance.invoice_overdue`, scope = invoice id, period = local date), then the job handler re-checks everything under `SELECT ... FOR UPDATE SKIP LOCKED` scoped by `startup_id` | the claim stops repeated ticks flooding the queue; the row lock plus the bookkeeping columns is the cross-worker guarantee (a locked row is skipped, a late worker sees the columns already set) | relying on the claim alone; advisory locks |
| Auto reminder: first scan after due, then every 7 days, cap `MAX_AUTO_REMINDERS = 8`; a manual reminder resets the clock | PRD cadence, bounded client annoyance | unbounded reminders; fixed calendar weekday |
| Manual `POST /finance/invoices/{id}/remind`: sent and past due only, at least 24 hours since any reminder (422 otherwise) | stops accidental double-clicks and nagging; still allowed after the cap | no gap; reusing the 7 day gap |
| Overdue event is one-shot per invoice (`overdue_notified_at` never cleared); turning `auto_remind` on later sends the reminder but not a second event | a notification per reminder would spam the team | event per reminder |
| `finance.runway.updated` fires on a band flip only (healthy/low, reusing the stored `alert_is_low` for the previous band, no new column); `finance.runway.low` still fires too, so a flip into low makes two notifications | no new state; existing alert unchanged | publishing on every transaction |
| `finance.invoice.paid` published in `mark_paid` once per real transition (also via the provider path with `paid_via`); idempotent replay is silent | one notification per payment | publishing in the endpoint |
| Notification audience for all new finance events = `FINANCE_ROLES` via the single `AUDIENCE` map; `minus_actor=False` | consistent with the read filter; overdue is a system event, and the payer should also see paid | per-event audiences |
| Copy: dashes removed from PRD text ("Hi {client_first}, a friendly nudge ...", "Runway below 6 months, review recommended") | house rule: no em or en dashes in user-facing copy | copying the PRD verbatim |
| D-FIN-5 applied: recurring expenses stay a label (nothing built). D-FIN-6 applied: invoices are NGN only (422 on create and edit; `_ngn_only` normalises case) | PRD decisions | multi-currency invoices (no conversion exists) |
| Email attachments are a first-class `EmailAttachment` on `EmailMessage`; Resend (base64), SMTP and the file sender (`<stem>.attachments/<file>`) honour it, filenames sanitised | one send path for invoice and reminder mails | a second sender for PDFs |

## What's involved
- Migration `alembic/versions/0066_finance_invoice_automation.py` (three nullable/defaulted columns; downgrade drops them).
- `app/services/finance/invoice_pdf.py` (renderer), `reminders.py` (`process_overdue`, `send_manual_reminder`, constants), `invoices.py` (serialize, `mark_paid` event), `runway.py` (`evaluate_runway_alert`).
- `app/api/v1/endpoints/finance.py`: `GET /invoices/{id}/pdf`, `POST /invoices/{id}/remind`.
- `app/worker/scheduler.py` (`_due_invoice_overdue`, `SCHED_INVOICE_OVERDUE`), `handlers/scheduled.py` (overdue handler), `handlers/invoice_email.py` (PDF attachment, `email.invoice_reminder`, `render_reminder_email`).
- `app/platform/email.py` (attachments), `app/schemas/invoice.py` (three response fields, NGN-only), `app/db/models/invoice.py`, `app/core/money.py` (`format_money`).
- `app/services/notifications/registry.py` and `audience.py` (four finance rows).
- Config: none new. Behaviour depends on `SCHEDULER_TIMEZONE` (daily scan date) and the worker/scheduler process running.
- Docs: `docs/fe-integration-guide-finance-invoices-automation.md` (new), cross-links and shape updates in `docs/fe-integration-guide-finance-invoices.md`, refreshed notification blocks in `docs/fe-integration-guide-finance-runway.md`.

## Verification
- Unit and integration: PDF structure, scheduler candidates, `process_overdue` (event once, reminders at 6 and 7 days, cap, paid between scan and handler, auto_remind toggled later), two real sessions with a held row lock, reminder copy and hostile input, `/remind` role and tenant matrix, events and audience, email senders. Full suite on scratch Postgres: 3468 passed, coverage 97.41% (gate 95%).
- Live e2e on a real server (scratch DB `fia_e2e`, file mailbox): `e2e/test_invoice_automation.py`, 36 captures in `e2e/_captures/invoice_automation/`, feeding the FE guide. Full e2e run 87 passed. The existing `e2e/test_finance_runway.py` assertion was updated to the new low-runway title (and its two notification captures refreshed).
- **Not live-verified**: the 7 day spacing and the cap of 8 (need elapsed time; unit-tested), the two-worker race (unit-tested with real sessions), real Resend/SMTP attachment delivery, PDF rendering in a real viewer, a non-UTC `SCHEDULER_TIMEZONE`.
- CI parity run locally with the project toolchain: black, isort, ruff, mypy, full pytest with coverage, migrations job (single head, fresh upgrade, `alembic check`, downgrade to base and upgrade), bandit, pylint, gitleaks (binary, `develop..HEAD`), semgrep (see the PR notes for which could not be run).

## Operate / roll back
- Needs the worker process with the scheduler running (it already does for other scheduled tasks). The first tick after deploy will email reminders for every existing sent, past-due invoice with `auto_remind` on and notify the team once for each overdue invoice; at most 500 invoices per tick.
- Roll back: `alembic downgrade 0065_finance_payments_core` drops the three columns. Code older than this slice ignores the columns. Queued `email.invoice_reminder` jobs without a handler would fail, so drain the queue first.
- Kill switch without a deploy: invoices with `auto_remind` off get the overdue notification but no email; there is no global flag for the notification itself.

## Follow-ups
- PDF is Latin-1 only (non-Latin characters print as `?`); embed a font if names need more.
- Manual remind compares against the UTC date while the scan uses `SCHEDULER_TIMEZONE`; they differ for non-UTC workspaces for part of a day.
- No per-invoice reminder history table (only the last time and the count).
- A flip into low runway sends two notifications (`finance.runway.updated` and `finance.runway.low`); the FE groups them for now.
- FE work: PDF button, Remind button with confirm and 24 hour state, reminder fields on the invoice row, notification routing for the new types, NGN-only builder.
