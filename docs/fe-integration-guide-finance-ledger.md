# Finance ledger correctness: FE integration guide

Slice (c) of the Finance build. Every body below is pasted from a real response captured against the
running app by `e2e/test_finance_ledger_correctness.py` (files in `e2e/_captures/finance_ledger/`),
except where a section says **NOT LIVE**. IDs and tokens are throwaway values from the capture run.
All paths are under `/api/v1/finance`. Auth: bearer token plus `X-Workspace-Id`. Finance roles
(founder, team member, accountant) pass; mentor and investor get `403 FORBIDDEN` on every route here.

Error body shape everywhere: `{"error": {"code", "message", "field_errors": [{"field", "message"}]}}`.

## What changed, in the FE's terms

| Area | Before | Now |
|---|---|---|
| Receipt link | `receipt_url` was a public https URL or a server file path | `receipt_url` is **an API route**. Call it (with your bearer token) to get a signed link that expires in 5 minutes |
| Receipt upload | any file the client labelled `image/png` | file type is read from the bytes; only PNG, JPEG, PDF; 10 MB max |
| Amounts | `0` accepted | `amount_minor` must be greater than 0 (transactions and expenses) |
| Currency | any 3 letters | NGN only; anything else is a 422 |
| Categories | free text, case-sensitive | one spelling per category per workspace; `GET /categories` lists them |
| Budget actuals | expenses only | every money-out ledger row of the month (manual, expense, imported) |
| Budgets list | budgeted categories only | also **unbudgeted** categories with spend (`limit_minor: null`) |
| Lists | unbounded, `meta: null` | `limit` / `cursor`, with `meta.next_cursor` and `meta.total_estimate` |
| Model generate | any number of parallel jobs | `409 MODEL_ALREADY_GENERATING` while one runs |

## 1. Private receipts

`GET /expenses/{id}/receipt` is the **only** way to read a receipt. Nothing else returns a storage
URL or file path. The expense object keeps `has_receipt` and `receipt_url`, where `receipt_url` is
that route (a path, not a full URL).

Upload (`POST /expenses/{id}/receipt`, multipart field `file`). The filename and `Content-Type` you
send are ignored; the server reads the first bytes. Captured with a PNG sent as `anything.txt` /
`text/plain`:

```json
{
  "data": {
    "id": "c66963f3-686f-4aec-9bde-7f4e48239858",
    "vendor": "AWS",
    "category": "Infrastructure",
    "expense_date": "2026-10-22",
    "amount_minor": 250000,
    "currency": "NGN",
    "recurring": false,
    "notes": null,
    "has_receipt": true,
    "receipt_url": "/api/v1/finance/expenses/c66963f3-686f-4aec-9bde-7f4e48239858/receipt",
    "transaction_id": "39dc4151-83bd-4228-a2e0-cf6ad4f5db6f",
    "created_by": "3c60b4f8-7568-4354-8e4c-9c26e1c47720",
    "created_at": "2026-10-22T13:26:35.955912+01:00",
    "updated_at": "2026-10-22T13:26:36.127130+01:00"
  },
  "meta": null
}
```

An HTML file labelled `image/png` (also anything that is not PNG, JPEG or PDF, an empty file, or a
file over 10 MB) is rejected with 422 and nothing is stored:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Receipt must be a PNG, JPEG, or PDF.",
    "field_errors": [
      {
        "field": "receipt",
        "message": "Receipt must be a PNG, JPEG, or PDF."
      }
    ]
  }
}
```

Getting a link (founder; team member and accountant return the same shape):

```json
{
  "data": {
    "url": "http://127.0.0.1:8010/api/v1/finance/receipts/download?token=eyJrIjoicmVjZWlwdHMvODQzMDFjMjgtYTc2Ny00MmY4LTg1MjgtMDc4YTU0ODZmZTExLzI4NmEyZmRkZmE4ODQ3ODQ5ZDE4OTI5NzcyNTYwMmNmLnBuZyIsImUiOjE3OTI2NzIyOTZ9.n9-xbqSyNrleu4jntBVux3c9xfjPvy6gGDXMeOBLwfg",
    "expires_at": "2026-10-22T12:31:36+00:00",
    "content_type": "image/png"
  },
  "meta": null
}
```

- `url`: open it in a new tab or use it as an `<img>`/`<iframe>` source. It needs **no** bearer token.
- `expires_at`: about 5 minutes from now. **Do not cache the link or store it; ask again each time the user clicks.** An expired link answers 404.
- `content_type`: `image/png`, `image/jpeg` or `application/pdf`; choose the preview widget from it.
- In local/dev the link points at this API (`/api/v1/finance/receipts/download?token=...`). In staging/prod it is a Cloudinary signed download URL on `api.cloudinary.com` (**NOT LIVE**, see the table at the end). Treat `url` as opaque.

What the link serves (captured headers; the body is the file bytes):

```json
{
  "status": 200,
  "headers": {
    "content-type": "image/png",
    "x-content-type-options": "nosniff",
    "cache-control": "private, no-store",
    "content-disposition": "inline; filename=\"receipt.png\""
  },
  "bytes": 37,
  "url_shape": "http://127.0.0.1:8010/api/v1/finance/receipts/download?token=<signed token>"
}
```

Errors the FE must handle:

| Case | Status | Body |
|---|---|---|
| No receipt on the expense | 404 | see below |
| Mentor / investor | 403 | see below |
| Expense belongs to another workspace | 404 (never 403) | see below |
| Link expired, edited or unknown | 404 | see below |

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
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**UX consequences to surface**
- If the file link answers 404 after you obtained it, request a new link instead of showing "not found" (the 5 minutes ran out).
- Receipts uploaded before this change keep working: the first time one is opened the server moves it behind authentication. Nothing to do on the FE except use the route in `receipt_url`.
- A receipt can no longer be shared by copying its address; tell users to download the file if they need to send it.

A receipt row written by the old code (legacy), captured as the API now returns it. There is no raw URL or path anywhere:

```json
{
  "data": {
    "id": "0a2778c5-2041-46d5-aca3-cd10517974ba",
    "vendor": "Legacy Vendor",
    "category": "Software",
    "expense_date": "2026-01-05",
    "amount_minor": 150000,
    "currency": "NGN",
    "recurring": false,
    "notes": null,
    "has_receipt": true,
    "receipt_url": "/api/v1/finance/expenses/0a2778c5-2041-46d5-aca3-cd10517974ba/receipt",
    "transaction_id": null,
    "created_by": null,
    "created_at": "2026-10-22T13:26:37.782469+01:00",
    "updated_at": "2026-10-22T13:26:37.782469+01:00"
  },
  "meta": null
}
```

## 2. Positive amounts and NGN only

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "amount_minor",
        "message": "Input should be greater than 0"
      }
    ]
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "currency",
        "message": "Only NGN is supported for now."
      }
    ]
  }
}
```

Same `currency` error (`"Only NGN is supported for now."`) on `POST`/`PATCH /transactions`, `/expenses` and
`POST /budgets`. `"ngn"` is accepted and stored as `NGN`. Expenses:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "currency",
        "message": "Only NGN is supported for now."
      }
    ]
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "currency",
        "message": "Only NGN is supported for now."
      }
    ]
  }
}
```

Hide the currency field. Payments import (Paystack/Flutterwave) skips non-NGN rows and reports the
count as `skipped_currency` in the sync summary (shape in `fe-integration-guide-finance-payments.md`).

## 3. Categories

`GET /categories` returns the default categories plus every category the workspace has used, one entry
per case-folded name (`is_default`, `in_use`). Use it for the category Select, with an "Add category" option that just
posts the typed text.

```json
{
  "data": {
    "categories": [
      {
        "name": "Equipment",
        "is_default": true,
        "in_use": true
      },
      {
        "name": "Infrastructure",
        "is_default": true,
        "in_use": true
      },
      {
        "name": "Legal",
        "is_default": true,
        "in_use": false
      },
      {
        "name": "Marketing",
        "is_default": true,
        "in_use": false
      },
      {
        "name": "Other",
        "is_default": true,
        "in_use": false
      },
      {
        "name": "Payment fees",
        "is_default": true,
        "in_use": false
      },
      {
        "name": "Payroll",
        "is_default": true,
        "in_use": false
      },
      {
        "name": "Refunds",
        "is_default": true,
        "in_use": false
      },
      {
        "name": "Rent",
        "is_default": true,
        "in_use": false
      }
    ]
  },
  "meta": null
}
```
(The captured list is cut after the first nine entries in this guide; the real response also lists Revenue, Software, Taxes, Travel, Utilities and Hosting.)

Writes are normalised on `POST`/`PATCH` for transactions, expenses and budgets: whitespace is trimmed and collapsed, and
the text is matched case-insensitively to a default, then to a category the workspace already uses, else
kept as typed. So typing `  hosting ` after `Hosting` exists stores `Hosting`:

```json
{
  "data": {
    "id": "0275d6b0-8a5a-4428-9263-234cf8c71714",
    "vendor": "Cloud host",
    "category": "Hosting",
    "expense_date": "2026-10-22",
    "amount_minor": 5000,
    "currency": "NGN",
    "recurring": false,
    "notes": null,
    "has_receipt": false,
    "receipt_url": null,
    "transaction_id": "9bd1876f-c042-4ff8-8965-dbd8345197b3",
    "created_by": "3c60b4f8-7568-4354-8e4c-9c26e1c47720",
    "created_at": "2026-10-22T13:26:38.199194+01:00",
    "updated_at": "2026-10-22T13:26:38.199194+01:00"
  },
  "meta": null
}
```

**Trap:** the category in the response can differ in case from what the user typed. Always render the response value.
A blank category is `null` on a transaction and a 422 on expenses and budgets.
The `category` filter on `GET /transactions` and `GET /expenses` is case-insensitive.

## 4. Budgets from the ledger

Budget actuals (`spent_minor`) are now **every money-out ledger row of the month in that category**: manual
entries, the outflow an expense posts, and imported rows. Do not add expenses on top of it. Created here: a
10,000,000 Equipment budget with a manual 4,500,000 outflow already in the ledger:

```json
{
  "data": {
    "id": "9cfde7a8-b805-4cbc-965e-0dd6a2dc22d1",
    "category": "Equipment",
    "period_month": "2026-10",
    "limit_minor": 10000000,
    "currency": "NGN",
    "notes": null,
    "spent_minor": 4500000,
    "variance_minor": -5500000,
    "variance_percent": -55.0,
    "over_budget": false,
    "percent_used": 45.0,
    "status": "under",
    "unbudgeted": false,
    "created_at": "2026-10-22T13:26:38.302944+01:00",
    "updated_at": "2026-10-22T13:26:38.302944+01:00"
  },
  "meta": null
}
```

`GET /budgets?month=YYYY-MM` now appends one row per category that has spend but no budget. Distinguish them by
`unbudgeted: true`. For those rows `id`, `limit_minor`, `variance_minor`, `variance_percent`, `percent_used`,
`created_at` and `updated_at` are **null** (not absent). Order: budgeted rows (by category) then unbudgeted rows (largest spend first).
Spend with no category appears as the category `"Uncategorized"`.

```json
{
  "data": {
    "month": "2026-10",
    "budgets": [
      {
        "id": "9cfde7a8-b805-4cbc-965e-0dd6a2dc22d1",
        "category": "Equipment",
        "period_month": "2026-10",
        "limit_minor": 10000000,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 4500000,
        "variance_minor": -5500000,
        "variance_percent": -55.0,
        "over_budget": false,
        "percent_used": 45.0,
        "status": "under",
        "unbudgeted": false,
        "created_at": "2026-10-22T13:26:38.302944+01:00",
        "updated_at": "2026-10-22T13:26:38.302944+01:00"
      },
      {
        "id": null,
        "category": "Infrastructure",
        "period_month": "2026-10",
        "limit_minor": null,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 250000,
        "variance_minor": null,
        "variance_percent": null,
        "over_budget": false,
        "percent_used": null,
        "status": "unbudgeted",
        "unbudgeted": true,
        "created_at": null,
        "updated_at": null
      },
      {
        "id": null,
        "category": "Software",
        "period_month": "2026-10",
        "limit_minor": null,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 33000,
        "variance_minor": null,
        "variance_percent": null,
        "over_budget": false,
        "percent_used": null,
        "status": "unbudgeted",
        "unbudgeted": true,
        "created_at": null,
        "updated_at": null
      },
      {
        "id": null,
        "category": "Hosting",
        "period_month": "2026-10",
        "limit_minor": null,
        "currency": "NGN",
        "notes": null,
        "spent_minor": 25000,
        "variance_minor": null,
        "variance_percent": null,
        "over_budget": false,
        "percent_used": null,
        "status": "unbudgeted",
        "unbudgeted": true,
        "created_at": null,
        "updated_at": null
      }
    ]
  },
  "meta": null
}
```

New fields on every budget row: `variance_percent` ((spent - limit) / limit, one decimal; null when the limit is 0 or missing) and `status`
(`under` below 80% of the limit, `near` from 80% to 100%, `over` above it, `unbudgeted`). **Show `status` as words**, never colour alone.
A zero-limit budget has null percentages and is `over` as soon as anything is spent.

**UX consequence:** a budget's actual will jump up for founders who had logged spend as manual transactions;
that spend was always real, it just was not counted before. Say so once in the UI.

## 5. Pagination

`GET /transactions`, `/expenses`, `/invoices` accept `limit` (1 to 200, default 100) and `cursor`. Results are newest first.
`meta.next_cursor` is `null` on the last page; `meta.total_estimate` is the exact count matching the current filters
(not just the page). Pass `next_cursor` back unchanged. Filters must be the same on every page of a walk.

```json
{
  "data": {
    "transactions": [
      {
        "id": "9617667e-b1fb-42e5-962f-acf5ef50b111",
        "date": "2026-10-22",
        "description": "Sale 4",
        "category": null,
        "amount_minor": 1004,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-10-22T13:26:38.535907+01:00",
        "updated_at": "2026-10-22T13:26:38.535907+01:00"
      },
      {
        "id": "c56c27de-ee34-48f3-9464-d5e331a088c5",
        "date": "2026-10-22",
        "description": "Sale 3",
        "category": null,
        "amount_minor": 1003,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-10-22T13:26:38.488654+01:00",
        "updated_at": "2026-10-22T13:26:38.488654+01:00"
      }
    ]
  },
  "meta": {
    "next_cursor": "WyIyMDI2LTEwLTIyIiwgIjIwMjYtMTAtMjJUMTM6MjY6MzguNDg4NjU0KzAxOjAwIiwgImM1NmMyN2RlLWVlMzQtNDhmMy05NDY0LWQ1ZTMzMWEwODhjNSJd",
    "total_estimate": 10
  }
}
```
```json
{
  "data": {
    "transactions": [
      {
        "id": "f2c0ae1f-10ad-4557-ae29-0a0ac07c6b67",
        "date": "2026-10-22",
        "description": "Sale 2",
        "category": null,
        "amount_minor": 1002,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-10-22T13:26:38.445410+01:00",
        "updated_at": "2026-10-22T13:26:38.445410+01:00"
      },
      {
        "id": "83c1c513-7cf7-4105-8163-7fc386d53ac4",
        "date": "2026-10-22",
        "description": "Sale 1",
        "category": null,
        "amount_minor": 1001,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-10-22T13:26:38.404537+01:00",
        "updated_at": "2026-10-22T13:26:38.404537+01:00"
      }
    ]
  },
  "meta": {
    "next_cursor": "WyIyMDI2LTEwLTIyIiwgIjIwMjYtMTAtMjJUMTM6MjY6MzguNDA0NTM3KzAxOjAwIiwgIjgzYzFjNTEzLTdjZjctNDEwNS04MTYzLTdmYzM4NmQ1M2FjNCJd",
    "total_estimate": 10
  }
}
```

Filtered total (`direction=in&limit=1`; note `meta.total_estimate` counts all matches):

```json
{
  "data": {
    "transactions": [
      {
        "id": "9617667e-b1fb-42e5-962f-acf5ef50b111",
        "date": "2026-10-22",
        "description": "Sale 4",
        "category": null,
        "amount_minor": 1004,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-10-22T13:26:38.535907+01:00",
        "updated_at": "2026-10-22T13:26:38.535907+01:00"
      }
    ]
  },
  "meta": {
    "next_cursor": "WyIyMDI2LTEwLTIyIiwgIjIwMjYtMTAtMjJUMTM6MjY6MzguNTM1OTA3KzAxOjAwIiwgIjk2MTc2NjdlLWIxZmItNDJlNS05NjJmLWFjZjVlZjUwYjExMSJd",
    "total_estimate": 5
  }
}
```

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid pagination cursor.",
    "field_errors": [
      {
        "field": "cursor",
        "message": "This page link is no longer valid."
      }
    ]
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.limit",
        "message": "Input should be less than or equal to 200"
      }
    ]
  }
}
```

**Traps**
- **The default page is 100.** Screens that used to get every row now get at most 100 unless they page. Cash-flow, runway and budget numbers are computed server side and are unaffected.
- The list key is unchanged (`data.transactions`, `data.expenses`, `data.invoices`); only `meta` is new. The cursor lives in `meta`, not in `data` (unlike `/notifications`).
- `GET /invoices?status=overdue` filters on the derived status; `meta.total_estimate` counts that filtered set.
- An empty `cursor=` means page one.

Expenses and invoices pages (first page of each; same envelope):

```json
{
  "data": {
    "expenses": [
      {
        "id": "0275d6b0-8a5a-4428-9263-234cf8c71714",
        "vendor": "Cloud host",
        "category": "Hosting",
        "expense_date": "2026-10-22",
        "amount_minor": 5000,
        "currency": "NGN",
        "recurring": false,
        "notes": null,
        "has_receipt": false,
        "receipt_url": null,
        "transaction_id": "9bd1876f-c042-4ff8-8965-dbd8345197b3",
        "created_by": "3c60b4f8-7568-4354-8e4c-9c26e1c47720",
        "created_at": "2026-10-22T13:26:38.199194+01:00",
        "updated_at": "2026-10-22T13:26:38.199194+01:00"
      }
    ]
  },
  "meta": {
    "next_cursor": "WyIyMDI2LTEwLTIyIiwgIjIwMjYtMTAtMjJUMTM6MjY6MzguMTk5MTk0KzAxOjAwIiwgIjAyNzVkNmIwLThhNWEtNDQyOC05MjYzLTIzNGNmOGM3MTcxNCJd",
    "total_estimate": 3
  }
}
```
```json
{
  "data": {
    "invoices": [
      {
        "id": "361fbcaa-c797-4c4e-90c9-e689616f66a7",
        "number": "INV-2026-003",
        "client_name": "Client 2",
        "client_email": "client@example.com",
        "line_items": [
          {
            "description": "Work",
            "quantity": 1,
            "unit_price_minor": 100,
            "amount_minor": 100
          }
        ],
        "subtotal_minor": 100,
        "tax_percent": 0.0,
        "tax_minor": 0,
        "total_minor": 100,
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
        "last_reminded_at": null,
        "reminder_count": 0,
        "overdue_notified_at": null,
        "created_at": "2026-10-22T13:26:38.900124+01:00",
        "updated_at": "2026-10-22T13:26:38.900124+01:00"
      },
      {
        "id": "d4e628c9-53d0-4c12-9b65-0236df7a9e0c",
        "number": "INV-2026-002",
        "client_name": "Client 1",
        "client_email": "client@example.com",
        "line_items": [
          {
            "description": "Work",
            "quantity": 1,
            "unit_price_minor": 100,
            "amount_minor": 100
          }
        ],
        "subtotal_minor": 100,
        "tax_percent": 0.0,
        "tax_minor": 0,
        "total_minor": 100,
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
        "last_reminded_at": null,
        "reminder_count": 0,
        "overdue_notified_at": null,
        "created_at": "2026-10-22T13:26:38.877055+01:00",
        "updated_at": "2026-10-22T13:26:38.877055+01:00"
      }
    ]
  },
  "meta": {
    "next_cursor": "WyIyMDI2LTEwLTIyVDEzOjI2OjM4Ljg3NzA1NSswMTowMCIsICJJTlYtMjAyNi0wMDIiXQ==",
    "total_estimate": 3
  }
}
```

## 6. One model generation at a time

`POST /model/generate` answers `202` as before. While one generation is `generating` for the workspace,
any further call (by anyone on the team) answers 409 and starts nothing:

```json
{
  "data": {
    "id": "56e272e1-bd58-426a-b8f3-dc395391f5aa",
    "status": "generating"
  },
  "meta": null
}
```
```json
{
  "error": {
    "code": "MODEL_ALREADY_GENERATING",
    "message": "A financial model is already being generated. Wait for it to finish.",
    "field_errors": []
  }
}
```

Disable the button while `GET /model` says `generating` and treat a 409 as "already running": keep polling `GET /model`.
After it completes or fails the next call works:

```json
{
  "data": {
    "id": "cf664b44-9126-4ac2-b702-8365b068cf37",
    "status": "generating"
  },
  "meta": null
}
```

A generation older than 15 minutes is treated as lost: it is marked `failed` ("Generation timed out") and a new one may start.

## Verification table

| Behaviour | Verified |
|---|---|
| Upload sniffing (HTML as PNG 422, PNG with wrong name/type accepted) | live |
| `receipt_url` is the route; no path/URL in expense bodies (create, get, list, legacy) | live |
| Link per role (founder, team member, accountant 200; mentor 403; other workspace 404; none attached 404) | live (investor 403: unit test only, investors cannot be invited yet) |
| Local signed link serves bytes with `nosniff`, `no-store`; tampered link 404 | live |
| Expired link 404 | unit test with a controlled clock (waiting 5 minutes live was not done) |
| Legacy local row moved behind auth on first open | live (local storage) |
| Cloudinary `authenticated` upload, signed download URL, legacy `upload` to `authenticated` move | **NOT LIVE**: real Cloudinary credentials were not used. Unit tests assert the SDK calls and the signed URL shape (`api.cloudinary.com/.../raw/download` with `type=authenticated`, `expires_at`, `signature`). Verify on staging before relying on it |
| Zero amount 422, NGN only 422 (transactions, expenses, budgets) | live |
| Categories list, normalisation, budget match across case | live (list and normalisation); case-insensitive filters unit-tested |
| Budget actuals include a manual outflow; unbudgeted rows; variance fields | live |
| Pagination, `meta` totals, bad cursor 422, limit bound 422 | live (transactions, expenses, invoices first pages); a full multi-page walk of each list is unit-tested |
| Model generate 202 then 409, then 202 after completion | live |
| Two simultaneous generate calls start exactly one | unit test with two real database sessions (not reproducible over HTTP here) |
| Stale `generating` (15 min) recovery | unit test |
