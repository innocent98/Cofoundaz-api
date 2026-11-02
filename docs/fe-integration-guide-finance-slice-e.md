# FE Integration Guide — Finance slice (e): runway reads the model, invoice extras, Advisor, XLSX, missing receipts

> **Provenance.** `e2e/test_finance_slice_e.py::test_finance_slice_e_journey` ran against the live
> app (`scripts/e2e_run.sh` flow: real uvicorn, fresh migrated database, `EMAIL_BACKEND=file`,
> `LLM_PROVIDER=stub`) and every **response body** below is pasted **verbatim** from
> `e2e/_captures/finance_slice_e/*.json`. Two labelled edits: (a) in the runway capture each
> scenario's `by_month` is cut to 2 points plus an `"<... N more points elided ...>"` string that
> is **not** in the real response; (b) nothing else is trimmed. Request bodies are the exact JSON
> the journey sends (read from the test), not captures. Ids, emails and timestamps are throwaway
> values from a disposable database: **placeholders**. Auth headers are written as
> `<access_token>` / `<workspace-id>`. What could not be exercised live is marked
> **NOT VERIFIED LIVE** inline and in the table at the end.

Base path `/api/v1/finance`. Every route needs `Authorization: Bearer <access_token>` and
`X-Workspace-Id: <workspace-id>`. Roles: `founder`, `team_member`, `accountant` (Finance roles);
any other role gets **403** (`FORBIDDEN`, captured in §4).

---

## 1. Runway now reads the Financial Model's rows (D-FIN-12)

Hiring and one-off rows have **one home: the Financial Model** (`PATCH /finance/model/{id}/assumptions`).
The runway has no rows of its own; its projection reads the newest **complete** model's rows
(with the founder's edits applied). A model that is `generating` or `failed` is skipped, so a
regeneration in flight never blanks the runway.

**What the FE does:** keep the three scalar inputs (growth %, hiring spend, one-off costs) as they
are. Add a read-only "From your financial model" block listing `model_rows.hiring_plan` and
`model_rows.one_offs` with a link to `model_rows.href` (`/finance/model`). Do **not** build a row
editor on the runway page.

### 1a. `GET /finance/runway` — new `model_rows` block

No model yet (`runway_no_model.json`, `data.model_rows` only; the rest of the body is unchanged
from the runway guide):

```json
{
  "model_id": null,
  "href": "/finance/model",
  "hiring_plan": [],
  "one_offs": []
}
```

With a model (`runway_with_model_rows.json`, `by_month` elided as stated above):

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 5,
      "hiring_spend_minor": 0,
      "one_off_costs_minor": 0
    },
    "model_rows": {
      "model_id": "d90b5f09-f5d8-4083-84c5-14f51059ce0a",
      "href": "/finance/model",
      "hiring_plan": [
        {
          "role": "Backend engineer",
          "start_month": "2026-11",
          "monthly_cost_minor": 400000
        }
      ],
      "one_offs": [
        {
          "label": "Laptops",
          "month": "2026-12",
          "amount_minor": 250000
        }
      ]
    },
    "baseline": {
      "cash_on_hand": 74000000,
      "monthly_burn": 8666667,
      "monthly_revenue": 2000000,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": 8.7,
        "cash_out_date": "2027-06",
        "avg_net_burn_minor": 8302003,
        "by_month": [
          {
            "month": "2026-11",
            "cash_balance": 65033333,
            "net": -8966667
          },
          {
            "month": "2026-12",
            "cash_balance": 55921666,
            "net": -9111667
          },
          "<... 10 more points elided ...>"
        ]
      },
      "best": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 4462180,
        "by_month": [
          {
            "month": "2026-11",
            "cash_balance": 66300000,
            "net": -7700000
          },
          {
            "month": "2026-12",
            "cash_balance": 58695000,
            "net": -7605000
          },
          "<... 10 more points elided ...>"
        ]
      },
      "worst": {
        "runway_months": 6.9,
        "cash_out_date": "2027-04",
        "avg_net_burn_minor": 10687500,
        "by_month": [
          {
            "month": "2026-11",
            "cash_balance": 63333333,
            "net": -10666667
          },
          {
            "month": "2026-12",
            "cash_balance": 52416666,
            "net": -10916667
          },
          "<... 10 more points elided ...>"
        ]
      }
    }
  },
  "meta": null
}
```

Rules the captures prove (asserted by the journey):

* Month 1 (`2026-11`) `net` is lower than the same month without the model by the hire's
  `monthly_cost_minor`; month 2 is lower by the hire **plus** the one-off (`2026-12`); month 3 only
  by the hire (a hire keeps costing every month from `start_month`, a one-off is a single month).
* `model_rows.model_id` is `null` and both lists are `[]` when the workspace has no complete model.
* Field-nesting trap: `model_rows` sits at the top level of `data`, **next to** `assumptions`,
  not inside it. `assumptions` is still only the three scalars.
* Hires are not scaled by the best/worst cost multiplier; one-offs and hires apply to all three
  scenarios. Model `capex` rows are **not** read (runway models operating cash only).

### 1b. `PUT /finance/runway/assumptions` — now strict (breaking for any client that sent extras)

Request `{"mom_growth_percent": 7, "hiring_plan": [...], "one_offs": []}` →
**422** (`runway_put_rejects_rows.json`; nothing is saved, not even `mom_growth_percent`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "hiring_plan",
        "message": "Value error, The runway reads hiring and one-off rows from the Financial Model; edit them there (PATCH /finance/model/{model_id}/assumptions)."
      },
      {
        "field": "one_offs",
        "message": "Value error, The runway reads hiring and one-off rows from the Financial Model; edit them there (PATCH /finance/model/{model_id}/assumptions)."
      }
    ]
  }
}
```

Any other unknown field → **422** (`runway_put_rejects_unknown.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "burn_rate",
        "message": "Extra inputs are not permitted"
      }
    ]
  }
}
```

Valid request `{"mom_growth_percent": 5}` → 200, the full runway (`runway_put_ok.json`, same shape
as GET, `by_month` elided):

```json
{
  "data": {
    "assumptions": {
      "mom_growth_percent": 5,
      "hiring_spend_minor": 0,
      "one_off_costs_minor": 0
    },
    "model_rows": {
      "model_id": null,
      "href": "/finance/model",
      "hiring_plan": [],
      "one_offs": []
    },
    "baseline": {
      "cash_on_hand": 74000000,
      "monthly_burn": 8666667,
      "monthly_revenue": 2000000,
      "currency": "NGN"
    },
    "horizon_months": 12,
    "scenarios": {
      "base": {
        "runway_months": 9.2,
        "cash_out_date": "2027-07",
        "avg_net_burn_minor": 7881170,
        "by_month": [
          {
            "month": "2026-11",
            "cash_balance": 65433333,
            "net": -8566667
          },
          {
            "month": "2026-12",
            "cash_balance": 56971666,
            "net": -8461667
          },
          "<... 10 more points elided ...>"
        ]
      },
      "best": {
        "runway_months": null,
        "cash_out_date": null,
        "avg_net_burn_minor": 4041347,
        "by_month": [
          {
            "month": "2026-11",
            "cash_balance": 66700000,
            "net": -7300000
          },
          {
            "month": "2026-12",
            "cash_balance": 59745000,
            "net": -6955000
          },
          "<... 10 more points elided ...>"
        ]
      },
      "worst": {
        "runway_months": 7.2,
        "cash_out_date": "2027-05",
        "avg_net_burn_minor": 10266667,
        "by_month": [
          {
            "month": "2026-11",
            "cash_balance": 63733333,
            "net": -10266667
          },
          {
            "month": "2026-12",
            "cash_balance": 53466666,
            "net": -10266667
          },
          "<... 10 more points elided ...>"
        ]
      }
    }
  },
  "meta": null
}
```

**UX instruction:** the FE must never send `hiring_plan`, `one_offs` or `capex` to this route.
Before this release the API answered 200 and silently dropped them; a client that still sends them
will now get the 422 above. `capex` is refused with the same message.

---

## 2. Invoices: client picker, notes, payment details, Resend

New optional fields on **create** (`POST /finance/invoices`) and **draft update**
(`PATCH /finance/invoices/{id}`), and on every invoice response:

| Field | Type | Notes |
|---|---|---|
| `account_id` | uuid or null | A Sales account of **this** workspace (the client picker lists `GET /sales/accounts`). Another workspace's id and an unknown id both answer the same 422 on `account_id`. |
| `account_name` | string or null | Response only. `null` when no client is linked, or when the account was deleted later (the invoice survives; `account_id` becomes `null`). |
| `notes` | string or null | Up to 2000 chars; leading/trailing whitespace trimmed; blank becomes `null`. Shown on the PDF and in the email. |
| `payment_details` | object or null | `{bank_name, account_name, account_number, instructions?}`; all but `instructions` required; unknown keys rejected. Shown on the PDF and in the email. |
| `last_sent_at` | datetime or null | Response only: when the email last went out (first send or resend). |

`client_name` and `client_email` are **still required**: the picker prefills them from the account
and contact, but the invoice keeps its own copy (an account rename never rewrites an issued invoice).

Create with all three (`invoice_create.json`, request body = the journey's `body`):

```json
{
  "data": {
    "id": "302e851d-a637-4962-80fe-1d2dbdde2b45",
    "number": "INV-2026-001",
    "client_name": "Acme Logistics",
    "client_email": "delivered+client-9da498e5604f@resend.dev",
    "line_items": [
      {
        "description": "Design sprint",
        "quantity": 2,
        "unit_price_minor": 50000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 7.5,
    "tax_minor": 7500,
    "total_minor": 107500,
    "currency": "NGN",
    "terms": "net_30",
    "status": "draft",
    "issued_on": null,
    "due_on": null,
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "account_id": "88422f12-3e75-4bdf-8cc1-3abff80df328",
    "account_name": "Acme Logistics",
    "notes": "Thank you for your business.\nPlease quote the invoice number.",
    "payment_details": {
      "bank_name": "GTBank",
      "account_name": "NovaPay Ltd",
      "account_number": "0123456789",
      "instructions": "Use the invoice number as the reference."
    },
    "last_sent_at": null,
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": null,
    "reminder_count": 0,
    "overdue_notified_at": null,
    "created_at": "2026-10-24T19:50:26.687416Z",
    "updated_at": "2026-10-24T19:50:26.687416Z"
  },
  "meta": null
}
```

Unknown / foreign `account_id` → **422** (`invoice_unknown_account.json`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That client was not found in this workspace.",
    "field_errors": [
      {
        "field": "account_id",
        "message": "That client was not found in this workspace."
      }
    ]
  }
}
```

* On **PATCH**, `account_id`, `notes` and `payment_details` accept an explicit `null` to clear
  them (unlike the other invoice fields). Omitted fields are untouched.
* They freeze once the invoice is sent, like the commercial terms: `PATCH` of anything but
  `auto_remind` on a non-draft is **422** on `status`, "Only a draft invoice can be edited."
  (`invoice_patch_after_send.json`):

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

### 2a. `POST /finance/invoices/{id}/resend`

Emails the invoice (PDF attached, pay link and bank details in the body) to the client again. No
request body. Allowed only for a **sent** invoice, which includes an overdue one (overdue is a
derived status). Returns the invoice with `last_sent_at` updated (`invoice_resend.json`):

```json
{
  "data": {
    "id": "302e851d-a637-4962-80fe-1d2dbdde2b45",
    "number": "INV-2026-001",
    "client_name": "Acme Logistics",
    "client_email": "delivered+client-9da498e5604f@resend.dev",
    "line_items": [
      {
        "description": "Design sprint",
        "quantity": 2,
        "unit_price_minor": 50000,
        "amount_minor": 100000
      }
    ],
    "subtotal_minor": 100000,
    "tax_percent": 7.5,
    "tax_minor": 7500,
    "total_minor": 107500,
    "currency": "NGN",
    "terms": "net_30",
    "status": "sent",
    "issued_on": "2026-10-24",
    "due_on": "2026-11-23",
    "paid_at": null,
    "auto_remind": false,
    "transaction_id": null,
    "account_id": "88422f12-3e75-4bdf-8cc1-3abff80df328",
    "account_name": "Acme Logistics",
    "notes": "Thank you for your business.\nPlease quote the invoice number.",
    "payment_details": {
      "bank_name": "GTBank",
      "account_name": "NovaPay Ltd",
      "account_number": "0123456789",
      "instructions": "Use the invoice number as the reference."
    },
    "last_sent_at": "2026-10-24T19:50:26.761893Z",
    "pay_url": null,
    "pay_provider": null,
    "paid_via": null,
    "last_reminded_at": null,
    "reminder_count": 0,
    "overdue_notified_at": null,
    "created_at": "2026-10-24T19:50:26.687416Z",
    "updated_at": "2026-10-24T19:50:26.760622Z"
  },
  "meta": null
}
```

The email the client received (`invoice_resend_email.json`, the file-backend capture of the real
rendered message; note the notes and the bank block):

```json
{
  "to": "delivered+client-9da498e5604f@resend.dev",
  "subject": "Invoice INV-2026-001 from NovaPay",
  "html": "<div style='font-family:system-ui,sans-serif;max-width:560px'><h2>Invoice INV-2026-001</h2><p>From NovaPay</p><p>Bill to Acme Logistics</p><table style='width:100%'><tr><td>Design sprint (x2)</td><td style='text-align:right'>1,000.00 NGN</td></tr></table><p>Subtotal: 1,000.00 NGN<br>Tax: 75.00 NGN<br><strong>Total: 1,075.00 NGN</strong></p><p>Due: 2026-11-23</p><p><strong>Pay by bank transfer</strong><br>Bank: GTBank<br>Account name: NovaPay Ltd<br>Account number: 0123456789<br>Use the invoice number as the reference.</p><p>Thank you for your business.<br>Please quote the invoice number.</p></div>",
  "sent_at": "2026-10-24T19:50:26.772428+00:00",
  "attachments": [
    "invoice-INV-2026-001.pdf"
  ]
}
```

Errors:

| Case | Status | Body |
|---|---|---|
| Resent less than 60 s after the last send | **429** | `invoice_resend_too_soon.json` below |
| Draft, or already paid | **422** on `status` | `invoice_resend_draft.json` below |
| Non-Finance role | **403** | `FORBIDDEN` (as §4) |
| Other workspace's invoice / unknown id | **404** | `NOT_FOUND` |
| More than 10 resends a minute per user | **429** | the app's standard rate-limit body (**NOT VERIFIED LIVE**: e2e runs with the limiter out of the way; unit-tested) |

```json
{
  "error": {
    "code": "RESEND_TOO_SOON",
    "message": "This invoice was emailed a moment ago. Try again in 60 seconds.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a sent, unpaid invoice can be resent.",
    "field_errors": [
      {
        "field": "status",
        "message": "Only a sent, unpaid invoice can be resent."
      }
    ]
  }
}
```

**UX instructions:** the 429 message contains the seconds to wait; show it as-is and keep the
button enabled afterwards. Show a "Resend" action on sent and overdue invoices only. After a
resend, update the row from the response (`last_sent_at`) and show "Sent to {client_email}"; the
email itself is queued, so a delivery failure is not reported by this call.

---

## 3. Advisor finance context (no new endpoint)

The AI Co-Founder chat now gets a finance paragraph in its system prompt: cash on hand, monthly
burn, monthly revenue (3-month average, **Funding excluded**, D-FIN-11), runway months, and the
count and total of overdue invoices. It is added **only when the person asking is an active member
with a Finance role** (the same set as the Finance routes). Mentors, investors, consultants and
non-members get no finance figures. The paragraph contains numbers only, no client names, vendors
or descriptions.

Real paragraph, probed in-process against the live e2e database right after the chat ran
(`advisor_finance_paragraph.json`; `mentor` is `null`):

```json
{
  "founder": "Finance snapshot (from their ledger, use it when relevant, never invent other figures): cash on hand NGN 740,000; monthly burn NGN 86,667; monthly revenue NGN 20,000 (3-month average, funding rounds excluded); runway 8.5 months; overdue invoices 0 totalling NGN 0.",
  "mentor": null
}
```

The chat endpoints are unchanged (`POST /ai/conversations`, `POST /ai/conversations/{id}/messages`).
**FE consequence:** nothing to build. The advisor may now quote the founder's real runway, so the
first-message seed the FE sends from the Finance page can be shortened to a question.
**NOT VERIFIED LIVE:** the model's *reply* (the e2e stub LLM does not read the prompt); that the
prompt carries the paragraph is proven by unit tests on the captured system message.

---

## 4. XLSX exports

Both return the file itself (not the JSON envelope): `200`, `Content-Type:
application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `Content-Disposition:
attachment`, `Cache-Control: private, no-store`. Rate limit 10 per minute per user (**NOT VERIFIED
LIVE**). The FE must download with the auth headers (a plain `<a href>` carries none): fetch as a
blob, then save with the `Content-Disposition` filename.

| Route | File | Contents |
|---|---|---|
| `GET /finance/transactions/export.xlsx` | `transactions.xlsx` | One sheet `Transactions`: `Date, Description, Category, Direction, Amount, Currency, Source`, newest first. Accepts the same filters as the list: `category`, `uncategorized`, `direction` (`in`/`out`), `date_from`, `date_to`. Over 50,000 rows → 422 on `date_from` "Narrow the date range." (unit-tested; **NOT VERIFIED LIVE**) |
| `GET /finance/model/{model_id}/export.xlsx` | `financial-model.xlsx` | Three sheets `P&L`, `Cash flow`, `Balance sheet`; first column `Line`, then one column per projected month. Not complete → **409** `MODEL_NOT_READY`; other workspace's model → 404 |

**Amounts are naira with decimals** (major units, number cells), unlike the API's kobo. Text cells
are never formulas: a description like `=SUM(A1:A9) hosting` is displayed, not evaluated (proven
below). Verified by unzipping and parsing the live download (`transactions_xlsx.json`,
`model_xlsx.json`; the model capture shows the first 4 rows × 4 columns of the P&L sheet):

```json
{
  "status": 200,
  "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "content_disposition": "attachment; filename=\"transactions.xlsx\"",
  "bytes_start": "PK",
  "sheets": [
    "Transactions"
  ],
  "rows": [
    [
      "Date",
      "Description",
      "Category",
      "Direction",
      "Amount",
      "Currency",
      "Source"
    ],
    [
      "2026-10-24",
      "=SUM(A1:A9) hosting",
      "Infrastructure",
      "out",
      "120000",
      "NGN",
      "manual"
    ],
    [
      "2026-10-24",
      "Team salaries",
      "Payroll",
      "out",
      "200000",
      "NGN",
      "manual"
    ],
    [
      "2026-10-24",
      "Customer payments",
      "Revenue",
      "in",
      "60000",
      "NGN",
      "manual"
    ],
    [
      "2026-06-15",
      "Pre-seed round",
      "Funding",
      "in",
      "1000000",
      "NGN",
      "manual"
    ]
  ]
}
```

```json
{
  "status": 200,
  "content_disposition": "attachment; filename=\"financial-model.xlsx\"",
  "sheets": [
    "P&L",
    "Cash flow",
    "Balance sheet"
  ],
  "pnl_first_rows": [
    [
      "Line",
      "2026-11",
      "2026-12",
      "2027-01"
    ],
    [
      "Revenue",
      "20000",
      "20000",
      "20000"
    ],
    [
      "COGS",
      "0",
      "0",
      "0"
    ],
    [
      "Gross profit",
      "20000",
      "20000",
      "20000"
    ]
  ],
  "columns_in_pnl": 37
}
```

A non-Finance role gets the standard 403 (`export_forbidden_mentor.json`):

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

No new Python dependency: the workbook is written with the standard library. The existing
client-side CSV export of the model can stay or be replaced by this.

---

## 5. Missing receipt file (D2)

If the file behind an expense's receipt is gone, `GET /finance/expenses/{id}/receipt` no longer
returns a signed link that 404s. It answers **410** and **clears the receipt on the expense**, so
`has_receipt` becomes `false` and `receipt_url` `null` from then on.

**410** (`receipt_file_missing.json`):

```json
{
  "error": {
    "code": "RECEIPT_FILE_MISSING",
    "message": "This receipt file is no longer available. Upload it again.",
    "field_errors": []
  }
}
```

The expense afterwards (`expense_after_missing_receipt.json`; `has_receipt: false`):

```json
{
  "data": {
    "id": "c42e06c9-5259-428b-b1f2-cfd64df43fdb",
    "vendor": "Notion",
    "category": "Software",
    "expense_date": "2026-10-24",
    "amount_minor": 1000,
    "currency": "NGN",
    "recurring": false,
    "notes": null,
    "has_receipt": false,
    "receipt_url": null,
    "transaction_id": "76c1767b-dadb-4bab-a8a8-33e19ba62e9c",
    "created_by": "47b32183-ce5a-43bd-8016-2ca5163b07ba",
    "created_at": "2026-10-24T19:50:26.887554Z",
    "updated_at": "2026-10-24T19:50:26.919879Z"
  },
  "meta": null
}
```

Asking again is now an ordinary expense without a receipt, **404** (`receipt_after_heal.json`):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**UX instructions:** on 410 show `error.message` ("This receipt file is no longer available.
Upload it again."), do **not** open a new tab (open the tab only after the link request succeeds,
or open it with the link in hand), then refetch the expense and show the Upload action. Until the
410 happens once, a legacy expense still reports `has_receipt: true`. The check applies to the
local storage backend; **Cloudinary storage is NOT VERIFIED LIVE** (the provider cannot be asked
cheaply, so a missing Cloudinary file still gives a link that fails).

---

## Verification table

| Behaviour | Verified |
|---|---|
| Runway `model_rows` empty without a model; filled and applied to month 1/2/3 with one; `PUT` 422 on `hiring_plan`/`one_offs`, 422 on unknown field, nothing half-applied | live (journey) |
| Generating model skipped; rows before/after the window ignored; a hire before the window starts at month 1 | unit tests |
| Runway ignores model `capex` rows | by code only (no test) |
| Invoice create/send with `account_id`, `notes`, `payment_details`; foreign/unknown account 422; freeze after send | live |
| Resend: 429 within 60 s, 200 after, second email carries bank block and notes, draft 422, mentor 403 | live |
| Resend 10/min rate limit; paid 422; accountant allowed; cross-tenant 404; account deleted keeps invoice | unit tests |
| PDF carries notes and bank block | unit tests (bytes) |
| Advisor paragraph for founder, absent for mentor (probe against live DB) | live (in-process probe) |
| Advisor prompt contains the paragraph only for Finance roles; figures-only; Funding excluded from revenue | unit tests |
| Advisor model reply | **not verified** (stub LLM) |
| Transactions and model XLSX download, parse, filters, formula text safe, mentor 403 | live |
| XLSX 50,000-row cap; mentor 403 on the model export | unit tests |
| XLSX 10/min rate limit; accountant access to the exports | **not verified** |
| Missing receipt: 410, `has_receipt` cleared, then 404, re-upload works | live (local storage) |
| Missing receipt on Cloudinary storage | **not verified** |
