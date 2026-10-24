# FE Integration Guide — Finance Hub: Invoices (Module 12, Slice 3)

> **Superseded in part (slice e):** invoices gain `account_id` / `account_name`, `notes`, `payment_details` and `last_sent_at`, and a `POST /finance/invoices/{id}/resend` route. See `docs/fe-integration-guide-finance-slice-e.md` §2; where this guide disagrees, that guide wins.

> **Superseded in part (slice c, ledger correctness):** `GET /finance/invoices` is paginated (`limit` default 100, `cursor`, `meta.next_cursor`, `meta.total_estimate`). See `docs/fe-integration-guide-finance-ledger.md` for the current shapes; where this guide disagrees, that guide wins.

> **Provenance.** `e2e/test_invoices.py::test_finance_invoices_journey` was run against the live
> app (`bash scripts/e2e_run.sh`, **61 e2e passed**, no Resend-429 flake) and every **response
> body** below is pasted **verbatim** from the 40 captures it wrote to
> `e2e/_captures/invoices/` (named inline as `capture: <file>.json`). Nothing was written from the
> schema, the DTO or memory. Two kinds of trimming are used and always labelled in the block:
> `"line_items": "<…same as above…>"` where an invoice object repeats a payload already shown in
> full, and `"by_month": "<12 points omitted>"` on the runway payload. Nothing else is trimmed and no
> field is invented.
>
> The harness captures **responses only**, so **request** bodies are the exact JSON the journey sends
> (read from `e2e/test_invoices.py`), not captures. Everything **not** exercised live is marked
> ⚠️ inline; the verification table at the end says where each shape came from. Two setup notes
> about the live run: (a) `due_on` was **backdated directly in the database** to prove the derived
> `overdue` status (there is no API to move time or to set `due_on`) — see §9; (b) the client email
> was read back out of the **file** email backend, not real Resend — see §5.
>
> `id`, `transaction_id`, `created_at`, `paid_at` and the `delivered+inv-…@resend.dev` addresses
> are throwaway values from the test run — treat them as **placeholders**.

> **Update (invoice automation, 2026-10-21).** Overdue notices, automatic and manual reminders, the
> invoice PDF endpoint, the paid and runway events, three new invoice fields (`last_reminded_at`,
> `reminder_count`, `overdue_notified_at`) and the NGN-only currency rule are documented, with live
> captures, in [`fe-integration-guide-finance-invoices-automation.md`](fe-integration-guide-finance-invoices-automation.md).
> Where this older guide says there is no PDF endpoint, no reminders, no invoice notifications or
> that `currency` is free text, the newer guide wins. The example blocks below were captured before
> the three new fields existed, so they do not show them; the live API now always returns them.

Invoices are the second money-in document of the Finance Hub. The FE gets (1) an **invoice builder**
(line items, tax, terms → server-computed totals), (2) a **lifecycle** — draft → sent → paid, with a
derived `overdue` — including **emailing the invoice to the client**, and (3) a **real consequence in
the books**: marking an invoice paid books the money into the Slice 1 ledger, so **Cash Flow moves and
Runway moves**, and marking it unpaid reverses that exactly.

Base path: `/api/v1/finance`. Every route requires `Authorization: Bearer <token>` and an
`X-Workspace-Id` header (the workspace id from `GET /auth/me` → `active_workspace_id`). Every
success response is the standard envelope `{"data": …, "meta": null}`.

**Access: `founder`, `team_member` or `accountant`** (same as the rest of `/finance`). Any other
membership role gets **403 `FORBIDDEN`** on all eight invoice routes. Invoices are workspace-scoped:
another workspace's invoice id is **404 `NOT_FOUND`**, never readable. ⚠️ 403, accountant-allowed and
cross-tenant 404 are unit-verified (`test_rbac_forbidden`, `test_accountant_allowed`,
`test_lifecycle_rbac_forbidden`, `test_get_one_and_cross_tenant_404`, `test_cross_tenant_*`), not
e2e-captured.

| # | Route | Purpose |
|---|---|---|
| 1 | `POST /finance/invoices` | create a **draft** |
| 2 | `GET /finance/invoices?status=` | list (optional filter) |
| 3 | `GET /finance/invoices/{id}` | one invoice |
| 4 | `PATCH /finance/invoices/{id}` | edit (draft only; `auto_remind` any time) |
| 5 | `POST /finance/invoices/{id}/send` | draft → sent, emails the client |
| 6 | `POST /finance/invoices/{id}/mark-paid` | sent/overdue → paid, books the inflow |
| 7 | `POST /finance/invoices/{id}/mark-unpaid` | paid → sent, reverses the inflow |
| 8 | `DELETE /finance/invoices/{id}` | delete (draft only) |
| 9 | `POST /finance/invoices/{id}/pay-link` | create or retry the provider pay link (see below) |
| 10 | `GET /finance/invoices/{id}/pdf` | download the invoice PDF (new, see the automation guide) |
| 11 | `POST /finance/invoices/{id}/remind` | email the client a reminder now (new, see the automation guide) |

---

## Read this first — eight things that will produce a wrong-looking screen

**(1) The server computes every total. The builder's numbers are advisory.** The request has **no**
`subtotal`, `tax_minor` or `total` field, and the server ignores any you send: the journey's create
request deliberately included `"total_minor": 1`, and the stored invoice came back with
`"total_minor": 134375` (§1). Show a live preview in the builder if you like, but **after every
create/PATCH re-render from the response** — never from your local arithmetic. The rules the server
applies (so your preview can match): `amount = quantity × unit_price_minor` per line; `subtotal =
Σ amounts`; `tax = round-half-up(subtotal × tax_percent ÷ 100)` to a whole minor unit (half a kobo
rounds **up**, not to-even); `total = subtotal + tax`. `tax_percent` is stored to 2 decimals.
Subtotal and total are capped at **2,147,483,647** minor units (→ 422, see Errors).

**(2) `status` is DERIVED on read, and `overdue` is never stored — do not try to set it.** The stored
states are only `draft`, `sent`, `paid`. `overdue` is computed on every read as *stored status is
`sent` **and** `due_on` is before today (UTC)*. There is **no endpoint that sets status** and `PATCH`
has no `status` field: sending `{"status": "…"}` to `PATCH` is silently ignored (200, unchanged —
verified live, §9). The FE must therefore **never offer "mark as overdue"**; render the badge from the
`status` the API returns. An overdue invoice can still be marked paid (§6) and then reads `paid`.
`GET /finance/invoices?status=sent` **excludes** overdue invoices (verified live, §9): a sent invoice
is *either* `sent` *or* `overdue`, never both.

**(3) Marking paid moves Cash Flow AND Runway; marking unpaid reverses it exactly.** This is the
composition of Slices 1–3. `mark-paid` creates a ledger **inflow** (category `Revenue`, dated **today**,
not the issue/due date) and links it on the invoice as `transaction_id`. The next
`GET /finance/cash-flow` and `GET /finance/runway` include it. Live numbers (§6): cash on hand
88,000,000 → **88,161,250** (+ exactly the 161,250 total); `monthly_revenue` 0 → 53,750;
`monthly_burn` 4,000,000 → 3,946,250 (an inflow inside the window reduces *net* burn);
`runway_months` 22.0 → 22.3. `mark-unpaid` deletes that inflow and the summary returns **byte-for-byte**
to its earlier state (the journey asserts the whole cash-flow and runway payloads are equal before
and after). **After either call, re-fetch cash-flow/runway** — do not adjust cached numbers locally.
A runway alert or the `money.runway_live` health signal is also re-evaluated on both calls (⚠️
unit-verified, `test_mark_paid_and_unpaid_refresh_runway_alert_and_signal`; the live journey did not
cross the low-runway threshold).

**(4) The paid inflow is invoice-managed — hide edit/delete for `source == "invoice"` rows.** The
inflow shows up in `GET /finance/transactions` with **`"source": "invoice"`** (Slice 1 only ever
showed `"manual"`). `PATCH` or `DELETE` on it returns **422** — *"This transaction is managed by an
invoice; unpay the invoice to change or remove it."* (verified live, §7). In the Transactions ledger,
**hide the edit/categorize/delete controls on any row whose `source` is `"invoice"`** and, ideally,
link the row to its invoice. To undo it the user marks the *invoice* unpaid.

**(5) Money is integer minor units everywhere in the API.** `unit_price_minor`, `amount_minor`,
`subtotal_minor`, `tax_minor`, `total_minor` (and the cash-flow/runway numbers) are integers in minor
units (kobo for NGN). `161250` with `"currency": "NGN"` is **₦1,612.50**. The FE divides by 100 and maps
the ISO code to a symbol; never send or display a float amount. **The exception is the client email**,
which the server renders itself in **major units** (`1,612.50 NGN`, §5) — the emailed numbers will
look "100× smaller" than the raw API integers; that is correct, not a bug.

**(6) Sending emails the client — asynchronously and best-effort. Don't block on it.** `POST …/send`
flips the invoice to `sent` and *queues* an email; the response returns immediately and **does not
tell you the email was delivered**. There is no delivery-status field. Show "Invoice sent" from the
200, not from any email confirmation, and don't poll for one. If delivery fails (client address
bounces, provider down) the invoice is still `sent`; there is no retry/redelivery endpoint in this
slice.

**(7) The state machine is strict; illegal moves are 422, not silent.** See the table in §"State
machine". In particular `send` is one-shot (a second send is 422), and only **drafts** can be edited or
deleted — once sent, the commercial terms are frozen.

**(8) Known limits to design around** (also in "Known limitations" below): single-currency
assumption (currencies are **not converted** — a foreign-currency invoice's inflow adds raw minor units
into cash-on-hand); `currency` is now **NGN only** (any other value is a 422, D-FIN-6); **reminders and overdue
notifications now exist** (`auto_remind` drives automatic client reminders) and **the PDF is served by
`GET /finance/invoices/{id}/pdf`**, both documented in the automation guide.

---

## The invoice object

Every invoice endpoint (except list, which wraps an array, and delete) returns exactly this shape,
all 25 fields always present (19 + the three payment fields below + the three reminder fields below) (nullable ones are present-as-`null`, never omitted):

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `number` | string | `INV-<year>-<NNN>`, e.g. `INV-2026-001`. Server-assigned, unique per workspace, 3-digit zero-padded sequence per calendar year (UTC year at creation). Not settable. Deleting the **most recent** draft frees its number for reuse; numbers of sent/paid invoices are never reused |
| `client_name` | string | 1–200 chars |
| `client_email` | string | valid email; the send target |
| `line_items` | array | each `{description, quantity, unit_price_minor, amount_minor}` — **`amount_minor` is server-added** (`quantity × unit_price_minor`); you do not send it |
| `subtotal_minor` | int | server-computed |
| `tax_percent` | float | as stored (2 dp), e.g. `7.5`, `0.0` |
| `tax_minor` | int | server-computed |
| `total_minor` | int | server-computed |
| `currency` | string | `"NGN"` only (default); anything else is a 422 `Value error, Only NGN invoices are supported.` |
| `terms` | string | `net_15` \| `net_30` \| `due_on_receipt` |
| `status` | string | **derived**: `draft` \| `sent` \| `paid` \| `overdue` |
| `issued_on` | `YYYY-MM-DD` \| null | `null` until sent (set to today, UTC, on send) |
| `due_on` | `YYYY-MM-DD` \| null | `null` until sent; `issued_on` + 15 / 30 / 0 days by `terms` |
| `paid_at` | ISO datetime \| null | set on mark-paid; back to `null` on mark-unpaid |
| `auto_remind` | bool | when true the daily scan emails the client a reminder (first scan after due, then every 7 days, max 8); see the automation guide |
| `transaction_id` | uuid \| null | the ledger inflow this invoice produced; non-null exactly while `paid` |
| `pay_url` | string \| null | hosted checkout link, set when the invoice was sent (or the pay-link route was called) while a Paystack or Flutterwave connection was active. `null` otherwise. **Added by the payments work** |
| `pay_provider` | `"paystack"` \| `"flutterwave"` \| null | which provider issued `pay_url`. **Added by the payments work** |
| `paid_via` | `"paystack"` \| `"flutterwave"` \| null | set only when the provider's webhook marked the invoice paid; `null` for manual mark-paid. **Added by the payments work** |
| `last_reminded_at` | ISO datetime \| null | **new**: when the latest reminder (auto or manual) was queued |
| `reminder_count` | int | **new**: reminders queued so far (auto plus manual), `0` at creation |
| `overdue_notified_at` | ISO datetime \| null | **new**: when the one-shot overdue notification was created, `null` until the scan sees it overdue |
| `created_at` / `updated_at` | ISO datetime | |

**Field-nesting traps.** (a) The list is at `data.invoices`, **not** `data` — every other invoice
route returns the invoice object directly at `data`. (b) `DELETE` returns `data: {"deleted": true}`,
not an invoice. (c) `line_items[].amount_minor` exists in **responses only**; `line_items` items in a
request take exactly three fields. (d) `tax_percent` is a JSON number (`0.0`), not a string. (e)
`transaction_id`/`paid_at`/`issued_on`/`due_on` are present-but-`null` when not applicable.

## State machine

```
            send                mark-paid
  draft ───────────▶ sent ───────────────────▶ paid
    │                 │ ▲                        │
    │ delete          │ └────────────────────────┘
    ▼                 │        mark-unpaid
  (gone)              └── due_on < today (UTC) ⇒ READ as "overdue" (still stored `sent`)
```

| Stored → shown | Edit (`PATCH`) | Delete | Send | Mark paid | Mark unpaid |
|---|---|---|---|---|---|
| `draft` | ✅ any field | ✅ | ✅ | ❌ 422 | ❌ 422 |
| `sent` | only `auto_remind` | ❌ 422 | ❌ 422 | ✅ | ❌ 422 |
| `overdue` (sent, past due) | only `auto_remind` | ❌ 422 | ❌ 422 | ✅ | ❌ 422 |
| `paid` | only `auto_remind` | ❌ 422 | ❌ 422 | ✅ **no-op** (200, unchanged) | ✅ → `sent`; **❌ 422 when `paid_via` is set** |

Every ❌ is a **422 `VALIDATION_ERROR`** with `field_errors[0].field == "status"` and a specific message
(see Errors). `mark-unpaid` returns the invoice to **`sent`** (not `draft`) and does **not** touch
`issued_on`/`due_on` — so if the due date has already passed, an unpaid invoice immediately reads
`overdue` again.

---

## 1. `POST /finance/invoices` — create a draft

**Request** (the exact JSON the e2e journey sends — note the deliberately-bogus `total_minor`, which
the server ignores):

```json
{
  "client_name": "Acme Traders Ltd",
  "client_email": "delivered+inv-<12 hex>@resend.dev",
  "line_items": [
    { "description": "Design retainer", "quantity": 1, "unit_price_minor": 100000 },
    { "description": "Hosting setup", "quantity": 5, "unit_price_minor": 5000 }
  ],
  "tax_percent": 7.5,
  "currency": "NGN",
  "terms": "net_30",
  "total_minor": 1
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `client_name` | string | yes | 1–200 chars |
| `client_email` | string | yes | valid email address |
| `line_items` | array | yes | **1–50** items |
| `line_items[].description` | string | yes | 1–300 chars |
| `line_items[].quantity` | int | yes | 1–100,000 |
| `line_items[].unit_price_minor` | int | yes | 0–2,147,483,647 |
| `tax_percent` | number | no (default `0`) | 0–100 |
| `currency` | string | no (default `"NGN"`) | must be `NGN` (case-insensitive); other values 422 |
| `terms` | string | no (default `"net_30"`) | `net_15` \| `net_30` \| `due_on_receipt`; anything else → 422 |

Unknown fields (like `total_minor` above) are ignored, not rejected.

**Response `200`** (create returns **200**, not 201) — `capture: invoice_created.json`:

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": [
      {
        "description": "Design retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      },
      {
        "description": "Hosting setup",
        "quantity": 5,
        "unit_price_minor": 5000,
        "amount_minor": 25000
      }
    ],
    "subtotal_minor": 125000,
    "tax_percent": 7.5,
    "tax_minor": 9375,
    "total_minor": 134375,
    "currency": "NGN",
    "terms": "net_30",
    "status": "draft",
    "issued_on": null,
    "due_on": null,
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.588896Z"
  },
  "meta": null
}
```

Arithmetic, so you can check your rendering: lines 1 × 100,000 + 5 × 5,000 = **125,000** subtotal; 7.5 %
tax = **9,375**; total **134,375** (₦1,343.75). The client's `total_minor: 1` was ignored. The number is
`INV-2026-001` — the first of the year for this workspace. A second draft created in the same run got
`INV-2026-002` (`capture: invoice_created_second_draft.json`; `terms: "due_on_receipt"`, `tax_percent`
`0.0`):

```json
{
  "data": {
    "id": "f1f7f149-47f7-4d5f-8c03-d15adbbe944f",
    "number": "INV-2026-002",
    "client_name": "Beta Foods",
    "client_email": "delivered+inv-3d8de01d7cc1@resend.dev",
    "line_items": [
      {
        "description": "Consulting",
        "quantity": 2,
        "unit_price_minor": 7000,
        "amount_minor": 14000
      }
    ],
    "subtotal_minor": 14000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 14000,
    "currency": "NGN",
    "terms": "due_on_receipt",
    "status": "draft",
    "issued_on": null,
    "due_on": null,
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.611065Z",
    "updated_at": "2026-09-30T08:32:55.611065Z"
  },
  "meta": null
}
```

---

## 2. `GET /finance/invoices` — list, optional `?status=`

Query param `status` (optional): `draft` | `sent` | `paid` | `overdue`. Any other value → **422**.
**Order: `created_at` descending** (newest first; ties by `number` descending). There is **no
pagination** — the full set comes back in one response. The `status` filter operates on the **derived**
status (so `?status=overdue` works and `?status=sent` excludes overdue).

**`GET /finance/invoices`** (no filter; the two invoices at the time — `capture: invoices_list_all.json`,
line items trimmed):

```json
{
  "data": {
    "invoices": [
      {
        "id": "f1f7f149-47f7-4d5f-8c03-d15adbbe944f",
        "number": "INV-2026-002",
        "client_name": "Beta Foods",
        "client_email": "delivered+inv-3d8de01d7cc1@resend.dev",
        "line_items": "<\u2026same as above\u2026>",
        "subtotal_minor": 14000,
        "tax_percent": 0.0,
        "tax_minor": 0,
        "total_minor": 14000,
        "currency": "NGN",
        "terms": "due_on_receipt",
        "status": "draft",
        "issued_on": null,
        "due_on": null,
        "paid_at": null,
        "auto_remind": false,
        "transaction_id": null,
        "created_at": "2026-09-30T08:32:55.611065Z",
        "updated_at": "2026-09-30T08:32:55.611065Z"
      },
      {
        "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
        "number": "INV-2026-001",
        "client_name": "Acme Traders Ltd",
        "client_email": "delivered+inv-6636f79d668d@resend.dev",
        "line_items": "<\u2026same as above\u2026>",
        "subtotal_minor": 150000,
        "tax_percent": 7.5,
        "tax_minor": 11250,
        "total_minor": 161250,
        "currency": "NGN",
        "terms": "net_30",
        "status": "paid",
        "issued_on": "2026-09-30",
        "due_on": "2026-10-30",
        "paid_at": "2026-09-30T08:32:55.704271Z",
        "auto_remind": true,
        "transaction_id": "37220419-300c-4eb8-ae3b-0317af2ba13d",
        "created_at": "2026-09-30T08:32:55.588896Z",
        "updated_at": "2026-09-30T08:32:55.701521Z"
      }
    ]
  },
  "meta": null
}
```

**`?status=paid`** — `capture: invoices_list_paid.json` (taken while INV-2026-001 was paid; one
invoice, shown in full):

```json
{
  "data": {
    "invoices": [
      {
        "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
        "number": "INV-2026-001",
        "client_name": "Acme Traders Ltd",
        "client_email": "delivered+inv-6636f79d668d@resend.dev",
        "line_items": [
          {
            "description": "Design retainer",
            "quantity": 1,
            "unit_price_minor": 100000,
            "amount_minor": 100000
          },
          {
            "description": "Hosting setup",
            "quantity": 10,
            "unit_price_minor": 5000,
            "amount_minor": 50000
          }
        ],
        "subtotal_minor": 150000,
        "tax_percent": 7.5,
        "tax_minor": 11250,
        "total_minor": 161250,
        "currency": "NGN",
        "terms": "net_30",
        "status": "paid",
        "issued_on": "2026-09-30",
        "due_on": "2026-10-30",
        "paid_at": "2026-09-30T08:32:55.704271Z",
        "auto_remind": true,
        "transaction_id": "37220419-300c-4eb8-ae3b-0317af2ba13d",
        "created_at": "2026-09-30T08:32:55.588896Z",
        "updated_at": "2026-09-30T08:32:55.701521Z"
      }
    ]
  },
  "meta": null
}
```

**`?status=draft`** returned only INV-2026-002 (`capture: invoices_list_draft.json`, same shape).
**`?status=overdue`** with none overdue — an **empty array, not an error** (`capture:
invoices_list_overdue_empty.json`):

```json
{
  "data": {
    "invoices": []
  },
  "meta": null
}
```

**Bad filter** — `?status=bogus` → 422 (`capture: error_list_bad_status.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.status",
        "message": "Input should be 'draft', 'sent', 'paid' or 'overdue'"
      }
    ]
  }
}
```

---

## 3. `GET /finance/invoices/{id}` — one invoice

Returns the invoice object at `data` (`capture: invoice_get_one.json`, taken while the invoice was
`paid`; line items trimmed):

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": "<\u2026same as above\u2026>",
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "paid",
    "issued_on": "2026-09-30",
    "due_on": "2026-10-30",
    "paid_at": "2026-09-30T08:32:55.704271Z",
    "auto_remind": true,
    "transaction_id": "37220419-300c-4eb8-ae3b-0317af2ba13d",
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.701521Z"
  },
  "meta": null
}
```

Unknown id or another workspace's id → **404 `NOT_FOUND`** (`capture: error_unknown_invoice.json`):

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

## 4. `PATCH /finance/invoices/{id}` — edit

Partial update: send only what changes. **Drafts** accept every field of the create body plus
`auto_remind`; **sent/overdue/paid** invoices accept **only `auto_remind`** — anything else is a 422.

- `line_items`, if sent, **replaces the whole list** (send all lines, not just the changed one).
- Sending `line_items` and/or `tax_percent` makes the server recompute subtotal/tax/total. Editing only
  client fields / `terms` / `currency` leaves the totals alone. ⚠️ (unit-verified: `test_edit_draft_*`.)
- Explicit `null` on any field → 422 (⚠️ unit-verified, `test_patch_explicit_null_rejected`); omit a field
  to leave it untouched. Invalid `terms`, empty `line_items` → 422 (⚠️ unit-verified).
- `terms` changes on a draft do not move dates — dates are only set at send time.

**Request** (the journey's edit — change the second line from 5 to 10 units, still a draft):

```json
{
  "line_items": [
    { "description": "Design retainer", "quantity": 1, "unit_price_minor": 100000 },
    { "description": "Hosting setup", "quantity": 10, "unit_price_minor": 5000 }
  ]
}
```

**Response `200`** — totals recomputed by the server (100,000 + 10 × 5,000 = 150,000; tax 11,250; total
161,250) — `capture: invoice_edited.json`:

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": [
      {
        "description": "Design retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      },
      {
        "description": "Hosting setup",
        "quantity": 10,
        "unit_price_minor": 5000,
        "amount_minor": 50000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "draft",
    "issued_on": null,
    "due_on": null,
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.601637Z"
  },
  "meta": null
}
```

**`auto_remind` after send** — the one field that stays editable. Request `{"auto_remind": true}` on
the now-`sent` invoice → 200 (`capture: invoice_auto_remind_after_send.json`, line items trimmed):

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": "<\u2026same as above\u2026>",
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-09-30",
    "due_on": "2026-10-30",
    "paid_at": null,
    "auto_remind": true,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.693691Z"
  },
  "meta": null
}
```

**Editing a non-draft** — request `{"client_name": "Renamed"}` on a sent invoice → **422** (`capture:
error_patch_sent_invoice.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a draft invoice can be edited.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a draft invoice can be edited."
      }
    ]
  }
}
```

**`status` is not writable** — `PATCH {"status": "paid"}` on a (then-overdue) invoice returned **200
with the invoice unchanged** (`status` still `"overdue"`): the unknown field is dropped. See §9
(`capture: patch_status_is_ignored.json`).

---

## 5. `POST /finance/invoices/{id}/send` — issue it and email the client

No request body. Legal only from `draft`. Sets `issued_on` = today (UTC), `due_on` = `issued_on` + 15 /
30 / 0 days by `terms` (`net_15` / `net_30` / `due_on_receipt`), status `sent`, and queues the email.

**Response `200`** — `capture: invoice_sent.json` (`net_30`, run on 2026-09-30 → due 2026-10-30):

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": [
      {
        "description": "Design retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      },
      {
        "description": "Hosting setup",
        "quantity": 10,
        "unit_price_minor": 5000,
        "amount_minor": 50000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-09-30",
    "due_on": "2026-10-30",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.632523Z"
  },
  "meta": null
}
```

**Sending twice / sending a non-draft** → 422 (`capture: error_send_twice.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a draft invoice can be sent.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a draft invoice can be sent."
      }
    ]
  }
}
```

### The email the client receives (verified live)

The queued `email.invoice_sent` job was drained in-process and the delivered message read back out of the
**file** email backend (`EMAIL_BACKEND=file`). The journey asserts: exactly **one** email was written
to the invoice's `client_email`; the subject contains the invoice number; the body shows the total in
**major units** (`1,612.50 NGN`) and never the raw minor figure (`161250` / `161,250`). Verbatim —
`capture: invoice_email_delivered.json`:

```json
{
  "to": "delivered+inv-6636f79d668d@resend.dev",
  "subject": "Invoice INV-2026-001 from Cofoundaz Invoices",
  "html": "<div style='font-family:system-ui,sans-serif;max-width:560px'><h2>Invoice INV-2026-001</h2><p>From Cofoundaz Invoices</p><p>Bill to Acme Traders Ltd</p><table style='width:100%'><tr><td>Design retainer (x1)</td><td style='text-align:right'>1,000.00 NGN</td></tr><tr><td>Hosting setup (x10)</td><td style='text-align:right'>500.00 NGN</td></tr></table><p>Subtotal: 1,500.00 NGN<br>Tax: 112.50 NGN<br><strong>Total: 1,612.50 NGN</strong></p><p>Due: 2026-10-30</p></div>",
  "sent_at": "2026-09-30T08:32:55.667029+00:00"
}
```

Notes for the FE:
- Subject is `Invoice <number> from <workspace name>`. The sender/recipient are the server's concern;
  the FE never composes or previews this email.
- Amounts are **major units** with thousands separators and the ISO code suffix (`1,612.50 NGN`) —
  unlike every API field (minor units).
- ⚠️ **What was NOT verified live:** delivery through **real Resend**. The e2e uses the file backend
  (any address is accepted there); the Resend path is unit-tested at the handler level only
  (`tests/worker/test_invoice_email.py`, 7 tests: content, HTML-escaping of client-controlled fields,
  CR/LF stripping in the subject, empty-recipient no-op). The email is an **async worker job**
  (`email.invoice_sent`): in a deployment it is only sent while the worker process is running.
- The email is fire-and-forget: a bounce is invisible to the API. See Read-this-first (6).

**Sent invoices are frozen:** `DELETE` on a sent invoice → **422** (`capture:
error_delete_sent_invoice.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a draft invoice can be deleted.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a draft invoice can be deleted."
      }
    ]
  }
}
```

---

## 6. `POST /finance/invoices/{id}/mark-paid` — record payment

No request body. Legal from `sent` (including derived `overdue`). Marks the invoice `paid`, stamps
`paid_at`, and **books an inflow** into the ledger (amount = `total_minor`, same `currency`, category
`Revenue`, dated **today UTC**, description `Invoice <number> — <client_name>`, `source: "invoice"`),
linking it as `transaction_id`. Runs the runway alert/signal refresh in the same transaction.

**Response `200`** — `capture: invoice_marked_paid.json`:

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": [
      {
        "description": "Design retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      },
      {
        "description": "Hosting setup",
        "quantity": 10,
        "unit_price_minor": 5000,
        "amount_minor": 50000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "paid",
    "issued_on": "2026-09-30",
    "due_on": "2026-10-30",
    "paid_at": "2026-09-30T08:32:55.704271Z",
    "auto_remind": true,
    "transaction_id": "37220419-300c-4eb8-ae3b-0317af2ba13d",
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.701521Z"
  },
  "meta": null
}
```

**Idempotent.** A second `mark-paid` (double-tap, retry) returns **200 with the same `transaction_id`
and the same `paid_at`** and does **not** create a second inflow (`capture:
invoice_marked_paid_again.json`, line items trimmed):

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": "<\u2026same as above\u2026>",
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "paid",
    "issued_on": "2026-09-30",
    "due_on": "2026-10-30",
    "paid_at": "2026-09-30T08:32:55.704271Z",
    "auto_remind": true,
    "transaction_id": "37220419-300c-4eb8-ae3b-0317af2ba13d",
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.701521Z"
  },
  "meta": null
}
```

Cash on hand rose by the total **once**, not twice (below). Marking paid from `draft` → **422** (`capture:
error_mark_paid_on_draft.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a sent invoice can be marked paid.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a sent invoice can be marked paid."
      }
    ]
  }
}
```

An **overdue** invoice can be marked paid and then reads `paid` (verified live: `capture:
invoice_overdue_marked_paid.json`, status `paid`, fresh `transaction_id`).

### What happens to Cash Flow and Runway (verified live)

Before (`capture: cash_flow_before.json`, seeded ledger: a ₦1,000,000 raise 4 months ago and ₦120,000
hosting today; `by_month` shown in full):

```json
{
  "data": {
    "cash_on_hand": 88000000,
    "monthly_burn": 4000000,
    "monthly_revenue": 0,
    "runway_months": 22.0,
    "runway_low": false,
    "currency": "NGN",
    "by_month": [
      {
        "month": "2026-04",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-05",
        "inflow": 100000000,
        "outflow": 0,
        "net": 100000000
      },
      {
        "month": "2026-06",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-07",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-08",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-09",
        "inflow": 0,
        "outflow": 12000000,
        "net": -12000000
      }
    ]
  },
  "meta": null
}
```

After marking INV-2026-001 paid (`capture: cash_flow_after_paid.json`):

```json
{
  "data": {
    "cash_on_hand": 88161250,
    "monthly_burn": 3946250,
    "monthly_revenue": 53750,
    "runway_months": 22.3,
    "runway_low": false,
    "currency": "NGN",
    "by_month": [
      {
        "month": "2026-04",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-05",
        "inflow": 100000000,
        "outflow": 0,
        "net": 100000000
      },
      {
        "month": "2026-06",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-07",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-08",
        "inflow": 0,
        "outflow": 0,
        "net": 0
      },
      {
        "month": "2026-09",
        "inflow": 161250,
        "outflow": 12000000,
        "net": -11838750
      }
    ]
  },
  "meta": null
}
```

| Field | Before | After paid | Why |
|---|---|---|---|
| `cash_on_hand` | 88,000,000 | **88,161,250** | + the invoice total, exactly once |
| `monthly_revenue` | 0 | 53,750 | 161,250 ÷ 3 (trailing-3-calendar-month average, Slice 1) |
| `monthly_burn` | 4,000,000 | 3,946,250 | *net* burn: (12,000,000 − 161,250) ÷ 3 |
| `runway_months` | 22.0 | 22.3 | 88,161,250 ÷ 3,946,250 |
| `by_month[2026-09].inflow` | 0 | 161,250 | inflow lands in the **payment** month (today), not the issue month |

`GET /finance/runway` (Slice 2) moves the same way — `capture: runway_after_paid.json`
(`by_month` arrays omitted; the 12-month projection points are unchanged in shape). In this seed cash
never runs out inside the horizon, so all three scenarios' `runway_months` stay `null`; the visible
movement is in `baseline` and `avg_net_burn_minor`:

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 0,
      "hiring_spend_minor": 0,
      "one_off_costs_minor": 0
    },
    "baseline": {
      "cash_on_hand": 88161250,
      "monthly_burn": 3946250,
      "monthly_revenue": 53750,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 3946250,
        "by_month": "<12 points omitted>"
      },
      "best": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 3494638,
        "by_month": "<12 points omitted>"
      },
      "worst": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 4546250,
        "by_month": "<12 points omitted>"
      }
    }
  },
  "meta": null
}
```

(For comparison, `capture: runway_before.json` had `baseline` `{cash_on_hand: 88000000, monthly_burn:
4000000, monthly_revenue: 0}` and `base.avg_net_burn_minor` 4000000.)

---

## 7. The inflow in the transactions ledger — and why it's read-only

`GET /finance/transactions?direction=in` while paid — `capture: transactions_list_inflow.json`. Note
`"source": "invoice"` on the first row (the other is Slice 1's manual raise, `"source": "manual"`):

```json
{
  "data": {
    "transactions": [
      {
        "id": "37220419-300c-4eb8-ae3b-0317af2ba13d",
        "date": "2026-09-30",
        "description": "Invoice INV-2026-001 \u2014 Acme Traders Ltd",
        "category": "Revenue",
        "amount_minor": 161250,
        "currency": "NGN",
        "direction": "in",
        "source": "invoice",
        "created_at": "2026-09-30T08:32:55.701521Z",
        "updated_at": "2026-09-30T08:32:55.701521Z"
      },
      {
        "id": "4c37d85f-57ed-4dbf-a15f-24e263c1cdc4",
        "date": "2026-05-15",
        "description": "Pre-seed round",
        "category": "Fundraising",
        "amount_minor": 100000000,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-09-30T08:32:55.535412Z",
        "updated_at": "2026-09-30T08:32:55.535412Z"
      }
    ]
  },
  "meta": null
}
```

**Editing or deleting that row is refused** with 422 and an actionable message. `PATCH
/finance/transactions/{id}` with `{"category": "Other"}` — `capture:
error_patch_invoice_transaction.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This transaction is managed by an invoice; unpay the invoice to change or remove it.",
    "field_errors": [
      {
        "field": "source",
        "message": "This transaction is managed by an invoice; unpay the invoice to change or remove it."
      }
    ]
  }
}
```

`DELETE /finance/transactions/{id}` — `capture: error_delete_invoice_transaction.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This transaction is managed by an invoice; unpay the invoice to change or remove it.",
    "field_errors": [
      {
        "field": "source",
        "message": "This transaction is managed by an invoice; unpay the invoice to change or remove it."
      }
    ]
  }
}
```

> **FE instruction:** in the ledger, for any row with `source === "invoice"`, **do not render** the
> edit / categorize / delete controls (and don't offer inline categorize). Show a "From invoice
> INV-… — mark the invoice unpaid to change" affordance instead. Manual rows (`source === "manual"`)
> are unchanged from Slice 1 (⚠️ unit-verified: `test_manual_transactions_still_editable_and_deletable`).

---

## 8. `POST /finance/invoices/{id}/mark-unpaid` — reverse a payment

No request body. Legal only from `paid`. Returns the invoice to **`sent`**, clears `paid_at` and
`transaction_id`, and **deletes the inflow** it created, then refreshes the runway alert/signal.
`issued_on`/`due_on`/`auto_remind` are untouched. Response `200` — `capture: invoice_marked_unpaid.json`:

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": [
      {
        "description": "Design retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      },
      {
        "description": "Hosting setup",
        "quantity": 10,
        "unit_price_minor": 5000,
        "amount_minor": 50000
      }
    ],
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-09-30",
    "due_on": "2026-10-30",
    "paid_at": null,
    "auto_remind": true,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.806840Z"
  },
  "meta": null
}
```

Cash Flow is back to **exactly** its pre-payment payload (the journey asserts full-payload equality of
`GET /finance/cash-flow` and `GET /finance/runway` against the "before" captures) —
`capture: cash_flow_after_unpaid.json`, `capture: runway_after_unpaid.json` (`cash_on_hand` 88,000,000,
`monthly_burn` 4,000,000, `monthly_revenue` 0, `runway_months` 22.0) — and the ledger no longer contains
the inflow (`capture: transactions_list_inflow_removed.json`, which holds only the manual raise).

Marking unpaid from `draft` (or any non-paid state) → **422** (`capture: error_mark_unpaid_on_draft.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a paid invoice can be marked unpaid.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a paid invoice can be marked unpaid."
      }
    ]
  }
}
```

Marking paid again afterwards creates a **fresh** inflow and a new `transaction_id` (⚠️ unit-verified:
`test_mark_paid_again_after_unpaid_creates_fresh_single_inflow`; the live journey does mark-paid again on
the overdue invoice, which shows the same fresh-inflow behavior).

---

## 9. `overdue` — derived, verified live

The journey sent the invoice, marked it unpaid (back to `sent`), then **moved its stored `due_on` three
days into the past directly in the database** (no API can do this). It then read back as `overdue`.
`GET /finance/invoices/{id}` — `capture: invoice_get_overdue.json` (line items trimmed; `due_on` here is
the backdated value):

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": "<\u2026same as above\u2026>",
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "overdue",
    "issued_on": "2026-09-30",
    "due_on": "2026-09-27",
    "paid_at": null,
    "auto_remind": true,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.806840Z"
  },
  "meta": null
}
```

`GET /finance/invoices?status=overdue` returned exactly that invoice (`capture:
invoices_list_overdue.json`, same object shape, `status: "overdue"`), while `?status=sent` returned an
**empty** list (`capture: invoices_list_sent_excludes_overdue.json`: `{"data": {"invoices": []},
"meta": null}`). And `PATCH {"status": "paid"}` on it changed nothing — `status` stayed `"overdue"`,
HTTP 200 (`capture: patch_status_is_ignored.json`, line items trimmed):

```json
{
  "data": {
    "id": "efbcbabc-6735-4dce-aaf7-acea2384b0c8",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-6636f79d668d@resend.dev",
    "line_items": "<\u2026same as above\u2026>",
    "subtotal_minor": 150000,
    "tax_percent": 7.5,
    "tax_minor": 11250,
    "total_minor": 161250,
    "currency": "NGN",
    "terms": "net_30",
    "status": "overdue",
    "issued_on": "2026-09-30",
    "due_on": "2026-09-27",
    "paid_at": null,
    "auto_remind": true,
    "transaction_id": null,
    "created_at": "2026-09-30T08:32:55.588896Z",
    "updated_at": "2026-09-30T08:32:55.806840Z"
  },
  "meta": null
}
```

> **FE instruction:** render the overdue badge from the returned `status`. Do not send a status on
> PATCH; there is no "mark overdue" action. "Today" is **UTC**, so an invoice due "today" in a
> UTC-behind timezone can flip to `overdue` a few hours early from the user's point of view; an invoice
> is overdue only when `due_on` is *before* today (due today = not yet overdue).

---

## 10. `DELETE /finance/invoices/{id}` — delete a draft

Legal only for `draft`. **Response `200`** — `capture: invoice_deleted.json`:

```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

It is a hard delete; a subsequent `GET` is **404** (`capture: error_get_deleted_invoice.json`):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

Deleting a sent/overdue/paid invoice → 422 (§5). Deletes are immediate — confirm before calling.

---

## Errors

All errors use the standard envelope (no `data`/`meta`). Bodies below are from live captures unless
marked ⚠️.

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | missing/invalid access token | `error_unauthenticated.json` |
| 403 | `EMAIL_NOT_VERIFIED` | email not verified | ⚠️ not captured |
| 403 | `FORBIDDEN` | membership role is not founder / team_member / accountant | ⚠️ not captured (unit) |
| 404 | `NOT_FOUND` | unknown id, deleted invoice, or another workspace's invoice | `error_unknown_invoice.json`, `error_get_deleted_invoice.json` |
| 422 | `VALIDATION_ERROR` | request-shape errors *and* illegal state transitions | `error_create_validation.json`, `error_*` above |

**401** — `capture: error_unauthenticated.json`:

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

**422 (request shape)** — the journey posts `client_email: "not-an-email"`, `line_items: []`,
`tax_percent: 150` and gets **one `field_errors` entry per bad field** (`capture:
error_create_validation.json`). Use `field` to attach the message to an input; **do not string-match
`message`**:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "client_email",
        "message": "value is not a valid email address: An email address must have an @-sign."
      },
      {
        "field": "line_items",
        "message": "List should have at least 1 item after validation, not 0"
      },
      {
        "field": "tax_percent",
        "message": "Input should be less than or equal to 100"
      }
    ]
  }
}
```

**422 (illegal transition)** — same code, and `field_errors[0].field` is `"status"` with a specific
message. The exact strings the server returns (all captured live above):

| Action | Exact `message` |
|---|---|
| PATCH a non-draft (anything but `auto_remind`) | `Only a draft invoice can be edited.` |
| DELETE a non-draft | `Only a draft invoice can be deleted.` |
| Send a non-draft | `Only a draft invoice can be sent.` |
| Mark-paid a draft | `Only a sent invoice can be marked paid.` |
| Mark-unpaid a non-paid | `Only a paid invoice can be marked unpaid.` |
| PATCH/DELETE an invoice-managed transaction (`field` = `"source"`) | `This transaction is managed by an invoice; unpay the invoice to change or remove it.` |

Also 422 (⚠️ unit-verified, bodies not captured): subtotal or total over 2,147,483,647 (`field`
`line_items`, *"Invoice subtotal exceeds the maximum allowed amount."* / *"Invoice total exceeds the
maximum allowed amount."*), explicit `null` on a PATCH field, invalid `terms`, empty `line_items` on PATCH.
The `message` for request-shape errors is the constant `"Please check the highlighted fields."`; the
per-field text lives in `field_errors[].message`. `NOT_FOUND` uses `"Not found."` with an **empty**
`field_errors` array.

---

## Payments update: `pay_url`, `pay_provider`, `paid_via`, pay-link, and the mark-unpaid rule

> Source: `e2e/test_finance_payments.py` (live app, provider HTTP stubbed inside the server; see
> `docs/fe-integration-guide-finance-payments.md` for the full provider/connection contract and the
> verification table). The invoice bodies in the sections above were captured **before** these three
> fields existed; the invoices journey was re-run afterwards and every invoice object in
> `e2e/_captures/invoices/` now carries all three as `null`, for example
> `capture: invoices/invoice_created.json` (re-captured):

```json
{
  "data": {
    "id": "8f5a3ee8-cf17-4535-9b2a-51f4d4a3c218",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+inv-1c5397959668@resend.dev",
    "line_items": [
      {
        "description": "Design retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      },
      {
        "description": "Hosting setup",
        "quantity": 5,
        "unit_price_minor": 5000,
        "amount_minor": 25000
      }
    ],
    "subtotal_minor": 125000,
    "tax_percent": 7.5,
    "tax_minor": 9375,
    "total_minor": 134375,
    "currency": "NGN",
    "terms": "net_30",
    "status": "draft",
    "issued_on": null,
    "due_on": null,
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "created_at": "2026-10-21T13:02:19.965326Z",
    "updated_at": "2026-10-21T13:02:19.965326Z"
  },
  "meta": null
}
```

**What changed for the FE**

- Every invoice object has `pay_url`, `pay_provider` and `paid_via` (present, `null` when unused).
  Values in the older example bodies above that omit them are stale by exactly these three keys.
- `POST …/send` creates the pay link when the invoice is NGN and a payment connection is `active`.
  A failure to create it never fails the send: the invoice is `sent` with `pay_url: null`.
  `capture: finance_payments/invoice_sent_with_pay_link.json`

```json
{
  "data": {
    "id": "ccf760dc-4b4f-4807-affb-c995df34303d",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+pay-da3a1aced2c5@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "pay_url": "https://checkout.paystack.com/stub-INV-2026-001-bfc32f9c",
    "pay_provider": "paystack",
    "paid_via": null,
    "created_at": "2026-10-21T13:06:58.857359Z",
    "updated_at": "2026-10-21T13:06:58.883725Z"
  },
  "meta": null
}
```

- `POST /finance/invoices/{id}/pay-link` (founder, team_member, accountant) creates the link if it is
  missing and otherwise returns the existing one. Returns the invoice. `capture: finance_payments/pay_link_reused.json`
  and, for a retry that created one, `capture: finance_payments/pay_link_retry_flutterwave.json`
  (see the payments guide for both bodies). Errors: draft invoice 422
  (`capture: finance_payments/error_pay_link_on_draft.json`), no connection 422
  (`capture: finance_payments/error_pay_link_no_connection.json`), mentor 403.

```json
{
  "error": {
    "code": "NO_PAYMENT_CONNECTION",
    "message": "Connect Paystack or Flutterwave before creating a pay link.",
    "field_errors": []
  }
}
```

- When the provider's webhook pays the invoice: `status: "paid"`, `paid_via` set, and one income row with
  `source: "invoice"`. `capture: finance_payments/invoice_paid_via_paystack.json`

```json
{
  "data": {
    "id": "ccf760dc-4b4f-4807-affb-c995df34303d",
    "number": "INV-2026-001",
    "client_name": "Acme Traders Ltd",
    "client_email": "delivered+pay-da3a1aced2c5@resend.dev",
    "line_items": [
      {
        "description": "Retainer",
        "quantity": 1,
        "unit_price_minor": 100000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 0.0,
    "tax_minor": 0,
    "total_minor": 100000,
    "currency": "NGN",
    "terms": "net_30",
    "status": "paid",
    "issued_on": "2026-10-21",
    "due_on": "2026-11-20",
    "paid_at": "2026-10-21T13:06:59Z",
    "auto_remind": false,
    "transaction_id": "87ea0a9c-6246-4deb-a10b-26bf4c1facb1",
    "pay_url": "https://checkout.paystack.com/stub-INV-2026-001-bfc32f9c",
    "pay_provider": "paystack",
    "paid_via": "paystack",
    "created_at": "2026-10-21T13:06:58.857359Z",
    "updated_at": "2026-10-21T13:07:00.012653Z"
  },
  "meta": null
}
```

- **Mark unpaid is refused on a provider-paid invoice** (otherwise the next sync would pay it again).
  Hide the action when `paid_via` is set. `capture: finance_payments/error_mark_unpaid_provider_paid.json`

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This invoice was paid through Paystack and cannot be marked unpaid.",
    "field_errors": [
      {
        "field": "status",
        "message": "This invoice was paid through Paystack and cannot be marked unpaid."
      }
    ]
  }
}
```

  Same rule for Flutterwave with its own name in the message (`error_mark_unpaid_provider_paid_flutterwave.json`).
  A manual **mark-paid** on a provider-paid invoice stays an idempotent no-op (200).

- The client email now contains a **Pay now** button with the link when `pay_url` is set
  (`capture: finance_payments/invoice_email_with_pay_link.json`, file mailbox, not real Resend).

**Trap:** `paid_via` is set only by the provider path. Manual `mark-paid` leaves it `null`, so a paid
invoice with `paid_via: null` was marked paid by hand.

**Not verified live:** real Paystack/Flutterwave checkout pages and real webhook delivery (the provider side
was stubbed); the non-NGN pay-link 422 message and the 502 `PAYMENT_PROVIDER_UNAVAILABLE`
(source only). See the payments guide's verification table.

---

## UX consequences the FE must surface

- **Live-preview, then trust the server.** Re-render totals from every response. A rounding
  difference of 1 minor unit between your preview and the server is possible; the server is right.
- **Marking paid changes Cash Flow and Runway.** After `mark-paid`/`mark-unpaid`, refetch cash-flow and
  runway (and the transactions ledger). A "Mark paid" confirmation should say it will add the amount to
  cash on hand *today*.
- **Hide edit/delete on `source == "invoice"` ledger rows** (§7) and explain how to undo (unpay).
- **Don't block on the email.** Show "Invoice sent" immediately. Don't promise delivery.
- **Say what the client will see.** The client email shows major units and the workspace name; consider
  a "Send invoice to `client_email`?" confirm step since send is one-shot and irreversible (a sent
  invoice cannot be recalled or edited).
- **Overdue is a badge, not a control.** Never let the user pick it; it appears/disappears with the date
  (an unpaid invoice that was overdue stays overdue after mark-unpaid).
- **Mark-paid is safe to double-tap** (idempotent), but `send` is not guarded against a double-tap
  (see limitations) — disable the Send button after the first click until the response arrives.
- **"Download PDF" uses the server.** `GET /finance/invoices/{id}/pdf` (automation guide); fetch it with the auth headers.
- **Deletes are hard** and only for drafts.

## Known limitations (be honest with users)

| Limitation | Effect |
|---|---|
| **Single-currency assumption.** Currencies are not converted (same as Slices 1–2). | An invoice in a non-workspace currency, once paid, adds its **raw minor units** into `cash_on_hand` and every cash-flow/runway figure as if it were the workspace currency. Don't let users mix currencies until conversion ships |
| **`currency` is NGN only.** | Other values are a 422; no currency picker |
| ~~No reminder / overdue scheduler yet.~~ Superseded. | A daily scan now sends the overdue notification and automatic reminders (automation guide) |
| ~~No server-side PDF.~~ Superseded. | `GET /finance/invoices/{id}/pdf` |
| **No Client/CRM entity.** | `client_name` / `client_email` are free text per invoice |
| **`send`, `PATCH`, `DELETE` use an unlocked read** (`mark-paid` / `mark-unpaid` **do** lock the row). | A rapid double-click on Send could queue **two emails** (the invoice itself only flips once); two concurrent PATCHes can lose an update. Disable buttons while a request is in flight |
| **No notification on send.** | `finance.invoice.overdue` and `finance.invoice.paid` now fire (automation guide); sending an invoice still creates none |
| **List is unpaginated; ordering is `created_at` desc.** | Fine at current volumes |
| **`number` year is the UTC creation year;** invoice numbers can be reused if the newest draft is deleted. | Don't treat `number` as a permanent external key — use `id` |

---

## Verification

Legend: ✅ **verified live** = asserted in `e2e/test_invoices.py` against the running app and the body is
in `e2e/_captures/invoices/`. ⚠️ **unit-only** = asserted by the named unit test; not exercised live.

| Claim | Status | Source |
|---|---|---|
| `POST /invoices` → 200, 22-field body (19 + `pay_url`, `pay_provider`, `paid_via`, all `null` on a draft), `number` `INV-<year>-001`, status `draft`, server-computed subtotal/tax/total (125,000 / 9,375 / 134,375) | ✅ verified live | `invoice_created.json` |
| Client-supplied `total_minor` (=1) ignored | ✅ verified live | `invoice_created.json` (request carried `total_minor: 1`) |
| Second invoice gets `INV-<year>-002` | ✅ verified live | `invoice_created_second_draft.json` |
| `PATCH` draft line items → totals recompute (150,000 / 11,250 / 161,250) | ✅ verified live | `invoice_edited.json` |
| Tax-only PATCH recomputes from existing lines; client-field PATCH leaves totals alone | ⚠️ unit-only | `test_edit_draft_tax_only_recomputes_from_existing_lines`, `test_edit_draft_client_fields_leave_totals_alone` |
| Half-up tax rounding | ⚠️ unit-only | `test_tax_rounds_half_up` |
| Number retry on stale/duplicate number (SAVEPOINT) | ⚠️ unit-only | `test_number_retry_recovers_from_stale_number`, `test_invoice_number_unique_per_startup` |
| Subtotal/total over int32 → 422 | ⚠️ unit-only | `test_overflowing_total_is_domain_422` |
| Explicit `null` / invalid `terms` / empty `line_items` on PATCH → 422 | ⚠️ unit-only | `test_patch_explicit_null_rejected`, `test_patch_invalid_terms_and_empty_items_422` |
| Request-shape 422: per-field `field_errors` (email, empty lines, tax > 100) | ✅ verified live | `error_create_validation.json` |
| `POST …/send` → `sent`, `issued_on` = today, `due_on` = today + 30 | ✅ verified live | `invoice_sent.json` |
| `due_on` for `net_15` / `due_on_receipt` | ⚠️ unit-only | `test_send_sets_dates_status_and_enqueues` (+ `due_on_receipt` invoice created live, not sent) |
| Send twice → 422 "Only a draft invoice can be sent." | ✅ verified live | `error_send_twice.json` |
| Invoice email written to `client_email`: subject has number, total in **major units** (`1,612.50 NGN`), not raw minor | ✅ verified live (**file email backend**) | `invoice_email_delivered.json` |
| Email delivered through **real Resend** | ⚠️ not verified (file backend only; handler unit-tested) | `tests/worker/test_invoice_email.py` |
| Email HTML-escapes client-controlled fields; subject strips CR/LF | ⚠️ unit-only | `test_html_escapes_user_controlled_fields`, `test_subject_strips_crlf_from_startup_name` |
| Sent invoice: PATCH (non-`auto_remind`) and DELETE → 422 with exact messages | ✅ verified live | `error_patch_sent_invoice.json`, `error_delete_sent_invoice.json` |
| `auto_remind` editable after send | ✅ verified live | `invoice_auto_remind_after_send.json` |
| `mark-paid` → `paid`, `paid_at` + `transaction_id` set | ✅ verified live | `invoice_marked_paid.json` |
| `mark-paid` idempotent (same `transaction_id`/`paid_at`, single inflow) | ✅ verified live | `invoice_marked_paid_again.json` + cash-flow +161,250 once |
| Mark-paid on a draft → 422; mark-unpaid on non-paid → 422 | ✅ verified live | `error_mark_paid_on_draft.json`, `error_mark_unpaid_on_draft.json` |
| Cash-flow: `cash_on_hand` +total, `monthly_revenue`, `monthly_burn`, `runway_months` move | ✅ verified live | `cash_flow_before.json` → `cash_flow_after_paid.json` |
| Runway (`GET /finance/runway`) baseline + `avg_net_burn_minor` move | ✅ verified live | `runway_before.json` → `runway_after_paid.json` |
| `mark-unpaid` → `sent`, link cleared, inflow deleted, cash-flow AND runway payloads identical to "before" | ✅ verified live | `invoice_marked_unpaid.json`, `cash_flow_after_unpaid.json`, `runway_after_unpaid.json`, `transactions_list_inflow_removed.json` |
| Paid inflow in ledger: `source: "invoice"`, category `Revenue`, dated today, description `Invoice <no> — <client>` | ✅ verified live | `transactions_list_inflow.json` |
| PATCH / DELETE on the invoice-managed transaction → 422 "unpay the invoice instead" | ✅ verified live | `error_patch_invoice_transaction.json`, `error_delete_invoice_transaction.json` |
| Manual transactions still editable/deletable (guard is source-specific) | ⚠️ unit-only | `test_manual_transactions_still_editable_and_deletable` |
| Mark-paid/unpaid refresh the runway alert + `money.runway_live` signal | ⚠️ unit-only (live run did not cross the low-runway threshold) | `test_mark_paid_and_unpaid_refresh_runway_alert_and_signal` |
| Mark-paid/unpaid take a row lock (concurrency) | ⚠️ unit-only | `test_money_paths_take_a_row_lock` |
| List filters `paid` / `draft` / `overdue` (empty and populated) / all; `sent` excludes overdue; bad value → 422 | ✅ verified live | `invoices_list_*.json`, `error_list_bad_status.json` |
| List order newest-first | ✅ verified live (`INV-…-002` before `-001`) / ⚠️ tie-break by `number` unit-only | `invoices_list_all.json`, `test_list_newest_first_and_status_filter` |
| `GET` one; unknown id → 404; deleted → 404 | ✅ verified live | `invoice_get_one.json`, `error_unknown_invoice.json`, `error_get_deleted_invoice.json` |
| `DELETE` draft → 200 `{"deleted": true}` | ✅ verified live | `invoice_deleted.json` |
| `overdue` derived on read (GET + list), not stored | ✅ verified live (**`due_on` backdated directly in the DB**) | `invoice_get_overdue.json`, `invoices_list_overdue.json` |
| `PATCH {"status": …}` ignored; overdue invoice can be marked paid | ✅ verified live | `patch_status_is_ignored.json`, `invoice_overdue_marked_paid.json` |
| Overdue boundary: due **today** is not overdue; only a stored-`sent` invoice can be overdue | ⚠️ from source only (`_derived_status`: `sent` and `due_on < today`); the unit test covers overdue-vs-draft, not the due-today boundary | `app/services/finance/invoices.py::_derived_status`, `test_overdue_derivation_and_filter` |
| 401 unauthenticated | ✅ verified live | `error_unauthenticated.json` |
| RBAC: founder / team_member / accountant allowed; other roles → 403 | ⚠️ unit-only | `test_rbac_forbidden`, `test_accountant_allowed`, `test_lifecycle_rbac_forbidden`, `test_mark_paid_accountant_allowed`, `test_mark_paid_unpaid_rbac_forbidden` |
| Cross-workspace access → 404 (get/patch/delete/send/mark-paid/mark-unpaid/list exclusion) | ⚠️ unit-only | `test_get_one_and_cross_tenant_404`, `test_cross_tenant_*` |
| Mixed-currency invoice summed into cash-on-hand without conversion | ⚠️ from source only (no test exercises it) | `app/services/finance/invoices.py::mark_paid`, `cashflow.py` |
| `currency` NGN only (D-FIN-6) | verified live in the automation guide | `error_create_non_ngn_currency.json` |
| Reminder / overdue scheduler | superseded: verified live in the automation guide | `fe-integration-guide-finance-invoices-automation.md` |
| 403 / `EMAIL_NOT_VERIFIED` error bodies | ⚠️ not captured | `app/core/errors.py` |
