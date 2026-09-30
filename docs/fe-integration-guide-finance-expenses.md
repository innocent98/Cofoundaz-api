# FE Integration Guide — Finance Hub: Expenses (Module 12, Slice 4a)

> **Provenance.** `e2e/test_expenses.py::test_finance_expenses_journey` was run against the live
> app (`bash scripts/e2e_run.sh`, **63 e2e passed**, no Resend-429 flake) and every **response
> body** below is pasted **verbatim** from the 39 captures it wrote to
> `e2e/_captures/expenses/` (named inline as `capture: <file>.json`). The guide was assembled by a
> script that substitutes each capture file's exact contents into the code blocks, so nothing was
> retyped from the schema, the DTO or memory. Trimming is used in only two places and both are labelled
> in the block: the runway payload is shown as its `baseline` excerpt only (full shape is in
> `docs/fe-integration-guide-finance-runway.md`), and long lists are not shortened at all.
>
> The harness captures **responses only**, so **request** bodies are the exact JSON the journey sends
> (read from `e2e/test_expenses.py`), not captures. Everything **not** exercised live is marked
> ⚠️ inline; the verification table at the end says where each shape came from. Two setup notes about
> the live run: (a) the e2e server ran on **local file storage** (`STORAGE_BACKEND=local`), so
> `receipt_url` is a **filesystem path**, not a URL — staging/prod use Cloudinary and return an
> `https://…` URL (see §7); (b) amounts are in **minor units** (kobo for NGN).
>
> All ids (`id`, `transaction_id`, `created_by`), timestamps and the `receipt_url` path are
> throwaway values from the test run — treat them as **placeholders**, not real data.

Expenses are the money-out document of the Finance Hub. The FE gets (1) an **expenses list** with
filters (category, month, date range, recurring) and a **category summary** for the Expenses screen's
breakdown chart, (2) **create / edit / delete** of an expense, (3) an optional **receipt attachment**
(PNG / JPEG / PDF), and (4) a **real consequence in the books**: every expense posts an **outflow** into
the Slice 1 ledger, so **Cash Flow moves and Runway moves**; editing the expense edits that outflow in
place, and deleting the expense removes it exactly.

Base path: `/api/v1/finance`. Every route requires `Authorization: Bearer <token>` and an
`X-Workspace-Id` header (the workspace id from `GET /auth/me` → `active_workspace_id`). Every
success response is the standard envelope `{"data": …, "meta": null}`.

**Access: `founder`, `team_member` or `accountant`** (same as the rest of `/finance`). Any other
membership role gets **403 `FORBIDDEN`** on all eight expense routes. Expenses are workspace-scoped:
another workspace's expense id is **404 `NOT_FOUND`**, never readable. ⚠️ 403, accountant-allowed and
cross-tenant 404 are unit-verified (`test_forbidden_roles_403`, `test_accountant_allowed`,
`test_patch_delete_forbidden_roles_403`, `test_patch_delete_accountant_allowed`,
`test_get_one_and_cross_tenant_404`, `test_patch_delete_cross_tenant_404`, `test_rbac_forbidden`,
`test_cross_tenant_404`), not e2e-captured.

| # | Route | Purpose |
|---|---|---|
| 1 | `POST /finance/expenses` | create an expense (posts an outflow) |
| 2 | `GET /finance/expenses?category=&month=&date_from=&date_to=&recurring=` | list (all filters optional) |
| 3 | `GET /finance/expenses/{id}` | one expense |
| 4 | `GET /finance/expenses/summary?month=YYYY-MM` | per-category totals + percents for one month |
| 5 | `PATCH /finance/expenses/{id}` | edit (partial); syncs the linked outflow |
| 6 | `DELETE /finance/expenses/{id}` | delete; reverses the outflow, removes the receipt file |
| 7 | `POST /finance/expenses/{id}/receipt` | attach / replace a receipt (multipart) |
| 8 | `DELETE /finance/expenses/{id}/receipt` | remove the receipt |

---

## Read this first — seven things that will produce a wrong-looking screen

**(1) An expense IS an outflow: creating one moves Cash Flow AND Runway.** `POST /finance/expenses`
also creates a row in the transactions ledger (`direction: "out"`, `source: "expense"`, same amount /
date / category / currency, description `Expense: <vendor>`) and links it on the expense as
`transaction_id`. The next `GET /finance/cash-flow` and `GET /finance/runway` include it. This is the
composition of Slices 1–4. Live numbers (§ "Expense → cash-flow → runway"): a ₦1,000,000 raise then four
expenses (₦440,000 total) took cash on hand **100,000,000 → 56,000,000** minor, `monthly_burn`
**0 → 14,666,667**, `runway_months` **null → 3.8**, `runway_low` **false → true**. Editing an expense
edits that outflow in place; deleting the expense deletes it. After **any** expense write, **refetch
cash-flow and runway** (and the transactions ledger if it is on screen).

**(2) The document-once rule: log a spend as an expense OR as a manual outflow transaction — never
both.** They are two independent paths into the same ledger. An "AWS ₦120,000" expense **and** a
hand-typed "AWS ₦120,000" `POST /finance/transactions` outflow are two outflows: cash on hand drops by
₦240,000 and burn doubles. The backend cannot know they are the same purchase. The FE should make
**Expenses the primary place to log spend** (it gives category, receipt and the summary chart), and
label the manual-transaction form as "for money movements that are not an expense". Do not offer a
"also add to ledger" checkbox on the expense form — the expense already does that.

**(3) The expense's outflow is visible in the transactions ledger but is READ-ONLY there.** It appears
in `GET /finance/transactions` with `source: "expense"`. `PATCH` or `DELETE
/finance/transactions/{id}` on it returns **422 `VALIDATION_ERROR`** ("This transaction is managed by an
expense; edit or delete the expense instead."). Invoice-paid inflows (Slice 3, `source: "invoice"`) are
guarded the same way with their own message. **Hide the edit and delete controls on every ledger row
whose `source` is `"expense"` or `"invoice"`**, and give the user a link to the parent record instead.
Only `source: "manual"` rows are user-editable. See §"The outflow in the transactions ledger".

**(4) Money is integer MINOR units; `currency` defaults to `"NGN"`.** `amount_minor: 12000000` is
₦120,000.00. Divide by 100 to display; multiply by 100 to send. `amount_minor` is `0 … 2,147,483,647`.
There is no separate `tax` or `subtotal` on an expense — one amount.

**(5) `category` is a free-text string (1–120 chars), not a controlled list.** The summary groups by the
**exact string**, case-sensitive: `"Infrastructure"` and `"infrastructure"` are two separate rows. Give
the user a picker seeded from prior categories (from the list/summary) and normalise on the FE. ⚠️
source-verified (`category_summary` groups on the raw column); no live capture of two casings.

**(6) The Expenses summary and Cash Flow can disagree for a future-dated expense.** Cash Flow ignores
any transaction dated after today (UTC), so an expense dated next week does not yet reduce cash on hand or
raise burn — but the **category summary counts it** in its month (it groups by `expense_date` alone).
Either block future dates in the date picker, or label the summary "includes scheduled expenses".
⚠️ source-verified (`cashflow.py::_base_query` filters `date <= today`; `expenses.py::category_summary`
does not); not exercised live.

**(7) `recurring` is a stored flag only — nothing generates the next occurrence.** `recurring: true` is
returned and filterable (`?recurring=true`) but there is **no scheduler**: the user must log next
month's Notion charge themselves. Do not show "next due" or "auto-repeats" copy. See Known limitations.

## The expense object

Every expense endpoint (except list, summary and delete) returns exactly this shape, all 14 fields
always present (nullable ones are present-as-`null`, never omitted):

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `vendor` | string | 1–200 chars |
| `category` | string | 1–120 chars, free text (see trap 5) |
| `expense_date` | `YYYY-MM-DD` | the day the money was spent; drives the ledger date, the month filter and the summary month |
| `amount_minor` | int | minor units, `0 … 2,147,483,647` |
| `currency` | string | 1–3 chars, default `"NGN"` (not ISO-validated) |
| `recurring` | bool | stored flag only (trap 7); default `false` |
| `notes` | string \| null | free text, unbounded; the only field that can be cleared with `null` |
| `has_receipt` | bool | `true` iff `receipt_url` is non-null |
| `receipt_url` | string \| null | `null` until a receipt is attached. Local dev: a filesystem path. Staging/prod (Cloudinary): an `https://` URL (§7) |
| `transaction_id` | uuid \| null | the ledger outflow this expense produced. Server-set; a client cannot send it. Stable across edits |
| `created_by` | uuid \| null | the user who created it (server-set from the token) |
| `created_at` / `updated_at` | ISO datetime | UTC, `Z`-suffixed |

**Field-nesting traps.** (a) The list is at `data.expenses`, **not** `data` — every other expense route
returns the expense object directly at `data`. (b) The summary is at `data` with its own `rows` array —
neither `data.expenses` nor an array. (c) `DELETE /expenses/{id}` returns `data: {"deleted": true}`, not
an expense. (d) `POST` returns **200**, not 201. (e) `receipt_url`, `notes` are present-but-`null` when
unset. (f) `has_receipt` is derived — do not send it. (g) The same ledger row is at `data.transactions`
(not `data`) on `GET /finance/transactions`.

---

## 1. `POST /finance/expenses` — create

**Request** (the exact JSON the e2e journey sends for the first expense):

```json
{
  "vendor": "AWS",
  "category": "Infrastructure",
  "expense_date": "<today, YYYY-MM-DD>",
  "amount_minor": 12000000,
  "currency": "NGN",
  "notes": "Production cluster, monthly bill"
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `vendor` | string | yes | 1–200 chars |
| `category` | string | yes | 1–120 chars |
| `expense_date` | `YYYY-MM-DD` | yes | any valid date (see trap 6 for future dates) |
| `amount_minor` | int | yes | `0 … 2,147,483,647`; negatives and larger → 422 |
| `currency` | string | no (default `"NGN"`) | 1–3 chars (not ISO-validated) |
| `recurring` | bool | no (default `false`) | stored flag only |
| `notes` | string \| null | no | |

Unknown fields (including `transaction_id`, `receipt_url`, `has_receipt`, `created_by`) are ignored,
not rejected. The payroll and marketing expenses send only `vendor`, `category`, `expense_date` and
`amount_minor`.

**Response `200`** — `capture: expense_created_hosting.json`:

```json
{
  "data": {
    "id": "c270b9f6-6098-439d-aec0-d44fae9ed408",
    "vendor": "AWS",
    "category": "Infrastructure",
    "expense_date": "2026-09-30",
    "amount_minor": 12000000,
    "currency": "NGN",
    "recurring": false,
    "notes": "Production cluster, monthly bill",
    "has_receipt": false,
    "receipt_url": null,
    "transaction_id": "51b93d14-f7d5-4b34-a07f-a18264401721",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.279615Z",
    "updated_at": "2026-09-30T11:03:57.279615Z"
  },
  "meta": null
}
```

`capture: expense_created_payroll.json` (no `notes`, no `currency` sent → `notes: null`, `currency:
"NGN"`):

```json
{
  "data": {
    "id": "bc3afe6d-8500-4648-8181-1d76dadea436",
    "vendor": "Team salaries",
    "category": "Payroll",
    "expense_date": "2026-09-30",
    "amount_minor": 20000000,
    "currency": "NGN",
    "recurring": false,
    "notes": null,
    "has_receipt": false,
    "receipt_url": null,
    "transaction_id": "7a4bfb66-86ef-41d3-9575-d852f55b91f2",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.297775Z",
    "updated_at": "2026-09-30T11:03:57.297775Z"
  },
  "meta": null
}
```

`capture: expense_created_last_month_recurring.json` (request also sent `"recurring": true`,
`expense_date` = the 15th of last month):

```json
{
  "data": {
    "id": "5fd40716-619a-48a6-a910-6688212c424f",
    "vendor": "Notion",
    "category": "Software",
    "expense_date": "2026-08-15",
    "amount_minor": 4000000,
    "currency": "NGN",
    "recurring": true,
    "notes": null,
    "has_receipt": false,
    "receipt_url": null,
    "transaction_id": "7934d017-dda6-4dee-b0cc-a293d0bbf52e",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.332374Z",
    "updated_at": "2026-09-30T11:03:57.332374Z"
  },
  "meta": null
}
```

The journey also created a Marketing expense (`Google Ads`, 8,000,000) — same shape,
`capture: expense_created_marketing.json`.

**Side effects, all in one transaction with the create:** a ledger outflow is inserted and linked, the
Slice 2 low-runway alert is re-evaluated and the `money.runway_live` Health-Score signal is refreshed
(an expense can push the workspace into low runway). ⚠️ the alert/signal refresh is unit-verified only
(`test_create_refreshes_runway_alert_and_signal`); the live run's low-runway crossing was not
separately asserted (`runway_low` flipped `false → true` in the cash-flow capture, but no notification
was read back).

---

## 2. `GET /finance/expenses` — list, with filters

Query params (all optional, combine freely — they are ANDed):

| Param | Type | Meaning |
|---|---|---|
| `category` | string | exact, case-sensitive match |
| `month` | `YYYY-MM` | that calendar month by `expense_date` (`2026-13` or `2026-9` → 422) |
| `date_from` / `date_to` | `YYYY-MM-DD` | inclusive range on `expense_date` |
| `recurring` | `true` \| `false` | filter on the flag |

Ordering is **`expense_date` descending, then `created_at` descending**. The list is **unpaginated**
(the full matching set).

**No filter** — `capture: expenses_list_all.json` (four expenses; last month's Notion sorts last):

```json
{
  "data": {
    "expenses": [
      {
        "id": "8a6bb79b-f826-49a3-90cb-768d002209cb",
        "vendor": "Google Ads",
        "category": "Marketing",
        "expense_date": "2026-09-30",
        "amount_minor": 8000000,
        "currency": "NGN",
        "recurring": false,
        "notes": null,
        "has_receipt": false,
        "receipt_url": null,
        "transaction_id": "9ca46658-6d4c-4f3e-a07d-9debb28fc18b",
        "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
        "created_at": "2026-09-30T11:03:57.313714Z",
        "updated_at": "2026-09-30T11:03:57.313714Z"
      },
      {
        "id": "bc3afe6d-8500-4648-8181-1d76dadea436",
        "vendor": "Team salaries",
        "category": "Payroll",
        "expense_date": "2026-09-30",
        "amount_minor": 20000000,
        "currency": "NGN",
        "recurring": false,
        "notes": null,
        "has_receipt": false,
        "receipt_url": null,
        "transaction_id": "7a4bfb66-86ef-41d3-9575-d852f55b91f2",
        "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
        "created_at": "2026-09-30T11:03:57.297775Z",
        "updated_at": "2026-09-30T11:03:57.297775Z"
      },
      {
        "id": "c270b9f6-6098-439d-aec0-d44fae9ed408",
        "vendor": "AWS",
        "category": "Infrastructure",
        "expense_date": "2026-09-30",
        "amount_minor": 12000000,
        "currency": "NGN",
        "recurring": false,
        "notes": "Production cluster, monthly bill",
        "has_receipt": false,
        "receipt_url": null,
        "transaction_id": "51b93d14-f7d5-4b34-a07f-a18264401721",
        "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
        "created_at": "2026-09-30T11:03:57.279615Z",
        "updated_at": "2026-09-30T11:03:57.279615Z"
      },
      {
        "id": "5fd40716-619a-48a6-a910-6688212c424f",
        "vendor": "Notion",
        "category": "Software",
        "expense_date": "2026-08-15",
        "amount_minor": 4000000,
        "currency": "NGN",
        "recurring": true,
        "notes": null,
        "has_receipt": false,
        "receipt_url": null,
        "transaction_id": "7934d017-dda6-4dee-b0cc-a293d0bbf52e",
        "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
        "created_at": "2026-09-30T11:03:57.332374Z",
        "updated_at": "2026-09-30T11:03:57.332374Z"
      }
    ]
  },
  "meta": null
}
```

**`?category=Payroll`** — `capture: expenses_list_by_category.json`:

```json
{
  "data": {
    "expenses": [
      {
        "id": "bc3afe6d-8500-4648-8181-1d76dadea436",
        "vendor": "Team salaries",
        "category": "Payroll",
        "expense_date": "2026-09-30",
        "amount_minor": 20000000,
        "currency": "NGN",
        "recurring": false,
        "notes": null,
        "has_receipt": false,
        "receipt_url": null,
        "transaction_id": "7a4bfb66-86ef-41d3-9575-d852f55b91f2",
        "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
        "created_at": "2026-09-30T11:03:57.297775Z",
        "updated_at": "2026-09-30T11:03:57.297775Z"
      }
    ]
  },
  "meta": null
}
```

**`?month=<last month>`** (the journey passes `YYYY-MM` of the 15th of last month) —
`capture: expenses_list_by_month.json`:

```json
{
  "data": {
    "expenses": [
      {
        "id": "5fd40716-619a-48a6-a910-6688212c424f",
        "vendor": "Notion",
        "category": "Software",
        "expense_date": "2026-08-15",
        "amount_minor": 4000000,
        "currency": "NGN",
        "recurring": true,
        "notes": null,
        "has_receipt": false,
        "receipt_url": null,
        "transaction_id": "7934d017-dda6-4dee-b0cc-a293d0bbf52e",
        "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
        "created_at": "2026-09-30T11:03:57.332374Z",
        "updated_at": "2026-09-30T11:03:57.332374Z"
      }
    ]
  },
  "meta": null
}
```

**`?recurring=true`** — `capture: expenses_list_recurring.json` returns exactly the one recurring
expense (same shape as above; not repeated here). `date_from` / `date_to` and a malformed `month` on the
**list** route are ⚠️ unit-verified (`test_list_newest_first_and_filters`, `test_invalid_month_filter_422`),
not captured.

---

## 3. `GET /finance/expenses/{id}` — one expense

`capture: expense_get_one.json`:

```json
{
  "data": {
    "id": "c270b9f6-6098-439d-aec0-d44fae9ed408",
    "vendor": "AWS",
    "category": "Infrastructure",
    "expense_date": "2026-09-30",
    "amount_minor": 12000000,
    "currency": "NGN",
    "recurring": false,
    "notes": "Production cluster, monthly bill",
    "has_receipt": false,
    "receipt_url": null,
    "transaction_id": "51b93d14-f7d5-4b34-a07f-a18264401721",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.279615Z",
    "updated_at": "2026-09-30T11:03:57.279615Z"
  },
  "meta": null
}
```

Unknown id, a deleted expense, or another workspace's expense → **404** (see Errors).

---

## 4. `GET /finance/expenses/summary?month=YYYY-MM` — category breakdown

For the Expenses screen's per-category chart. `month` is optional and defaults to **the current month
(UTC)**; a malformed value → 422. The route is `/expenses/summary` — `summary` is never parsed as an id.

| Field | Type | Notes |
|---|---|---|
| `month` | string | echo of the requested month |
| `currency` | string | the **most common** currency among that month's expenses, else `"NGN"` when empty. Mixed currencies are **not** converted (see limitations) |
| `total_minor` | int | sum of every expense in the month |
| `rows[]` | array | one per category, **sorted by `total_minor` descending, ties by category name ascending** |
| `rows[].category` | string | exact string |
| `rows[].total_minor` | int | |
| `rows[].percent` | float | `100 × category_total ÷ total_minor`, **rounded to 1 decimal place** |

**This month** (payroll 20,000,000 / infrastructure 12,000,000 / marketing 8,000,000) —
`capture: summary_this_month.json`:

```json
{
  "data": {
    "month": "2026-09",
    "currency": "NGN",
    "total_minor": 40000000,
    "rows": [
      {
        "category": "Payroll",
        "total_minor": 20000000,
        "percent": 50.0
      },
      {
        "category": "Infrastructure",
        "total_minor": 12000000,
        "percent": 30.0
      },
      {
        "category": "Marketing",
        "total_minor": 8000000,
        "percent": 20.0
      }
    ]
  },
  "meta": null
}
```

**Percent rounding.** Each `percent` is rounded independently to 1 dp, so the rows may not sum to exactly
`100.0` (e.g. thirds give `33.3 + 33.3 + 33.3 = 99.9`). Render the numbers as returned; if you draw a
donut, scale by `total_minor`, not by the sum of the rounded percents. In the capture above the values are
exact (50.0 / 30.0 / 20.0). ⚠️ the rounding behaviour is source-verified (`round(…, 1)`); the live data
happens to divide exactly.

**Last month** — `capture: summary_last_month.json` (single category → `100.0`):

```json
{
  "data": {
    "month": "2026-08",
    "currency": "NGN",
    "total_minor": 4000000,
    "rows": [
      {
        "category": "Software",
        "total_minor": 4000000,
        "percent": 100.0
      }
    ]
  },
  "meta": null
}
```

**Empty month** — `capture: summary_empty_month.json`. **`rows` is `[]`, `total_minor` is `0` and
`currency` falls back to `"NGN"`** — render an empty state, not a chart:

```json
{
  "data": {
    "month": "2020-01",
    "currency": "NGN",
    "total_minor": 0,
    "rows": []
  },
  "meta": null
}
```

An all-zero month (expenses that exist but sum to 0) also returns `rows: []` — percentages are not
computed against a zero total (⚠️ unit-only: `test_summary_all_zero_amounts_no_divide_by_zero`).

**After deleting the payroll expense** (§6), the same month — `capture: summary_after_delete.json`
(`total_minor` 18,000,000: payroll is gone; the hosting expense is now 10,000,000 after the §5 amount
edit and sits under the renamed category `Cloud` after the §5 category edit — the summary follows edits
immediately; marketing is 8,000,000). `percent` here is 55.6 / 44.4, the 1-dp rounding of 55.555… /
44.444…:

```json
{
  "data": {
    "month": "2026-09",
    "currency": "NGN",
    "total_minor": 18000000,
    "rows": [
      {
        "category": "Cloud",
        "total_minor": 10000000,
        "percent": 55.6
      },
      {
        "category": "Marketing",
        "total_minor": 8000000,
        "percent": 44.4
      }
    ]
  },
  "meta": null
}
```

---

## 5. `PATCH /finance/expenses/{id}` — edit

Partial update: send only the fields you change. Same field rules as create; every field is optional.
**Explicit `null` is rejected on every field except `notes`** (`notes: null` clears the note) — see the
422 body below, and note its `field` is the empty string.

**Request** (the journey lowers the hosting amount and adds a note):

```json
{
  "amount_minor": 10000000,
  "notes": "Negotiated a discount"
}
```

**Response `200`** — `capture: expense_edited.json`. `transaction_id` is **unchanged** (the same ledger
row is edited in place), and `has_receipt` / `receipt_url` **survive** the edit:

```json
{
  "data": {
    "id": "c270b9f6-6098-439d-aec0-d44fae9ed408",
    "vendor": "AWS",
    "category": "Infrastructure",
    "expense_date": "2026-09-30",
    "amount_minor": 10000000,
    "currency": "NGN",
    "recurring": false,
    "notes": "Negotiated a discount",
    "has_receipt": true,
    "receipt_url": "var/storage/receipts/01548b1f-9005-4f0d-b0e0-814d62457d81/6710470267074f6db332d9cbd2ace49a.pdf",
    "transaction_id": "51b93d14-f7d5-4b34-a07f-a18264401721",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.279615Z",
    "updated_at": "2026-09-30T11:03:57.495987Z"
  },
  "meta": null
}
```

A second PATCH changes `vendor` and `category` — `capture: expense_edited_vendor_category.json`:

```json
{
  "data": {
    "id": "c270b9f6-6098-439d-aec0-d44fae9ed408",
    "vendor": "Amazon Web Services",
    "category": "Cloud",
    "expense_date": "2026-09-30",
    "amount_minor": 10000000,
    "currency": "NGN",
    "recurring": false,
    "notes": "Negotiated a discount",
    "has_receipt": true,
    "receipt_url": "var/storage/receipts/01548b1f-9005-4f0d-b0e0-814d62457d81/6710470267074f6db332d9cbd2ace49a.pdf",
    "transaction_id": "51b93d14-f7d5-4b34-a07f-a18264401721",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.279615Z",
    "updated_at": "2026-09-30T11:03:57.527692Z"
  },
  "meta": null
}
```

**What the linked outflow mirrors.** Editing `amount_minor`, `expense_date`, `category`, `vendor` or
`currency` updates the ledger row to match (`amount_minor`, `date`, `category`, `currency`, and
`description` re-derived as `Expense: <vendor>`). Editing only `notes` or `recurring` is not mirrored and
leaves the ledger row untouched (⚠️ unit-only: `test_update_non_ledger_fields_leaves_transaction_untouched`).
The Slice 2 runway alert and signal are refreshed in the same transaction (⚠️ unit-only:
`test_patch_and_delete_refresh_runway_alert_and_signal`).

Live proof the ledger followed both edits — `capture: transactions_list_after_edit.json`
(`GET /finance/transactions?category=Cloud`; the row `id` equals the expense's `transaction_id`, the amount
is the edited 10,000,000 and the description is `Expense: Amazon Web Services`):

```json
{
  "data": {
    "transactions": [
      {
        "id": "51b93d14-f7d5-4b34-a07f-a18264401721",
        "date": "2026-09-30",
        "description": "Expense: Amazon Web Services",
        "category": "Cloud",
        "amount_minor": 10000000,
        "currency": "NGN",
        "direction": "out",
        "source": "expense",
        "created_at": "2026-09-30T11:03:57.279615Z",
        "updated_at": "2026-09-30T11:03:57.527692Z"
      }
    ]
  },
  "meta": null
}
```

**Edit → cash-flow.** Lowering the hosting expense by 2,000,000 raised cash on hand by exactly that
(56,000,000 → 58,000,000) — see the composition table below.

---

## 6. `DELETE /finance/expenses/{id}` — delete

**Response `200`** — `capture: expense_deleted.json`:

```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```

The delete, in one transaction: removes the linked outflow from the ledger, deletes the receipt file from
storage (if any), deletes the expense, and re-evaluates the runway alert and signal. The e2e attached a
PDF receipt to the payroll expense first and asserted the file was gone from disk after the delete.
Afterwards `GET /finance/expenses/{id}` is a **404** — `capture: error_get_deleted_expense.json`
(identical to `error_unknown_expense.json`; same body as the 404 in Errors).

**Deletes are hard and immediate; there is no undo.** The FE should confirm, and the confirm copy should
say the amount will be added back to cash on hand.

---

## Expense → cash-flow → runway (the composition of Slices 1–4)

The journey seeds a ₦1,000,000 raise dated four months ago (outside the 3-month burn window), then walks
one workspace through create → edit → delete, capturing `GET /finance/cash-flow` and `GET
/finance/runway` at each step. Cash-flow `monthly_burn` is the trailing-3-calendar-month net outflow ÷ 3
(this month and the two before it); `runway_months` = `cash_on_hand ÷ monthly_burn`, rounded to 1 dp,
`null` when not burning.

| Step | `cash_on_hand` | `monthly_burn` | `runway_months` | `runway_low` | Captures |
|---|---|---|---|---|---|
| Baseline (raise only) | 100,000,000 | 0 | `null` | `false` | `cash_flow_before.json`, `runway_before.json` |
| After 4 expenses (12M + 20M + 8M + 4M = 44M) | 56,000,000 | 14,666,667 | 3.8 | **`true`** | `cash_flow_after_create.json`, `runway_after_create.json` |
| After editing hosting 12M → 10M | 58,000,000 | 14,000,000 | 4.1 | `true` | `cash_flow_after_edit.json`, `runway_after_edit.json` |
| After deleting payroll (20M) | 78,000,000 | 7,333,333 | 10.6 | `false` | `cash_flow_after_delete.json`, `runway_after_delete.json` |

`GET /finance/cash-flow` after creating all four expenses — `capture: cash_flow_after_create.json`. Note
`by_month`: this month's `outflow` is 40,000,000 (three expenses) and last month's is 4,000,000 (Notion),
and the raise sits four months back:

```json
{
  "data": {
    "cash_on_hand": 56000000,
    "monthly_burn": 14666667,
    "monthly_revenue": 0,
    "runway_months": 3.8,
    "runway_low": true,
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
        "outflow": 4000000,
        "net": -4000000
      },
      {
        "month": "2026-09",
        "inflow": 0,
        "outflow": 40000000,
        "net": -40000000
      }
    ]
  },
  "meta": null
}
```

`GET /finance/runway` follows the same ledger. Excerpt of `capture: runway_after_create.json` — **only
`baseline` is shown; the payload also carries `assumptions`, `horizon_months` and the three `scenarios`
with `by_month` projections (see `docs/fe-integration-guide-finance-runway.md`)**. Everything shown is
verbatim from the capture:

```json
{
  "data": {
    "<omitted: assumptions, horizon_months, scenarios>": "...",
    "baseline": {
      "cash_on_hand": 56000000,
      "monthly_burn": 14666667,
      "monthly_revenue": 0,
      "currency": "NGN"
    }
  }
}
```

The baseline moves with every expense write: `cash_on_hand` / `monthly_burn` were
`100000000 / 0` (`runway_before.json`), `56000000 / 14666667` (after create), `58000000 / 14000000`
(after edit) and `78000000 / 7333333` (after delete — `runway_after_delete.json`).

**Deletion is an exact reversal of that expense's contribution.** Deleting payroll raised cash by exactly
its 20,000,000 (58M → 78M) and dropped the burn window's outflow from 42M to 22M.

---

## The outflow in the transactions ledger — and why it's read-only

`GET /finance/transactions?direction=out` after the four creates — `capture:
transactions_list_outflows.json`. Every row has `source: "expense"`; the description is `Expense:
<vendor>`; `category`, `amount_minor`, `currency` and date mirror the expense. (The journey's manual
`Fundraising` inflow is excluded by the `direction=out` filter.) The row `id` equals the expense's
`transaction_id`:

```json
{
  "data": {
    "transactions": [
      {
        "id": "9ca46658-6d4c-4f3e-a07d-9debb28fc18b",
        "date": "2026-09-30",
        "description": "Expense: Google Ads",
        "category": "Marketing",
        "amount_minor": 8000000,
        "currency": "NGN",
        "direction": "out",
        "source": "expense",
        "created_at": "2026-09-30T11:03:57.313714Z",
        "updated_at": "2026-09-30T11:03:57.313714Z"
      },
      {
        "id": "7a4bfb66-86ef-41d3-9575-d852f55b91f2",
        "date": "2026-09-30",
        "description": "Expense: Team salaries",
        "category": "Payroll",
        "amount_minor": 20000000,
        "currency": "NGN",
        "direction": "out",
        "source": "expense",
        "created_at": "2026-09-30T11:03:57.297775Z",
        "updated_at": "2026-09-30T11:03:57.297775Z"
      },
      {
        "id": "51b93d14-f7d5-4b34-a07f-a18264401721",
        "date": "2026-09-30",
        "description": "Expense: AWS",
        "category": "Infrastructure",
        "amount_minor": 12000000,
        "currency": "NGN",
        "direction": "out",
        "source": "expense",
        "created_at": "2026-09-30T11:03:57.279615Z",
        "updated_at": "2026-09-30T11:03:57.279615Z"
      },
      {
        "id": "7934d017-dda6-4dee-b0cc-a293d0bbf52e",
        "date": "2026-08-15",
        "description": "Expense: Notion",
        "category": "Software",
        "amount_minor": 4000000,
        "currency": "NGN",
        "direction": "out",
        "source": "expense",
        "created_at": "2026-09-30T11:03:57.332374Z",
        "updated_at": "2026-09-30T11:03:57.332374Z"
      }
    ]
  },
  "meta": null
}
```

Because the expense owns this row, a direct edit or delete is refused.

**`PATCH /finance/transactions/{transaction_id}`** on it with `{"category": "Other"}` — **422**,
`capture: error_patch_expense_transaction.json`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This transaction is managed by an expense; edit or delete the expense instead.",
    "field_errors": [
      {
        "field": "source",
        "message": "This transaction is managed by an expense; edit or delete the expense instead."
      }
    ]
  }
}
```

**`DELETE /finance/transactions/{transaction_id}`** on it — **422**, `capture:
error_delete_expense_transaction.json` (same body):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "This transaction is managed by an expense; edit or delete the expense instead.",
    "field_errors": [
      {
        "field": "source",
        "message": "This transaction is managed by an expense; edit or delete the expense instead."
      }
    ]
  }
}
```

`field_errors[0].field` is `"source"`. The `message` for a `source: "invoice"` row differs — `This
transaction is managed by an invoice; unpay the invoice to change or remove it.` (Slice 3, captured in
`e2e/_captures/invoices/error_patch_invoice_transaction.json`). **The FE should not string-match either
message; decide from the row's `source`** and hide edit/delete for `expense` and `invoice`. Manual
transactions stay editable and deletable (⚠️ unit-only:
`test_manual_transaction_still_editable_after_guard_generalized`).

After the payroll expense was deleted, the ledger has three outflows —
`capture: transactions_list_after_delete.json` (same row shape as above, payroll row gone).

---

## 7. Receipts — `POST` / `DELETE /finance/expenses/{id}/receipt`

A receipt is an optional single file per expense.

**Upload** — `multipart/form-data`, one file part named **`file`** (no other fields). Do **not** set
`Content-Type` manually in `fetch`/`axios`; let the browser add the multipart boundary.

| Rule | Value |
|---|---|
| Allowed content types | `image/png`, `image/jpeg`, `application/pdf` — anything else is **422** |
| Max size | **10 MB** (10,485,760 bytes) — larger is **422** |
| One receipt per expense | uploading again **replaces** the old one and **deletes the old file** from storage |
| Effect on the ledger | none — a receipt never touches the outflow, cash-flow or runway |

The **content type is taken from the part's `Content-Type` header**, not sniffed from the bytes. A
mislabelled file (e.g. a `.png` sent as `application/pdf`) is accepted as labelled. Send the real MIME
type (in a browser, `file.type`). ⚠️ source-verified; the journey sends correctly-labelled files.

**Upload PNG** — `capture: receipt_uploaded.json` (`has_receipt` now `true`, `receipt_url` set,
`transaction_id` unchanged, `updated_at` bumped):

```json
{
  "data": {
    "id": "c270b9f6-6098-439d-aec0-d44fae9ed408",
    "vendor": "AWS",
    "category": "Infrastructure",
    "expense_date": "2026-09-30",
    "amount_minor": 12000000,
    "currency": "NGN",
    "recurring": false,
    "notes": "Production cluster, monthly bill",
    "has_receipt": true,
    "receipt_url": "var/storage/receipts/01548b1f-9005-4f0d-b0e0-814d62457d81/be495782e8224e9d88a87ac57967b636.png",
    "transaction_id": "51b93d14-f7d5-4b34-a07f-a18264401721",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.279615Z",
    "updated_at": "2026-09-30T11:03:57.452713Z"
  },
  "meta": null
}
```

**Replace with a PDF** — same route — `capture: receipt_replaced.json`. `receipt_url` is a **new value**
ending `.pdf`; the previous `.png` file was deleted from storage (the e2e asserts the old path no longer
exists on disk):

```json
{
  "data": {
    "id": "c270b9f6-6098-439d-aec0-d44fae9ed408",
    "vendor": "AWS",
    "category": "Infrastructure",
    "expense_date": "2026-09-30",
    "amount_minor": 12000000,
    "currency": "NGN",
    "recurring": false,
    "notes": "Production cluster, monthly bill",
    "has_receipt": true,
    "receipt_url": "var/storage/receipts/01548b1f-9005-4f0d-b0e0-814d62457d81/6710470267074f6db332d9cbd2ace49a.pdf",
    "transaction_id": "51b93d14-f7d5-4b34-a07f-a18264401721",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.279615Z",
    "updated_at": "2026-09-30T11:03:57.462143Z"
  },
  "meta": null
}
```

**Remove** — `DELETE /finance/expenses/{id}/receipt`, no body — `capture: receipt_removed.json` (this is the
Marketing expense, which had a receipt uploaded first; the stored file is deleted and both fields return
to `false` / `null`):

```json
{
  "data": {
    "id": "8a6bb79b-f826-49a3-90cb-768d002209cb",
    "vendor": "Google Ads",
    "category": "Marketing",
    "expense_date": "2026-09-30",
    "amount_minor": 8000000,
    "currency": "NGN",
    "recurring": false,
    "notes": null,
    "has_receipt": false,
    "receipt_url": null,
    "transaction_id": "9ca46658-6d4c-4f3e-a07d-9debb28fc18b",
    "created_by": "6bd91cb4-d31a-449d-a75a-9a89a464bbf1",
    "created_at": "2026-09-30T11:03:57.313714Z",
    "updated_at": "2026-09-30T11:03:57.488397Z"
  },
  "meta": null
}
```

`DELETE …/receipt` on an expense that has **no** receipt is a harmless **200** no-op (⚠️ unit-only:
`test_delete_receipt_when_none_is_noop_200`).

**Disallowed type** — the journey posts `virus.exe` as `application/x-msdownload` — **422**,
`capture: error_receipt_bad_type.json`. The expense is unchanged (the e2e re-reads it and asserts the
existing receipt is intact):

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

**Oversize** returns the same `VALIDATION_ERROR` envelope with `field` `"receipt"` and message
`Receipt exceeds the 10 MB limit.` ⚠️ unit-only (`test_oversize_422_and_no_receipt`, which lowers the cap
to 8 bytes); the live run did not upload 10 MB.

**Rendering the receipt — dev vs prod.** `receipt_url` is whatever the configured storage backend returns.
In the live e2e it was a **local filesystem path**
(`var/storage/receipts/<workspace-id>/<random>.png`), which a browser **cannot** open. In staging/prod
(`STORAGE_BACKEND=cloudinary`) it is an `https://res.cloudinary.com/…` URL the FE can put directly in an
`<a href>` / `<img src>`. ⚠️ the Cloudinary URL form was **not** exercised live (local backend only);
treat `receipt_url` as opaque and only link it when it starts with `http`. Gate the "View receipt"
control on **`has_receipt`**, not on string-testing the URL.

**Deleting the expense deletes the receipt file** too (§6, verified live).

---

## Errors

All errors use the standard envelope (no `data`/`meta`). Bodies below are from live captures unless
marked ⚠️.

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | missing/invalid access token | `error_unauthenticated.json` |
| 403 | `EMAIL_NOT_VERIFIED` | email not verified | ⚠️ not captured |
| 403 | `FORBIDDEN` | membership role is not founder / team_member / accountant | ⚠️ not captured (unit) |
| 404 | `NOT_FOUND` | unknown id, deleted expense, or another workspace's expense | `error_unknown_expense.json`, `error_get_deleted_expense.json` |
| 422 | `VALIDATION_ERROR` | request-shape errors, bad receipt type/size, and the managed-transaction guard | `error_create_validation.json`, `error_patch_null_required_field.json`, `error_summary_bad_month.json`, `error_receipt_bad_type.json`, `error_patch_expense_transaction.json` |

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

**404** — `capture: error_unknown_expense.json` (`NOT_FOUND` uses `"Not found."` with an **empty**
`field_errors` array):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

**422 (create validation)** — the journey posts `vendor: ""` and `amount_minor: -5`; one
`field_errors` entry per bad field (`capture: error_create_validation.json`). Use `field` to attach the
message to an input; **do not string-match `message`**:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "vendor",
        "message": "String should have at least 1 character"
      },
      {
        "field": "amount_minor",
        "message": "Input should be greater than or equal to 0"
      }
    ]
  }
}
```

**422 (explicit null on a required PATCH field)** — `PATCH {"amount_minor": null}`, `capture:
error_patch_null_required_field.json`. **Trap: `field` is the empty string `""`, not `"amount_minor"`** —
the name is only inside `message`. Never send `null` for `vendor`, `category`, `expense_date`,
`amount_minor`, `currency` or `recurring`; omit the field instead. (`notes: null` is the one allowed null.)

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, amount_minor may not be null"
      }
    ]
  }
}
```

**422 (bad month on summary)** — `?month=2026-13`, `capture: error_summary_bad_month.json`. **Trap:
`field` is `"query.month"`** (the location is prefixed for query params), and the pattern is
`YYYY-MM` with month `01–12`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "query.month",
        "message": "String should match pattern '^\\d{4}-(0[1-9]|1[0-2])$'"
      }
    ]
  }
}
```

**422 (receipt type)** — see §7 (`error_receipt_bad_type.json`). **422 (managed transaction)** — see
"The outflow in the transactions ledger".

Also 422 (⚠️ unit-verified, bodies not captured): `amount_minor` over 2,147,483,647 or empty `category`
on create (`test_create_validation_422`), an out-of-range amount on PATCH
(`test_patch_out_of_range_amount_422`), a malformed `month` on the list route, oversize receipt.

---

## UX consequences the FE must surface

- **Refetch cash-flow and runway after every expense create / edit / delete** (and the ledger if shown).
  A "Add expense" success toast should say it was deducted from cash on hand, and the runway card may
  turn red (`runway_low`) as a direct result.
- **Document once.** Steer users to log spend as an expense; explain that also adding it as a manual
  transaction double-counts. Label the manual-transaction form accordingly.
- **Hide edit/delete on ledger rows with `source == "expense"` or `"invoice"`**; show "Managed by an
  expense — open it" / "Managed by an invoice" with a link to the parent.
- **Editing the amount or date changes cash on hand today.** Say so in the edit sheet. Deleting adds the
  amount back.
- **Empty month = empty state.** `rows: []` and `total_minor: 0` — do not draw a zero chart.
- **Percent rows may not sum to exactly 100** (rounding). Render as returned.
- **Future-dated expenses appear in the summary but not yet in cash-flow** (trap 6).
- **Don't promise recurring behaviour.** `recurring` is a label only; nothing repeats automatically.
- **Category is free text** — offer a picker from prior categories to avoid `"Ads"` vs `"ads"` splitting
  the chart.
- **A single currency per workspace.** Do not let users mix currencies (see limitations).
- **Disable submit while a money request is in flight** (no idempotency key — see limitations).
- **Receipts:** accept only PNG / JPEG / PDF up to 10 MB client-side too (validate before upload);
  confirm on replace ("this replaces the current receipt"); gate "View receipt" on `has_receipt`.

## Known limitations (be honest with users)

| Limitation | Effect |
|---|---|
| **No idempotency key on money POSTs** (`POST /finance/expenses`, and module-wide across `/finance/transactions` and `/finance/invoices`). | A client retry after a timeout, or a double-click, can **create two expenses and two outflows** (double-deducted cash). Disable the submit button while in flight and do not auto-retry a `POST` that may have reached the server. A module-wide fix is a tracked follow-up |
| **Single-currency assumption.** Currencies are not converted (same as Slices 1–3). | The summary sums raw minor units across currencies (`currency` is just the most common one), and every expense outflow adds its raw minor units into `cash_on_hand` and burn as if it were the workspace currency. Don't let users mix currencies until conversion ships |
| **`currency` is not ISO-validated** — only 1–3 characters. | `"ZZ"` or `"1"` is accepted; use a currency picker |
| **`recurring` is a flag only — there is no scheduler.** | Nothing creates the next occurrence or reminds the user. The flag is only stored, returned and filterable |
| **Concurrent edits to one expense can desync it from its outflow.** `PATCH` reads the expense without a row lock. | Two simultaneous PATCHes to the *same* expense can leave the ledger row reflecting a different write than the expense row. Rare (needs two clients editing one expense at the same instant); disable Save while a PATCH is in flight |
| **Receipt storage is deleted *before* the database commit** (also true of Documents). | If the commit fails after the file delete (replace / remove / delete-expense), the row can briefly point at a receipt file that no longer exists, or a stored file can be orphaned. Rare; a broken receipt link should degrade gracefully ("receipt unavailable") |
| **Receipt content type is trusted from the header** (not sniffed). | A mislabelled file is stored as labelled |
| **`category` is free text.** | Case-sensitive grouping (trap 5) |
| **List is unpaginated** (`expense_date` desc, then `created_at` desc). | Fine at current volumes |
| **No `expense.*` domain events / notifications.** | Nothing fires in the notification feed when an expense is created |
| **Budgets are not built yet.** | Slice 4b will add per-category monthly limits whose "actual" is the sum of these expenses; there is no budget field on an expense today |

---

## Verification

Legend: ✅ **verified live** = asserted in `e2e/test_expenses.py` against the running app and the body is
in `e2e/_captures/expenses/`. ⚠️ **unit-only** = asserted by the named unit test; not exercised live.

| Claim | Status | Source |
|---|---|---|
| `POST /expenses` → 200, 14-field body, `transaction_id` set, `has_receipt: false`, `receipt_url: null` | ✅ verified live | `expense_created_hosting.json` |
| `currency` defaults to `"NGN"`; `notes` defaults to `null`; `recurring` defaults to `false` | ✅ verified live | `expense_created_payroll.json` |
| `recurring: true` stored and returned; back-dated (last month) expense accepted | ✅ verified live | `expense_created_last_month_recurring.json` |
| Each create links its own distinct outflow (`transaction_id` unique) | ✅ verified live | e2e asserts 3 distinct ids; `transactions_list_outflows.json` |
| `created_by` set from the token | ✅ verified live (present) / ⚠️ value unit-only | `expense_created_hosting.json`, `test_create_sets_created_by_from_verified_user` |
| List: no filter, newest `expense_date` first | ✅ verified live | `expenses_list_all.json` |
| List filters `category`, `month`, `recurring` | ✅ verified live | `expenses_list_by_category.json`, `expenses_list_by_month.json`, `expenses_list_recurring.json` |
| List filters `date_from` / `date_to`; malformed `month` on list → 422 | ⚠️ unit-only | `test_list_newest_first_and_filters`, `test_invalid_month_filter_422` |
| `GET /expenses/{id}` | ✅ verified live | `expense_get_one.json` |
| Summary: per-category totals + percents (50.0 / 30.0 / 20.0), sorted total-desc | ✅ verified live | `summary_this_month.json` |
| Summary: single category, empty month (`rows: []`, `total_minor: 0`, `currency: "NGN"`), post-delete recompute | ✅ verified live | `summary_last_month.json`, `summary_empty_month.json`, `summary_after_delete.json` |
| Summary percent rounded to 1 dp, may not sum to 100 | ⚠️ from source only (live data divides exactly) | `app/services/finance/expenses.py::category_summary` |
| Summary all-zero month → no divide-by-zero, `rows: []`; defaults to current month; route not shadowed by `/{id}`; tenant-scoped | ⚠️ unit-only | `test_summary_all_zero_amounts_no_divide_by_zero`, `test_summary_defaults_to_current_month_and_route_not_shadowed`, `test_summary_is_tenant_scoped` |
| Summary bad month → 422 with `field: "query.month"` | ✅ verified live | `error_summary_bad_month.json` |
| Create moves cash-flow: cash 100M → 56M, burn 0 → 14,666,667, runway `null` → 3.8, `runway_low` false → true | ✅ verified live | `cash_flow_before.json` → `cash_flow_after_create.json` |
| Create moves runway baseline (`cash_on_hand`, `monthly_burn`) | ✅ verified live | `runway_before.json` → `runway_after_create.json` |
| Edit syncs cash-flow (56M → 58M, burn → 14,000,000, runway 4.1) and runway baseline | ✅ verified live | `cash_flow_after_edit.json`, `runway_after_edit.json` |
| Edit keeps the same `transaction_id`; ledger row follows amount, category and vendor (`Expense: <vendor>`) | ✅ verified live | `expense_edited.json`, `transactions_list_after_edit.json` |
| Edit keeps the receipt | ✅ verified live | `expense_edited.json` (`has_receipt: true`) |
| Editing non-ledger fields (`notes`, `recurring`) leaves the ledger row untouched | ⚠️ unit-only | `test_update_non_ledger_fields_leaves_transaction_untouched` |
| `PATCH {"notes": null}` clears the note; explicit null on a required field → 422 (`field: ""`) | ⚠️ unit (clear) / ✅ live (422 shape) | `test_patch_partial_and_null_notes_allowed`; `error_patch_null_required_field.json` |
| Out-of-range amount on PATCH / create → 422 | ⚠️ unit-only | `test_patch_out_of_range_amount_422`, `test_create_validation_422` |
| Create-validation 422 body (per-field `field_errors`) | ✅ verified live | `error_create_validation.json` |
| Delete → 200 `{"deleted": true}`; subsequent GET → 404 | ✅ verified live | `expense_deleted.json`, `error_get_deleted_expense.json` |
| Delete reverses cash-flow exactly (58M → 78M, burn 7,333,333, runway 10.6, `runway_low` false) and runway baseline | ✅ verified live | `cash_flow_after_delete.json`, `runway_after_delete.json` |
| Delete removes the outflow from the ledger (3 outflows remain) | ✅ verified live | `transactions_list_after_delete.json` |
| Delete of an expense with a receipt removes the stored file | ✅ verified live (local file existence asserted) | e2e assertion; `expense_deleted.json` |
| Expense outflow visible in `GET /finance/transactions` with `source: "expense"` | ✅ verified live | `transactions_list_outflows.json` |
| `PATCH` / `DELETE` on an expense-managed transaction → 422 "managed by an expense" | ✅ verified live | `error_patch_expense_transaction.json`, `error_delete_expense_transaction.json` |
| Same guard for `source: "invoice"` (own message) | ✅ verified live (Slice 3 capture) | `e2e/_captures/invoices/error_patch_invoice_transaction.json` |
| Manual transactions still editable/deletable after the guard was generalized | ⚠️ unit-only | `test_manual_transaction_still_editable_after_guard_generalized` |
| Receipt upload (PNG) → `has_receipt: true`, `receipt_url` set, file stored | ✅ verified live (local backend) | `receipt_uploaded.json` |
| Receipt replace (PDF) → new url, old file deleted | ✅ verified live (local backend) | `receipt_replaced.json` |
| `DELETE …/receipt` → `has_receipt: false`, `receipt_url: null`, file removed | ✅ verified live (local backend) | `receipt_removed.json` |
| Disallowed receipt type → 422, expense unchanged | ✅ verified live | `error_receipt_bad_type.json` |
| PDF accepted; oversize (> 10 MB) → 422; delete-with-no-receipt → 200 no-op | ⚠️ unit-only | `test_upload_pdf_accepted`, `test_oversize_422_and_no_receipt`, `test_delete_receipt_when_none_is_noop_200` |
| Cloudinary `https://` receipt URL form | ⚠️ not verified (e2e ran `STORAGE_BACKEND=local`) | `app/platform/storage.py::CloudinaryStorage` |
| Receipt content type trusted from the header (not sniffed) | ⚠️ from source only | `app/services/finance/expenses.py::attach_receipt` |
| Create / edit / delete refresh the runway alert + `money.runway_live` signal | ⚠️ unit-only | `test_create_refreshes_runway_alert_and_signal`, `test_patch_and_delete_refresh_runway_alert_and_signal` |
| Future-dated expense: in summary, not in cash-flow | ⚠️ from source only | `cashflow.py::_base_query`, `expenses.py::category_summary` |
| Category grouping is case-sensitive | ⚠️ from source only | `expenses.py::category_summary` |
| Mixed-currency expenses summed without conversion; `currency` not ISO-validated | ⚠️ from source only (no test exercises it) | `expenses.py::category_summary`, `app/schemas/expense.py` |
| No idempotency key: a retried POST creates a second expense + outflow | ⚠️ from source only (not exercised) | `app/api/v1/endpoints/finance.py::create_expense` |
| Concurrent PATCH desync (no row lock) | ⚠️ from source only (not exercised) | `expenses.py::update_expense` |
| `recurring` has no scheduler | ⚠️ not built — flag only | — |
| 401 unauthenticated; 404 unknown id | ✅ verified live | `error_unauthenticated.json`, `error_unknown_expense.json` |
| RBAC: founder / team_member / accountant allowed; other roles → 403 | ⚠️ unit-only | `test_accountant_allowed`, `test_forbidden_roles_403`, `test_patch_delete_accountant_allowed`, `test_patch_delete_forbidden_roles_403`, `test_rbac_forbidden` |
| Cross-workspace access → 404 (get / patch / delete / receipt / summary exclusion) | ⚠️ unit-only | `test_get_one_and_cross_tenant_404`, `test_patch_delete_cross_tenant_404`, `test_cross_tenant_404`, `test_summary_is_tenant_scoped`, `test_update_and_delete_are_tenant_scoped` |
| 403 / `EMAIL_NOT_VERIFIED` error bodies | ⚠️ not captured | `app/core/errors.py` |
