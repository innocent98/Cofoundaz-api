# FE Integration Guide: Finance Hub, Invoice Automation (PDF, overdue, reminders, paid and runway events)

> **Provenance.** `e2e/test_invoice_automation.py::test_invoice_automation_journey` was run against
> the live app (the `scripts/e2e_run.sh` steps on scratch Postgres database `fia_e2e`, port 8047,
> file email backend; **87 e2e passed**, 86 existing plus this journey). Every **response body**
> below is pasted **verbatim** from the captures it wrote to `e2e/_captures/invoice_automation/`
> (named inline as `capture: <file>.json`) by a script that inlines the files byte for byte, so
> nothing was typed from the schema, the DTO or memory. Two labelled trims only: a notifications
> feed is filtered to its `finance.*` rows (the omitted rows are counted in the block), and mails
> are shown as the JSON the file email sender wrote.
>
> **What "live" means here.** The server was the real uvicorn app. The scheduler tick and the
> worker run **inside the test process** (the harness has no worker container; same pattern as
> `e2e/test_notifications_scheduler.py`), against the same database and mailbox as the server.
> The overdue date was made by **backdating `due_on` directly in the database** (no API sets it).
> Emails went to the **file** email backend, not Resend: real delivery of attachments is not
> verified. The harness captures **responses only**, so **request** bodies shown are the exact JSON
> the journey sends. `id`s, `delivered+...@resend.dev` addresses and timestamps are throwaway
> placeholders from the run.

## What the feature is (in the FE's terms)

Slice (a) gave invoices pay links. This slice makes an unpaid invoice **chase itself** and gives
the FE four new things:

1. **Invoice PDF.** `GET /finance/invoices/{id}/pdf` returns a real PDF (also attached to the
   client's invoice email and to every reminder email). The old "Download PDF is FE-only" note in
   `docs/fe-integration-guide-finance-invoices.md` is obsolete: use the endpoint.
2. **Overdue notice.** The first time the daily scan sees a sent invoice past its due date, the
   founder, team members and accountants get an in-app notification `finance.invoice.overdue`
   (once per invoice, ever).
3. **Reminders to the client.** If `auto_remind` is on, the scan emails the **client** a reminder
   (PDF attached): immediately on the first scan, then every 7 days, at most 8 times. Any
   finance user can also send one by hand with `POST /finance/invoices/{id}/remind`.
4. **Events.** Marking an invoice paid notifies the team (`finance.invoice.paid`). A runway band
   change (healthy to low or back) notifies the team (`finance.runway.updated`).

Base path `/api/v1`. Every route needs `Authorization: Bearer <token>` and `X-Workspace-Id`. Success
envelope `{"data": ..., "meta": null}`; error envelope `{"error": {"code", "message",
"field_errors"}}`. Roles: `founder`, `team_member`, `accountant` (the finance roles); anyone else
gets 403.

| # | Route | Purpose |
|---|---|---|
| 1 | `GET /finance/invoices/{id}/pdf` | download the invoice as PDF (any status, drafts included) |
| 2 | `POST /finance/invoices/{id}/remind` | queue a reminder email to the client now |
| 3 | `GET /notifications` | where the three new notification types arrive (existing route) |

No other route changed; the invoice object gained three fields and `currency` became NGN-only.

---

## Read this first: seven things that will produce a wrong-looking screen

**(1) A reminder emails the client, not the user.** `POST .../remind` and the automatic reminders
send a real email to `client_email`. Put a confirm step ("Email a reminder to {client_email}?") in
front of the button, and after a 200 say "Reminder queued", not "sent": delivery is asynchronous
and best effort (no delivery status exists).

**(2) `overdue` is still a derived status, and it is judged on the UTC date.** There is no way to
set it (section 5 of the invoices guide). The new automation uses the **workspace scheduler
timezone** (`SCHEDULER_TIMEZONE`, UTC by default) for its daily scan, and `/remind` uses the **UTC**
date. They agree unless the scheduler timezone is not UTC; then an invoice can read `overdue` on
the API for some hours before the scan notices it. Always render the badge from `status`.

**(3) The overdue notification is not instant.** It is created by the scheduled scan (one claim
per invoice per local day), so it can lag the moment `due_on` passes by up to a day. Do not wait
for it to flip a badge; the badge comes from `status`.

**(4) Manual remind has a 24 hour gap, the automatic one has a 7 day gap, and they share a clock.**
A manual reminder is refused (422, section 2) if **any** reminder (manual or automatic) went out
in the last 24 hours. A manual reminder also resets the automatic clock, so the next automatic
one is 7 days after it. Disable the "Send reminder" button when `last_reminded_at` is within 24
hours, but still handle the 422 (the clock is the server's).

**(5) Reminders stop after 8, and only automatic ones count.** `reminder_count` counts both
manual and automatic reminders; automatic sending stops once it reaches **8**. A manual reminder
is still allowed after that (it only needs the invoice to be sent, past due and 24 hours since the
last one). Show "Auto reminders paused after 8" when `auto_remind` is on and `reminder_count >= 8`.

**(6) `auto_remind` off still gets the overdue notification, never the automatic email.** Verified
live (section 4): the invoice with `auto_remind: false` got `overdue_notified_at` set and no
reminder email. Turning `auto_remind` on later (a `PATCH`, allowed in any status) makes the
**next** scan send the reminder; the overdue notification does not repeat.

**(7) The PDF is Latin-1 only.** Characters outside Latin-1 (for example the dotted letters in many Yoruba names, emoji, the naira
sign) are printed as `?` in the PDF. The client name and line
descriptions on screen are unaffected. If the user types such characters, warn that the PDF will
show `?`. (Unit-verified, not captured; see the verification table.)

---

## The invoice object: three new fields, NGN only

Every invoice response (create, list, get, patch, send, mark-paid, pay-link, remind) now also has,
always present:

| Field | Type | Notes |
|---|---|---|
| `last_reminded_at` | ISO datetime or `null` | when the most recent reminder (auto or manual) was queued; `null` until one is |
| `reminder_count` | int | reminders queued so far, automatic plus manual; `0` at creation |
| `overdue_notified_at` | ISO datetime or `null` | when the one-shot overdue notification was created; `null` until the scan sees the invoice overdue; never cleared |

**Field-nesting trap.** These three live at the top level of the invoice object (`data.last_reminded_at`),
and on the list route inside each element of `data.invoices`, like every other invoice field. They
are present-as-`null`, never omitted. `last_reminded_at` and `overdue_notified_at` use the
`...Z` form (`2026-10-21T15:22:15.368132Z`), whereas notification `created_at` uses `+00:00`.
Both parse as ISO 8601.

**NGN only (D-FIN-6).** `currency` on `POST /finance/invoices` and `PATCH /finance/invoices/{id}`
accepts `NGN` (case-insensitive, stored as `NGN`) and nothing else. Anything else is a 422 and the
message carries a `Value error, ` prefix straight from the validator, so strip or ignore the
prefix and prefer your own copy. Remove any non-NGN option from the currency picker.

Request: `{"client_name": "Dollar Client", "client_email": "delivered+usd@resend.dev", "line_items": [...], "tax_percent": 7.5, "terms": "net_15", "currency": "USD"}` returns 422, `capture: error_create_non_ngn_currency.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "currency",
        "message": "Value error, Only NGN invoices are supported."
      }
    ]
  }
}
```

`PATCH` with `{"currency": "EUR"}` on a draft returns the same shape, `capture: error_patch_non_ngn_currency.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "currency",
        "message": "Value error, Only NGN invoices are supported."
      }
    ]
  }
}
```

A freshly created draft, `capture: invoice_created.json` (request: client "Chidi Okafor Stores",
one line `Retainer` x2 at 75000, `tax_percent` 7.5, `terms` `net_15`):

```json
{
  "data": {
    "id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
    "number": "INV-2026-001",
    "client_name": "Chidi Okafor Stores",
    "client_email": "delivered+client1-cebfd50a4301@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 75000,
        "amount_minor": 150000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_15",
    "status": "draft",
    "issued_on": null,
    "due_on": null,
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": null,
    "reminder_count": 0,
    "overdue_notified_at": null,
    "created_at": "2026-10-21T15:33:17.494752Z",
    "updated_at": "2026-10-21T15:33:17.494752Z"
  },
  "meta": null
}
```

---

## 1. `GET /finance/invoices/{id}/pdf`

Returns the PDF bytes, not the JSON envelope. The journey captured the response headers
(`capture: pdf_response_headers.json`; `body_starts_with` and `body_bytes` describe the bytes):

```json
{
  "status_code": 200,
  "content-type": "application/pdf",
  "content-disposition": "attachment; filename=\"invoice-INV-2026-001.pdf\"",
  "body_starts_with": "%PDF-1.4",
  "body_bytes": 1697
}
```

- Status `200`, `Content-Type: application/pdf`, `Content-Disposition: attachment;
  filename="invoice-{number}.pdf"`. The body starts `%PDF-1.4` and ends `%%EOF`.
- Works for every status including `draft`. A paid invoice carries a PAID stamp. (The stamp is
  unit-verified, not captured.)
- Fetch it with the auth headers (a plain `<a href>` will not send them): `fetch(...)` with the
  Bearer token, read `response.blob()`, then save with the filename from the header.
- 403 for mentor, `capture: error_pdf_mentor_forbidden.json`; 404 for another workspace's invoice,
  `capture: error_pdf_cross_tenant.json`; 401 without a token, `capture: error_pdf_unauthenticated.json`:

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

Team member and accountant get 200 (statuses asserted in the journey, bodies not captured).

---

## 2. `POST /finance/invoices/{id}/remind`

No request body. Allowed only for a **sent** invoice that is **past due**, and only if no reminder
went out in the last **24 hours**. Roles: founder, team member, accountant. A 200 returns the
invoice (same shape as everywhere) with the bookkeeping updated and queues the email.

Success on an invoice with `auto_remind: false`, called as a team member (`capture: remind_manual_ok.json`):

```json
{
  "data": {
    "id": "deeebb35-d797-4dfa-a56a-78bb45bbed5c",
    "number": "INV-2026-002",
    "client_name": "Ngozi Bello Ltd",
    "client_email": "delivered+client2-3c8a658c584c@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 75000,
        "amount_minor": 150000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_15",
    "status": "overdue",
    "issued_on": "2026-10-21",
    "due_on": "2026-10-18",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": "2026-10-21T15:33:20.762997Z",
    "reminder_count": 1,
    "overdue_notified_at": "2026-10-21T15:33:20.544314Z",
    "created_at": "2026-10-21T15:33:19.269515Z",
    "updated_at": "2026-10-21T15:33:20.756106Z"
  },
  "meta": null
}
```

Errors, all `422` with `code: VALIDATION_ERROR` and the message repeated in `field_errors[0]` with
`field: "status"`:

| Situation | Capture |
|---|---|
| draft (nothing sent) | `error_remind_draft.json` |
| sent but not yet due | `error_remind_not_overdue.json` |
| paid | `error_remind_paid.json` |
| a reminder in the last 24 hours (auto or manual) | `error_remind_within_24h.json`, `error_remind_manual_twice.json` |

Past-due text (draft, not due and paid all return the same message), `capture: error_remind_not_overdue.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a sent invoice that is past due can be reminded.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a sent invoice that is past due can be reminded."
      }
    ]
  }
}
```

The 24 hour text, `capture: error_remind_within_24h.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A reminder was already sent in the last 24 hours.",
    "field_errors": [
      {
        "field": "status",
        "message": "A reminder was already sent in the last 24 hours."
      }
    ]
  }
}
```

Role and tenancy: mentor gets 403 (`capture: error_remind_mentor_forbidden.json`), another
workspace's invoice id gets 404 (`capture: error_remind_cross_tenant.json`):

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

---

## 3. The invoice email now carries the PDF

On `POST /finance/invoices/{id}/send` the client mail has the same subject and body as before and
now one attachment, `invoice-{number}.pdf`. The file email sender wrote this message (and the PDF
beside it); `attachments` lists the attached file names, `attachment_bytes` was added by the
test (`capture: mail_invoice_sent.json`):

```json
{
  "to": "delivered+client1-cebfd50a4301@resend.dev",
  "subject": "Invoice INV-2026-001 from Automation Co",
  "html": "<div style='font-family:system-ui,sans-serif;max-width:560px'><h2>Invoice INV-2026-001</h2><p>From Automation Co</p><p>Bill to Chidi Okafor Stores</p><table style='width:100%'><tr><td>Retainer (x2)</td><td style='text-align:right'>1,500.00 NGN</td></tr></table><p>Subtotal: 1,500.00 NGN<br>Tax: 112.50 NGN<br><strong>Total: 1,612.50 NGN</strong></p><p>Due: 2026-11-05</p></div>",
  "sent_at": "2026-10-21T15:33:19.215907+00:00",
  "attachments": [
    "invoice-INV-2026-001.pdf"
  ],
  "attachment_bytes": 1697
}
```

---

## 4. The scan: overdue notice and the automatic reminder

**What happens.** A background scheduler tick looks for `sent` invoices with `due_on` before
today. For each one, at most once per local day, a job re-checks the invoice under a row lock and
then (a) creates the overdue notification if `overdue_notified_at` is `null`, and (b) if
`auto_remind` is on, `reminder_count < 8` and `last_reminded_at` is `null` or at least 7 days
old, emails the client and bumps `reminder_count`/`last_reminded_at`.

State before the scan (the invoice is overdue by derived status but the scan has not run),
`capture: invoice_overdue_before_scan.json`:

```json
{
  "data": {
    "id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
    "number": "INV-2026-001",
    "client_name": "Chidi Okafor Stores",
    "client_email": "delivered+client1-cebfd50a4301@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 75000,
        "amount_minor": 150000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_15",
    "status": "overdue",
    "issued_on": "2026-10-21",
    "due_on": "2026-10-18",
    "paid_at": null,
    "auto_remind": true,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": null,
    "reminder_count": 0,
    "overdue_notified_at": null,
    "created_at": "2026-10-21T15:33:17.494752Z",
    "updated_at": "2026-10-21T15:33:17.566711Z"
  },
  "meta": null
}
```

After one tick plus worker drain, invoice 1 (`auto_remind: true`), `capture: invoice_after_scan_auto_remind_on.json`:

```json
{
  "data": {
    "id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
    "number": "INV-2026-001",
    "client_name": "Chidi Okafor Stores",
    "client_email": "delivered+client1-cebfd50a4301@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 75000,
        "amount_minor": 150000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_15",
    "status": "overdue",
    "issued_on": "2026-10-21",
    "due_on": "2026-10-18",
    "paid_at": null,
    "auto_remind": true,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": "2026-10-21T15:33:20.519172Z",
    "reminder_count": 1,
    "overdue_notified_at": "2026-10-21T15:33:20.519172Z",
    "created_at": "2026-10-21T15:33:17.494752Z",
    "updated_at": "2026-10-21T15:33:20.508050Z"
  },
  "meta": null
}
```

Invoice 2 (`auto_remind: false`): notice yes, reminder no, `capture: invoice_after_scan_auto_remind_off.json`:

```json
{
  "data": {
    "id": "deeebb35-d797-4dfa-a56a-78bb45bbed5c",
    "number": "INV-2026-002",
    "client_name": "Ngozi Bello Ltd",
    "client_email": "delivered+client2-3c8a658c584c@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 75000,
        "amount_minor": 150000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_15",
    "status": "overdue",
    "issued_on": "2026-10-21",
    "due_on": "2026-10-18",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": null,
    "reminder_count": 0,
    "overdue_notified_at": "2026-10-21T15:33:20.544314Z",
    "created_at": "2026-10-21T15:33:19.269515Z",
    "updated_at": "2026-10-21T15:33:20.538729Z"
  },
  "meta": null
}
```

A second tick the same day changed nothing (`reminder_count` still 1, same `last_reminded_at`,
exactly one reminder mail, no second notification), `capture: invoice_after_second_tick.json`:

```json
{
  "data": {
    "id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
    "number": "INV-2026-001",
    "client_name": "Chidi Okafor Stores",
    "client_email": "delivered+client1-cebfd50a4301@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 75000,
        "amount_minor": 150000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_15",
    "status": "overdue",
    "issued_on": "2026-10-21",
    "due_on": "2026-10-18",
    "paid_at": null,
    "auto_remind": true,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": "2026-10-21T15:33:20.519172Z",
    "reminder_count": 1,
    "overdue_notified_at": "2026-10-21T15:33:20.519172Z",
    "created_at": "2026-10-21T15:33:17.494752Z",
    "updated_at": "2026-10-21T15:33:20.508050Z"
  },
  "meta": null
}
```

### The reminder email (what the client receives)

Subject is exactly `Reminder: invoice {number} from {startup name}`; body is the sentence below;
the invoice PDF is attached. The sentence has no dashes. The "You can view and pay it here: {link}."
part appears **only** when the invoice has a `pay_url` (this invoice had none, so it is absent
here); that variant is unit-verified, not captured. `capture: mail_reminder_auto.json`:

```json
{
  "to": "delivered+client1-cebfd50a4301@resend.dev",
  "subject": "Reminder: invoice INV-2026-001 from Automation Co",
  "html": "<div style='font-family:system-ui,sans-serif;max-width:560px'><p>Hi Chidi, a friendly nudge that invoice INV-2026-001 for 1,612.50 NGN was due on 2026-10-18. Thanks, Ada Founder</p></div>",
  "sent_at": "2026-10-21T15:33:20.621662+00:00",
  "attachments": [
    "invoice-INV-2026-001.pdf"
  ]
}
```

A manual reminder produces the same mail, `capture: mail_reminder_manual.json`:

```json
{
  "to": "delivered+client2-3c8a658c584c@resend.dev",
  "subject": "Reminder: invoice INV-2026-002 from Automation Co",
  "html": "<div style='font-family:system-ui,sans-serif;max-width:560px'><p>Hi Ngozi, a friendly nudge that invoice INV-2026-002 for 1,612.50 NGN was due on 2026-10-18. Thanks, Ada Founder</p></div>",
  "sent_at": "2026-10-21T15:33:20.786809+00:00",
  "attachments": [
    "invoice-INV-2026-002.pdf"
  ]
}
```

---

## 5. Notifications (all in-app, no email)

All four types below go to **founder, team member and accountant**; mentors and other roles get
none and also cannot read them (the read filter). Nobody is excluded as "actor": the person who
clicked mark-paid receives `finance.invoice.paid` too. Fields are in `GET /notifications`
(`data.notifications[]`: `id, type, title, body, data, read, created_at`); `body` is the
call-to-action label. Each `data` carries `href`, the FE route to open.

| `type` | `title` | `body` | `data` keys | `href` |
|---|---|---|---|---|
| `finance.invoice.overdue` | `Invoice overdue: {number} · {client} · NGN 1,612.50` | `Send reminder` | `href, due_on, number, currency, invoice_id, startup_id, client_name, amount_minor` | `/finance/invoices` |
| `finance.invoice.paid` | `Invoice paid: {number} 🎉` | `View invoice` | `href, number, currency, paid_via, invoice_id, startup_id, client_name, amount_minor` | `/finance/invoices` |
| `finance.runway.updated` | `Your runway is now {n} months` or `Your runway is healthy` | `Open Finance` | `band, href, currency, startup_id, monthly_burn, runway_months` | `/finance/runway` |
| `finance.runway.low` (copy changed) | `Runway below 6 months, review recommended` | `Open Finance` | `href, currency, startup_id, monthly_burn, runway_months` | `/finance/runway` |

Notes that matter:
- The title uses a middle dot (`·`, U+00B7) and the paid title an emoji. Render them as given.
- Money in the **title** is `NGN 1,612.50` (code first, major units). `data.amount_minor` is kobo (`161250`).
- The overdue CTA text `Send reminder` should open the invoice and offer the `/remind` action.
- `data.paid_via` is `null` for a manual mark-paid and `paystack`/`flutterwave` for a provider
  payment (the provider value is unit-verified, not captured).
- Titles for `finance.runway.updated` use `runway_months` to one decimal; when it is `null`
  (cash-positive) the title is `Your runway is healthy`.

Founder feed after the scan (`capture: notifications_founder_after_overdue_scan.json`, filtered to
`finance.*`; the team member and accountant captures,
`notifications_team_after_overdue_scan.json` and `notifications_accountant_after_overdue_scan.json`,
contain the same two overdue rows):

```json
{
  "data": {
    "notifications": [
      {
        "id": "4ee89476-46e9-4d9a-ab85-1de6746fdf67",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-002 · Ngozi Bello Ltd · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-002",
          "currency": "NGN",
          "invoice_id": "deeebb35-d797-4dfa-a56a-78bb45bbed5c",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Ngozi Bello Ltd",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.538729+00:00"
      },
      {
        "id": "97a86de4-1ada-4b8f-8737-40d99b82fa86",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-001 · Chidi Okafor Stores · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-001",
          "currency": "NGN",
          "invoice_id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Chidi Okafor Stores",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.508050+00:00"
      },
      "<4 non-finance notifications omitted>"
    ],
    "next_cursor": null
  },
  "meta": null
}
```

The mentor's feed is empty and the unread count is 0 (`notifications_mentor_after_overdue_scan.json`
is `"notifications": []`; `capture: notifications_mentor_unread_count.json`):

```json
{
  "data": {
    "unread": 0
  },
  "meta": null
}
```

---

## 6. Mark paid

`POST /finance/invoices/{id}/mark-paid` on the overdue invoice (unchanged route and behaviour,
plus an event). The response keeps the reminder fields as they were (`capture: invoice_mark_paid.json`):

```json
{
  "data": {
    "id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
    "number": "INV-2026-001",
    "client_name": "Chidi Okafor Stores",
    "client_email": "delivered+client1-cebfd50a4301@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 2,
        "unit_price_minor": 75000,
        "amount_minor": 150000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_15",
    "status": "paid",
    "issued_on": "2026-10-21",
    "due_on": "2026-10-18",
    "paid_at": "2026-10-21T15:33:20.910876Z",
    "auto_remind": true,
    "transaction_id": "8813c44d-7e7b-4a97-8d14-4d0a99ec730b",
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": "2026-10-21T15:33:20.519172Z",
    "reminder_count": 1,
    "overdue_notified_at": "2026-10-21T15:33:20.519172Z",
    "created_at": "2026-10-21T15:33:17.494752Z",
    "updated_at": "2026-10-21T15:33:20.900710Z"
  },
  "meta": null
}
```

The paid notification appears for the founder (and, asserted but not captured, team member and
accountant); the mentor still sees none. A second `mark-paid` is a no-op and does **not** create a
second notification. After payment no further reminder is sent and `/remind` is a 422
(`error_remind_paid.json`, section 2). `capture: notifications_founder_after_paid.json`
(filtered to `finance.*`):

```json
{
  "data": {
    "notifications": [
      {
        "id": "cc79ab8d-5d73-4b8d-91cf-d71333b1dcbc",
        "type": "finance.invoice.paid",
        "title": "Invoice paid: INV-2026-001 🎉",
        "body": "View invoice",
        "data": {
          "href": "/finance/invoices",
          "number": "INV-2026-001",
          "currency": "NGN",
          "paid_via": null,
          "invoice_id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Chidi Okafor Stores",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.900710+00:00"
      },
      {
        "id": "4ee89476-46e9-4d9a-ab85-1de6746fdf67",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-002 · Ngozi Bello Ltd · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-002",
          "currency": "NGN",
          "invoice_id": "deeebb35-d797-4dfa-a56a-78bb45bbed5c",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Ngozi Bello Ltd",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.538729+00:00"
      },
      {
        "id": "97a86de4-1ada-4b8f-8737-40d99b82fa86",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-001 · Chidi Okafor Stores · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-001",
          "currency": "NGN",
          "invoice_id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Chidi Okafor Stores",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.508050+00:00"
      },
      "<4 non-finance notifications omitted>"
    ],
    "next_cursor": null
  },
  "meta": null
}
```

---

## 7. Invoice list

`GET /finance/invoices` returns the new fields on every element of `data.invoices`
(`capture: invoices_list.json`):

```json
{
  "data": {
    "invoices": [
      {
        "id": "deeebb35-d797-4dfa-a56a-78bb45bbed5c",
        "number": "INV-2026-002",
        "client_name": "Ngozi Bello Ltd",
        "client_email": "delivered+client2-3c8a658c584c@resend.dev",
        "line_items": [
          {
            "description": "Retainer",
            "quantity": 2,
            "unit_price_minor": 75000,
            "amount_minor": 150000
          }
        ],
        "subtotal_minor": 150000,
        "tax_percent": 7.5,
        "tax_minor": 11250,
        "total_minor": 161250,
        "currency": "NGN",
        "terms": "net_15",
        "status": "overdue",
        "issued_on": "2026-10-21",
        "due_on": "2026-10-18",
        "paid_at": null,
        "auto_remind": false,
        "transaction_id": null,
        "pay_url": null,
        "pay_provider": null,
        "paid_via": null,
        "last_reminded_at": "2026-10-21T15:33:20.762997Z",
        "reminder_count": 1,
        "overdue_notified_at": "2026-10-21T15:33:20.544314Z",
        "created_at": "2026-10-21T15:33:19.269515Z",
        "updated_at": "2026-10-21T15:33:20.756106Z"
      },
      {
        "id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
        "number": "INV-2026-001",
        "client_name": "Chidi Okafor Stores",
        "client_email": "delivered+client1-cebfd50a4301@resend.dev",
        "line_items": [
          {
            "description": "Retainer",
            "quantity": 2,
            "unit_price_minor": 75000,
            "amount_minor": 150000
          }
        ],
        "subtotal_minor": 150000,
        "tax_percent": 7.5,
        "tax_minor": 11250,
        "total_minor": 161250,
        "currency": "NGN",
        "terms": "net_15",
        "status": "paid",
        "issued_on": "2026-10-21",
        "due_on": "2026-10-18",
        "paid_at": "2026-10-21T15:33:20.910876Z",
        "auto_remind": true,
        "transaction_id": "8813c44d-7e7b-4a97-8d14-4d0a99ec730b",
        "pay_url": null,
        "pay_provider": null,
        "paid_via": null,
        "last_reminded_at": "2026-10-21T15:33:20.519172Z",
        "reminder_count": 1,
        "overdue_notified_at": "2026-10-21T15:33:20.519172Z",
        "created_at": "2026-10-21T15:33:17.494752Z",
        "updated_at": "2026-10-21T15:33:20.900710Z"
      }
    ]
  },
  "meta": null
}
```

---

## 8. Runway events

Every transaction create/update/delete re-evaluates runway. The journey drove the band
healthy to low to healthy:

| Step | Request | Result (live) |
|---|---|---|
| inflow dated 4 months ago | `POST /finance/transactions` NGN 1,000,000 in | no notification |
| outflow today | `POST /finance/transactions` NGN 900,000 out (`transaction_pushing_low.json`) | **two** notifications: `finance.runway.updated` (band `low`) and `finance.runway.low` |
| another small outflow while low | | **none** (events fire on a band flip only) |
| large inflow today (`transaction_recovering.json`) | NGN 4,000,000 in | one `finance.runway.updated` with band `healthy`, title `Your runway is healthy` |

UX consequence: entering low produces two notifications at the same instant. The FE should show
one toast (prefer `finance.runway.low`) or group the pair; both count towards the unread badge.

The trigger transactions are the unchanged transaction shape:

```json
{
  "data": {
    "id": "2442b3cc-bc0b-41ad-8e73-868861995521",
    "date": "2026-10-21",
    "description": "Office build-out",
    "category": "Capex",
    "amount_minor": 90000000,
    "currency": "NGN",
    "direction": "out",
    "source": "manual",
    "created_at": "2026-10-21T15:33:21.079704Z",
    "updated_at": "2026-10-21T15:33:21.079704Z"
  },
  "meta": null
}
```

```json
{
  "data": {
    "id": "f45a1593-c2da-4a25-bf24-c755bc69e3fb",
    "date": "2026-10-21",
    "description": "Bridge round",
    "category": "Fundraising",
    "amount_minor": 400000000,
    "currency": "NGN",
    "direction": "in",
    "source": "manual",
    "created_at": "2026-10-21T15:33:21.166771Z",
    "updated_at": "2026-10-21T15:33:21.166771Z"
  },
  "meta": null
}
```

Founder feed after recovery, newest first (`capture: notifications_founder_after_runway_recovered.json`,
filtered to `finance.*`):

```json
{
  "data": {
    "notifications": [
      {
        "id": "9ac2f25c-5dc8-4919-8d7e-ae79d784f187",
        "type": "finance.runway.updated",
        "title": "Your runway is healthy",
        "body": "Open Finance",
        "data": {
          "band": "healthy",
          "href": "/finance/runway",
          "currency": "NGN",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "monthly_burn": 0,
          "runway_months": null
        },
        "read": false,
        "created_at": "2026-10-21T15:33:21.166771+00:00"
      },
      {
        "id": "9b848f6e-9261-4bed-a359-c6ab80df12e7",
        "type": "finance.runway.low",
        "title": "Runway below 6 months, review recommended",
        "body": "Open Finance",
        "data": {
          "href": "/finance/runway",
          "currency": "NGN",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "monthly_burn": 29946250,
          "runway_months": 0.3
        },
        "read": false,
        "created_at": "2026-10-21T15:33:21.079704+00:00"
      },
      {
        "id": "4be8b5ee-54bf-4b69-a9fc-a6905f9825d3",
        "type": "finance.runway.updated",
        "title": "Your runway is now 0.3 months",
        "body": "Open Finance",
        "data": {
          "band": "low",
          "href": "/finance/runway",
          "currency": "NGN",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "monthly_burn": 29946250,
          "runway_months": 0.3
        },
        "read": false,
        "created_at": "2026-10-21T15:33:21.079704+00:00"
      },
      {
        "id": "cc79ab8d-5d73-4b8d-91cf-d71333b1dcbc",
        "type": "finance.invoice.paid",
        "title": "Invoice paid: INV-2026-001 🎉",
        "body": "View invoice",
        "data": {
          "href": "/finance/invoices",
          "number": "INV-2026-001",
          "currency": "NGN",
          "paid_via": null,
          "invoice_id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Chidi Okafor Stores",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.900710+00:00"
      },
      {
        "id": "4ee89476-46e9-4d9a-ab85-1de6746fdf67",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-002 · Ngozi Bello Ltd · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-002",
          "currency": "NGN",
          "invoice_id": "deeebb35-d797-4dfa-a56a-78bb45bbed5c",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Ngozi Bello Ltd",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.538729+00:00"
      },
      {
        "id": "97a86de4-1ada-4b8f-8737-40d99b82fa86",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-001 · Chidi Okafor Stores · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-001",
          "currency": "NGN",
          "invoice_id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Chidi Okafor Stores",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.508050+00:00"
      },
      "<4 non-finance notifications omitted>"
    ],
    "next_cursor": null
  },
  "meta": null
}
```

And right after entering low (`capture: notifications_founder_after_runway_low.json`, filtered to `finance.*`):

```json
{
  "data": {
    "notifications": [
      {
        "id": "9b848f6e-9261-4bed-a359-c6ab80df12e7",
        "type": "finance.runway.low",
        "title": "Runway below 6 months, review recommended",
        "body": "Open Finance",
        "data": {
          "href": "/finance/runway",
          "currency": "NGN",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "monthly_burn": 29946250,
          "runway_months": 0.3
        },
        "read": false,
        "created_at": "2026-10-21T15:33:21.079704+00:00"
      },
      {
        "id": "4be8b5ee-54bf-4b69-a9fc-a6905f9825d3",
        "type": "finance.runway.updated",
        "title": "Your runway is now 0.3 months",
        "body": "Open Finance",
        "data": {
          "band": "low",
          "href": "/finance/runway",
          "currency": "NGN",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "monthly_burn": 29946250,
          "runway_months": 0.3
        },
        "read": false,
        "created_at": "2026-10-21T15:33:21.079704+00:00"
      },
      {
        "id": "cc79ab8d-5d73-4b8d-91cf-d71333b1dcbc",
        "type": "finance.invoice.paid",
        "title": "Invoice paid: INV-2026-001 🎉",
        "body": "View invoice",
        "data": {
          "href": "/finance/invoices",
          "number": "INV-2026-001",
          "currency": "NGN",
          "paid_via": null,
          "invoice_id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Chidi Okafor Stores",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.900710+00:00"
      },
      {
        "id": "4ee89476-46e9-4d9a-ab85-1de6746fdf67",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-002 · Ngozi Bello Ltd · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-002",
          "currency": "NGN",
          "invoice_id": "deeebb35-d797-4dfa-a56a-78bb45bbed5c",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Ngozi Bello Ltd",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.538729+00:00"
      },
      {
        "id": "97a86de4-1ada-4b8f-8737-40d99b82fa86",
        "type": "finance.invoice.overdue",
        "title": "Invoice overdue: INV-2026-001 · Chidi Okafor Stores · NGN 1,612.50",
        "body": "Send reminder",
        "data": {
          "href": "/finance/invoices",
          "due_on": "2026-10-18",
          "number": "INV-2026-001",
          "currency": "NGN",
          "invoice_id": "9b9e99ff-a2ce-4493-ad17-5ee62d7a0b50",
          "startup_id": "74d93808-3645-4aaa-b96b-7ced0bd74318",
          "client_name": "Chidi Okafor Stores",
          "amount_minor": 161250
        },
        "read": false,
        "created_at": "2026-10-21T15:33:20.508050+00:00"
      },
      "<4 non-finance notifications omitted>"
    ],
    "next_cursor": null
  },
  "meta": null
}
```

---

## State machine and polling guidance

Invoice lifecycle is unchanged (`draft`, `sent`, `overdue` derived, `paid`). The reminder
bookkeeping adds this per-invoice view for a **sent, past-due** invoice:

| Condition | UI |
|---|---|
| `overdue_notified_at == null` | scan has not seen it yet; badge `overdue` from `status` regardless |
| `last_reminded_at == null` | "No reminder sent yet"; Remind enabled |
| `last_reminded_at` within 24 hours | Remind disabled, hint "Reminded {time ago}" (still handle 422) |
| `auto_remind && reminder_count < 8` | "Next automatic reminder about 7 days after {last_reminded_at}" |
| `auto_remind && reminder_count >= 8` | "Automatic reminders paused (8 sent)" |
| `!auto_remind` | "Automatic reminders off"; offer the toggle (`PATCH {"auto_remind": true}`) |

Polling: do **not** poll for the reminder email or its delivery. After `POST .../remind` re-render
from the response (it already has the new `last_reminded_at`/`reminder_count`). Notifications
arrive through the existing feed/stream; the daily scan means a once-per-session refetch of the
invoice list is enough for the reminder fields.

## Errors the FE must handle (exact strings from captures)

| Route | Status | `code` | `message` / `field_errors[0].message` |
|---|---|---|---|
| POST/PATCH invoices, non-NGN `currency` | 422 | `VALIDATION_ERROR` | `Please check the highlighted fields.` / field `currency`: `Value error, Only NGN invoices are supported.` |
| `/remind` draft, not due, paid | 422 | `VALIDATION_ERROR` | `Only a sent invoice that is past due can be reminded.` (field `status`) |
| `/remind` within 24 hours | 422 | `VALIDATION_ERROR` | `A reminder was already sent in the last 24 hours.` (field `status`) |
| `/remind`, `/pdf` as mentor | 403 | `FORBIDDEN` | `You don't have permission to do that.` |
| `/remind`, `/pdf` other workspace | 404 | `NOT_FOUND` | `Not found.` |
| `/pdf` without a token | 401 | `UNAUTHORIZED` | `Not authenticated.` |

## UX consequences the FE must surface

- Confirm before a reminder: it **emails the client** (use `client_email` in the dialog).
- After `/remind` 200 say "Reminder queued", never "delivered".
- Show the 24 hour limit and the "auto reminders stop after 8" rule where the buttons are.
- Tell users the PDF drops non-Latin-1 characters as `?` when they type such text in client names or descriptions.
- Do not offer a currency picker for invoices; they are NGN only.
- Group the two runway notifications that arrive together.

## Verification table

| Behaviour | Status | Evidence / source |
|---|---|---|
| NGN-only 422 on create and PATCH, exact message | verified live | `error_create_non_ngn_currency.json`, `error_patch_non_ngn_currency.json` |
| Three new fields on create, get-after-scan, mark-paid, list | verified live | `invoice_created.json`, `invoice_after_scan_*.json`, `invoice_mark_paid.json`, `invoices_list.json` |
| Invoice mail carries `invoice-{number}.pdf`; file starts `%PDF-` and contains the number | verified live (file sender) | `mail_invoice_sent.json`, journey asserts on the attachment file |
| `GET /pdf`: 200, headers, `%PDF-1.4`/`%%EOF`, draft allowed, number and client name in the bytes | verified live | `pdf_response_headers.json` |
| `/pdf` and `/remind`: mentor 403, cross-tenant 404, `/pdf` 401, team member and accountant 200 on `/pdf` | verified live | `error_pdf_*.json`, `error_remind_*forbidden*`, `error_remind_cross_tenant.json` |
| PAID stamp on a paid invoice's PDF | not exercised live | `tests/services/finance/test_invoice_pdf.py` |
| Non-Latin-1 characters become `?`; parentheses and backslashes escaped | not exercised live | `tests/services/finance/test_invoice_pdf.py` |
| Scan sets `overdue_notified_at`, sends the auto reminder with `auto_remind` on | verified live | `invoice_after_scan_auto_remind_on.json`, `mail_reminder_auto.json` |
| `auto_remind` off: notice, no automatic email | verified live | `invoice_after_scan_auto_remind_off.json` |
| Second tick same day: no duplicate email, no duplicate notification | verified live | `invoice_after_second_tick.json` |
| Reminder subject exact, PDF attached, copy has no dashes | verified live | `mail_reminder_auto.json` |
| Reminder with a pay link (link sentence present) and hostile client name escaping | not exercised live | `tests/worker/test_invoice_reminder_email.py` |
| 7 day spacing, cap of 8, cap and spacing after manual reminder | not exercised live (needs elapsed time; the DB clock was not moved) | `tests/services/finance/test_invoice_reminders.py`, `tests/worker/test_invoice_overdue_scheduler.py` |
| Two workers racing on one invoice send once (row lock) | not exercised live | `tests/services/finance/test_invoice_reminders.py` (two real sessions) |
| Invoice paid between scan and handler sends nothing | not exercised live | unit test |
| `/remind` 200 for team member; 422 for draft, not due, paid, within 24 hours, manual twice | verified live | `remind_manual_ok.json`, `error_remind_*.json` |
| Founder and accountant on `/remind` 200 (live run used a team member) | not exercised live | `tests/api/test_finance_invoice_remind.py::test_finance_roles_can_remind` |
| Overdue notification to founder, team member, accountant; none for mentor (feed and unread count) | verified live | `notifications_*_after_overdue_scan.json`, `notifications_mentor_unread_count.json` |
| Paid notification once (idempotent second mark-paid adds none); mentor none | verified live | `notifications_founder_after_paid.json` |
| `paid_via` populated in the paid notification for a provider payment | not exercised live | `tests/services/finance/test_finance_events.py` |
| Runway band flip events: low then healthy, none on a non-flip, none for the mentor | verified live | `notifications_founder_after_runway_*.json` |
| Scheduler timezone other than UTC vs `/remind` UTC date | not exercised live | source: `app/services/finance/reminders.py` |
| Attachments through Resend and SMTP senders | not exercised live | `tests/platform/test_email.py` (payload shape only, no real provider) |
| Real worker process cadence in staging or production | not exercised live | worker/scheduler run in-process in the journey |
