# Finance Invoice Automation + Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finance BE slice (b): overdue/reminder automation, `finance.invoice.overdue|paid` and `finance.runway.updated` events with notifications, and an invoice PDF (download + email attachment).

**Architecture:** Add three nullable/defaulted columns on `invoices` (migration 0066). A daily-claimed scheduler scan enqueues one `scheduled.finance.invoice_overdue` job per candidate invoice per local day; the handler re-checks everything under `SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent workers or repeated ticks cannot double-send. Reminders go out through the existing `EmailSender` (extended with attachments). The PDF is rendered by a small dependency-free writer (Helvetica, Latin-1) so no new package, lock change or audit surface is added.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Postgres, existing job dispatcher/scheduler/event bus/notification registry.

**Spec:** `docs/checklist/finance-prd-gaps.md` rows 8, 9, 10, 24 and "Build order" 1(b) (in the `cofoundaz` FE repo, `origin/develop`); PRD §12.4 (`Cofoundaz_Technical_PRD.md`).

## Global Constraints

- Money is kobo (`*_minor`); NGN only (D-FIN-6: invoice create/update with any other currency is a 422 with a clear message).
- D-FIN-5: recurring expenses stay a label; nothing to build here.
- No em/en dashes in user-facing copy: PRD copy "Hi {client_first} — a friendly nudge" becomes "Hi {client_first}, a friendly nudge" and "Runway below 6 months — review recommended" becomes "Runway below 6 months, review recommended".
- Reminder subject exactly `Reminder: invoice {number} from {startup}`; body: "Hi {client_first}, a friendly nudge that invoice {number} for {amount} was due on {date}. You can view and pay it here: {link}. Thanks, {founder}" (link sentence omitted when the invoice has no `pay_url`).
- Overdue notification title `Invoice overdue: {number} · {client} · {amount}`, CTA (body) `Send reminder`; paid title `Invoice paid: {number} 🎉`; runway low title `Runway below 6 months, review recommended`, CTA `Open Finance`.
- Audience for all new finance events = `FINANCE_ROLES` (founder, team member, accountant) via `AUDIENCE` in `app/services/notifications/audience.py`.
- Migration `0066_finance_invoice_automation`, `down_revision = "0065_finance_payments_core"`; re-check `origin/develop` before the PR.
- No AI attribution anywhere; no real email providers (console/file sender only); never run pytest against the dev DB (scratch DBs only, `TZ=UTC`, CI env vars).

## Review Focus

- Two scheduler ticks / two workers racing on one invoice: exactly one reminder (test with two sessions and a held lock).
- Invoice paid between scan and handler: no reminder (handler re-check).
- `auto_remind` toggled on after the overdue event already fired: reminder still goes out on the next scan, overdue event does not repeat.
- Reminder cap (`MAX_AUTO_REMINDERS = 8`) and 7-day spacing: a manual reminder resets the clock.
- Client name / pay URL hostile characters in the email and PDF (escaping, header injection, non-Latin-1 text).
- Mentor/consultant get 403 on `/remind` and `/pdf`; cross-tenant invoice id is 404.

---

### Task 1: Data, email attachments, PDF renderer, PDF endpoint, send attaches PDF

**Files:**
- Create: `alembic/versions/0066_finance_invoice_automation.py`, `app/services/finance/invoice_pdf.py`, `tests/test_finance_invoice_automation_migration.py`, `tests/services/finance/test_invoice_pdf.py`, `tests/api/test_finance_invoice_pdf.py`
- Modify: `app/db/models/invoice.py`, `app/schemas/invoice.py` (response fields, NGN-only), `app/services/finance/invoices.py` (serialize), `app/platform/email.py` (attachments), `app/worker/handlers/invoice_email.py`, `app/api/v1/endpoints/finance.py`

**Interfaces:**
- Produces columns on `Invoice`: `last_reminded_at: datetime|None`, `reminder_count: int` (default 0, server_default "0"), `overdue_notified_at: datetime|None`. `InvoiceResponse` gains `last_reminded_at`, `reminder_count`, `overdue_notified_at`.
- Produces `@dataclass EmailAttachment(filename: str, content: bytes, content_type: str)` and `EmailMessage.attachments: list[EmailAttachment] = field(default_factory=list)`; SMTP, Resend (base64 `attachments`), File (writes `<name>.attachments/<filename>` beside the message file or equivalent) and Console senders all honour it.
- Produces `render_invoice_pdf(inv: Invoice, startup_name: str) -> bytes` (valid PDF 1.4, one page per ~30 lines, Helvetica, text uncompressed so tests can search bytes, "PAID" stamp when `status == paid`, non-Latin-1 chars replaced with `?`, parentheses/backslashes escaped).
- Produces `GET /finance/invoices/{id}/pdf` -> `application/pdf`, `Content-Disposition: attachment; filename="invoice-{number}.pdf"`, `_finance` role gate, 404 cross-tenant, draft allowed.

- [ ] Write failing tests: migration adds the 3 columns and downgrades cleanly; `render_invoice_pdf` bytes start `%PDF-`, end `%%EOF`, contain the number, client name, formatted total, "PAID" only when paid, survive `"( ) \\ ₦ é"` in a name; endpoint returns the PDF for F/TM/AC and 403 for mentor/consultant, 404 for another workspace; `handle_invoice_email` attaches `invoice-{number}.pdf` (read `ConsoleEmailSender.sent[0].attachments`); non-NGN invoice create is 422.
- [ ] Run, confirm they fail.
- [ ] Implement migration + model + schema + serialize, email attachment support in all four senders, the renderer, the endpoint, handler attachment, NGN-only validation.
- [ ] Run the new tests plus `tests/api/test_finance*.py tests/worker -k invoice` green; black/isort/ruff/mypy on touched files.
- [ ] Commit `feat(finance): invoice PDF, email attachments and reminder columns`.

### Task 2: Overdue and reminder automation

**Files:**
- Create: `app/services/finance/reminders.py`, `tests/services/finance/test_invoice_reminders.py`, `tests/worker/test_invoice_overdue_scheduler.py`, `tests/api/test_finance_invoice_remind.py`
- Modify: `app/worker/scheduler.py`, `app/worker/handlers/scheduled.py`, `app/worker/handlers/invoice_email.py`, `app/api/v1/endpoints/finance.py`, `app/services/finance/invoices.py` (nothing else)

**Interfaces:**
- Consumes Task 1 columns and `EmailMessage.attachments`.
- Produces constants `REMINDER_INTERVAL_DAYS = 7`, `MAX_AUTO_REMINDERS = 8`, `MANUAL_REMIND_MIN_GAP_HOURS = 24` in `reminders.py`.
- Produces `process_overdue(db, *, invoice_id: uuid.UUID, startup_id: uuid.UUID, now: datetime) -> ProcessResult` (`NamedTuple(overdue_event: bool, reminder_queued: bool)`): row-locks the invoice with `with_for_update(skip_locked=True).populate_existing()` scoped by `startup_id`; returns `(False, False)` when the row is locked/missing/not `sent`/not past `due_on`; sets `overdue_notified_at` and publishes `finance.invoice.overdue` (payload `startup_id, invoice_id, number, client_name, amount_minor, currency, due_on, href="/finance/invoices"`) once; if `auto_remind` and `reminder_count < MAX_AUTO_REMINDERS` and (`last_reminded_at is None` or `now - last_reminded_at >= 7 days`): sets `last_reminded_at=now`, `reminder_count += 1`, enqueues job `email.invoice_reminder` `{invoice_id}`.
- Produces `send_manual_reminder(db, *, startup_id, invoice_id, now) -> Invoice` (locks; 422 `status` error unless `sent` and past due; 422 if `last_reminded_at` is within 24h; else same bookkeeping + enqueue) and `POST /finance/invoices/{id}/remind` (200 invoice body, `_finance` gate).
- Produces scheduler `_due_invoice_overdue(db, now) -> list[Due]` with `SCHED_INVOICE_OVERDUE = "scheduled.finance.invoice_overdue"`, task_key `finance.invoice_overdue`, scope = invoice id, period = local date; candidates = `sent` invoices of non-deleted startups with `due_on < local today` AND (`overdue_notified_at IS NULL` OR (`auto_remind` AND `reminder_count < MAX` AND (`last_reminded_at IS NULL` OR `last_reminded_at <= now - 7d`))), limit 500, ordered by `due_on`.
- Produces handler `email.invoice_reminder` (`render_reminder_email(inv, startup_name, founder_name) -> str`, subject `Reminder: invoice {number} from {startup}` with CR/LF stripped, PDF attached, skips silently when the invoice is no longer `sent`) and handler for `SCHED_INVOICE_OVERDUE` calling `process_overdue`.

- [ ] Write failing tests: overdue transition (a `sent` invoice past due serialises `status="overdue"`, a not-yet-due one does not); scheduler picks overdue-unnotified, picks auto_remind after 7 days, skips paid/draft/deleted startup/not-due/cap reached/auto_remind off after notified; second `scheduler_tick` the same day enqueues nothing; `process_overdue` fires the event once and a reminder once per 7 days (advance `now` 6d: none, 7d: one); concurrent test: session A holds the row lock (`with_for_update`), session B `process_overdue` returns `(False, False)` and sends nothing; paid-between-scan-and-handle sends nothing; toggle auto_remind on after notified: reminder yes, event no; reminder email subject/body copy exact, includes pay link only when present, escapes hostile client name; `/remind` 200 for F/TM/AC, 403 mentor/consultant, 404 cross-tenant, 422 not overdue, 422 within 24h.
- [ ] Run, confirm they fail.
- [ ] Implement per Interfaces. `_claim` stays the scheduler's once-per-day gate; the row lock is the cross-worker guarantee.
- [ ] Run new tests + `tests/worker tests/api/test_finance*.py` green; lint/mypy.
- [ ] Commit `feat(finance): overdue detection and auto-remind reminders`.

### Task 3: Events and notifications

**Files:**
- Modify: `app/services/finance/invoices.py` (`mark_paid`), `app/services/finance/runway.py` (`evaluate_runway_alert`), `app/services/notifications/registry.py`, `app/services/notifications/audience.py`, `app/services/notifications/categories.py` (only if email categories apply: none, in-app only)
- Create: `tests/services/finance/test_finance_events.py`, `tests/services/notifications/test_finance_notifications.py`

**Interfaces:**
- `mark_paid` publishes `finance.invoice.paid` once per real transition (not on the idempotent no-op), payload `startup_id, invoice_id, number, client_name, amount_minor, currency, paid_via (None|provider), href="/finance/invoices"`; covers `mark_paid_via_provider` (it calls `mark_paid`).
- `evaluate_runway_alert` additionally publishes `finance.runway.updated` (payload `startup_id, runway_months, monthly_burn, currency, band`) only when the band (`"healthy"` / `"low"`, same `_runway_is_low` rule) changes versus the stored state; reuse `alert_is_low` for the previous band so no new column is needed. First evaluation with no change publishes nothing.
- Registry rows: `finance.invoice.overdue` (title from payload, body `Send reminder`), `finance.invoice.paid` (title `Invoice paid: {number} 🎉`, body `View invoice`), `finance.runway.updated` (title `Your runway is now {n} months`, body `Open Finance`; `n` formatted to 1 decimal, "more than a year" safe when None -> `Your runway is healthy`), and `finance.runway.low` gets title `Runway below 6 months, review recommended` and body `Open Finance`. Each payload carries `href` (`/finance/runway` for runway events). `AUDIENCE` maps all four to `FINANCE_ROLES`; `minus_actor=False` for overdue (a system event).

- [ ] Failing tests: paid event once on first `mark_paid` and zero on repeat; webhook/importer path publishes with `paid_via`; audience: founder, team member and accountant receive, mentor/consultant do not (use the real fan-out + read-filter helpers); titles exact (amount formatted `NGN 1,500.00`-style via one shared formatter, no dashes); runway.updated only on band flip; `runway.low` copy updated; existing runway tests still pass.
- [ ] Run, confirm they fail, implement, run green, lint/mypy.
- [ ] Commit `feat(finance): invoice paid/overdue and runway updated events with notifications`.

### Task 4: E2E journey, FE guide, SOP, checklist, CI parity

**Files:**
- Create: `e2e/test_invoice_automation.py`, `docs/fe-integration-guide-finance-invoices-automation.md`, `docs/sop/2026-10-21-finance-invoice-automation.md`
- Modify: `docs/checklist/PROJECT_CHECKLIST.md`, `docs/fe-integration-guide-finance-invoices.md` (cross-link and changed shapes)

- [ ] E2E (live server via `scripts/e2e_run.sh`, captures to `e2e/_captures/invoice_automation/`): create+send invoice (PDF attached in the file-sender mail), `GET .../pdf` (assert `%PDF-`), backdate `due_on` with a direct DB write, run `scheduler_tick` + worker drain, capture overdue notification (founder/TM/AC yes, mentor no), the reminder mail, a second tick (no duplicate), `/remind` 422 within 24h, `mark-paid` then paid notification, role 403s, runway.updated notification.
- [ ] FE guide with bodies pasted from captures, verification table, anything not exercised live marked.
- [ ] SOP + checklist rows; reconcile Finance gap rows 8, 9, 10, 24 as done in the checklist note.
- [ ] CI parity: black, isort, ruff, mypy, full pytest with coverage (scratch DB), migrations job steps (single head, round-trip, drift), gitleaks/semgrep as the workflows define. Fix to green, then push `feature/finance-invoice-automation`, open PR to `develop`, do not merge.
