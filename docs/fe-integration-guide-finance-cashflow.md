# FE Integration Guide — Finance Hub: Transactions + Cash Flow (Module 12, Slice 1)

> **Provenance.** `e2e/test_finance.py::test_finance_cash_flow_journey` was run against the live
> app (`bash scripts/e2e_run.sh`, 59 e2e passed) and every **success response body** below is
> pasted **verbatim** from the captures it wrote to `e2e/_captures/finance/`:
> `transaction_created.json`, `transactions_list.json`, `transactions_list_uncategorized.json`,
> `transaction_categorized.json`, `cash_flow.json`. Nothing was written from the schema or from
> memory. The harness captures **responses only**, so the **request** bodies below are the exact
> JSON the e2e journey sends (read from `e2e/test_finance.py`), not a capture. Everything that was
> **not** exercised live is marked ⚠️ inline — error bodies, the `DELETE` body, 403s, and the
> "runway is null" cases — and the verification table at the end says where each shape came from.
> The `id` / `created_at` values in the captures are throwaway test-run values; treat them as
> placeholders.

This is the first Finance Hub slice. It gives the FE (1) a manual **transactions ledger** with
inline categorize, and (2) a **cash-flow summary** — cash on hand, monthly burn, monthly revenue,
runway, and a 6-month series — for the Cash Flow screen.

Base path: `/api/v1/finance`. Every route requires `Authorization: Bearer <token>` and an
`X-Workspace-Id` header (the workspace id from `GET /auth/me` → `active_workspace_id`). Every
success response is the standard envelope `{"data": …, "meta": null}`.

**Access: `founder`, `team_member` or `accountant`.** Any other membership role (e.g. `mentor`,
`investor`) gets **403 `FORBIDDEN`** on every finance route. Note `accountant` is allowed here —
it is not allowed on `/marketing`. ⚠️ 403 is unit-verified
(`tests/api/test_finance.py::test_rbac_forbidden`, `::test_accountant_allowed`), not e2e-captured.

---

## Read this first — five things that will produce a wrong-looking screen

**(1) Money is `amount_minor` + `currency`, never a decimal.** `amount_minor` is an **integer in
minor units** (kobo for NGN, cents for USD). `100000000` with `"currency": "NGN"` is
**₦1,000,000.00**. The API sends only the ISO code (`"NGN"`); **the FE must map the code to a symbol
(₦, $, …) and divide by 100** for display. This applies to *every* money field in this guide:
`amount_minor`, `cash_on_hand`, `monthly_burn`, `monthly_revenue`, and `by_month[].inflow/outflow/net`.

**(2) `direction` wire values are `"in"` and `"out"`** — not `inflow`/`outflow`. `amount_minor` is
always **non-negative**; the sign lives in `direction`. Sending a negative `amount_minor` is a 422.

**(3) `runway_months` is `null` in TWO different situations — do NOT render null as "healthy".**

| Situation | `runway_months` | `runway_low` | `cash_on_hand` | `monthly_burn` | Meaning |
|---|---|---|---|---|---|
| Not burning (net-positive or break-even over the last 3 months) | `null` | `false` | any | `0` | Healthy — there is no burn, so no runway to compute |
| Burning and cash left | number (e.g. `7.0`) | `true` if `< 6`, else `false` | `> 0` | `> 0` | Normal — show the number |
| **Burning and out of cash** (`cash_on_hand ≤ 0`) | **`null`** | **`true`** | `≤ 0` | `> 0` | **Critical — the worst case, not the best** |

`runway_low` is `true` exactly when the startup is **burning AND (under 6 months of runway OR out
of cash)**. **Drive the danger banner from `runway_low`, not from `runway_months`.** When
`runway_months` is `null`, use `monthly_burn` and `cash_on_hand` to pick the copy: `monthly_burn
== 0` → "not burning / healthy"; `monthly_burn > 0` (with `runway_low: true`) → "out of cash".
⚠️ The two null situations are unit-verified
(`test_cash_flow_runway_null_when_net_positive`,
`test_cash_flow_runway_not_low_and_null_when_cash_not_positive`), not in the live capture — the
live capture only has the "7.0" case.

**(4) Burn and revenue are a trailing-3-calendar-month average (÷ 3), so young startups are
diluted.** The window runs from the **first day of the month two months ago through today**
(i.e. this month + the two before it). `monthly_burn = max(0, round((outflow − inflow) / 3))` over
that window and `monthly_revenue = round(inflow / 3)`. The divisor is **always 3**, even if the
workspace only has a few weeks of data — so a startup younger than 3 months shows a *lower* burn and
*lower* revenue than its real recent run-rate, and a *longer* runway. Consider a small "based on
last 3 months" caption. Note that `monthly_burn` is **net** burn (outflow minus inflow); an
inflow such as a fundraise inside the window reduces it.

**(5) Future-dated transactions are excluded from the cash-flow summary; multi-currency is NOT
converted.**
- Only transactions with `date <= today` (UTC) count toward `cash_on_hand`, `monthly_burn`,
  `monthly_revenue` and `by_month`. A transaction dated next week is stored and appears in
  `GET /finance/transactions`, but does not move any cash-flow number until its date arrives.
- Amounts in different currencies are **not converted**: the summary adds raw minor units across
  currencies as if they were one unit, and `currency` is just the **most common** currency among the
  workspace's counted transactions (default `"NGN"` when there are none). **This slice supports
  single-currency workspaces only** — do not let a user mix currencies in one workspace and expect a
  meaningful total. (Conversion is a follow-up.)

Also note the dates are **UTC**: "today" and the month boundaries are UTC dates, not the viewer's
local date, so a transaction created late on the last day of a month in a UTC-ahead timezone can
land in a different month bucket than the user expects.

---

## 1. `POST /finance/transactions` — record a transaction

**Request** (the exact JSON the e2e journey sends for the raise; not a capture):

```json
{
  "date": "2026-05-15",
  "description": "Pre-seed round",
  "category": "Fundraising",
  "amount_minor": 100000000,
  "currency": "NGN",
  "direction": "in"
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `date` | `YYYY-MM-DD` | yes | valid date |
| `description` | string | yes | 1–300 chars |
| `category` | string \| null | no | max 120 chars; omit or `null` = uncategorized. Free text (no fixed list) |
| `amount_minor` | integer | yes | **0 to 2,147,483,647** inclusive. Negative or above int32 → 422 |
| `currency` | string | no | 1–3 chars, defaults to `"NGN"` |
| `direction` | `"in"` \| `"out"` | yes | any other value → 422 |

`source` is **not** accepted: the server always stores `"manual"` (a `source` sent by the client is
ignored — ⚠️ unit-verified by `test_client_cannot_set_source`).

**Response `200`** — verbatim from `e2e/_captures/finance/transaction_created.json`:

```json
{
  "data": {
    "id": "1f368374-4881-43d9-9d52-ac82792d708f",
    "date": "2026-05-15",
    "description": "Pre-seed round",
    "category": "Fundraising",
    "amount_minor": 100000000,
    "currency": "NGN",
    "direction": "in",
    "source": "manual",
    "created_at": "2026-09-29T12:46:38.759726Z",
    "updated_at": "2026-09-29T12:46:38.759726Z"
  },
  "meta": null
}
```

Create returns **200** (not 201) with the created transaction. `source` is always `"manual"` in this
slice (the schema reserves `bank`/`accounting`/`stripe` for later integrations; the FE will never
see them yet). Every transaction object in this guide has exactly these ten fields, always present
(`category` is present-but-`null` when uncategorized — it is never omitted).

---

## 2. `GET /finance/transactions` — list / filter

Query params (all optional, combinable):

| Param | Type | Behaviour |
|---|---|---|
| `uncategorized` | bool (`true`/`false`, default `false`) | `true` → only rows with `category = null`. **When `true`, the `category` param is ignored.** |
| `category` | string | exact match on `category` |
| `direction` | `in` \| `out` | only that direction |
| `date_from` | `YYYY-MM-DD` | `date >= date_from` (inclusive) |
| `date_to` | `YYYY-MM-DD` | `date <= date_to` (inclusive) |

Order: **`date` descending, then `created_at` descending** (newest first). There is **no
pagination** in this slice — the full filtered set comes back in one response. Filters use the
transaction's own `date`; unlike the cash-flow summary, the list **includes future-dated rows**.

**Response `200`** (no filters; five transactions recorded by the e2e journey) — verbatim from
`e2e/_captures/finance/transactions_list.json`:

```json
{
  "data": {
    "transactions": [
      {
        "id": "880cf9b3-cc08-4e3f-8d0a-d4bc9ebbd347",
        "date": "2026-09-29",
        "description": "Bank transfer - unknown vendor",
        "category": null,
        "amount_minor": 4000000,
        "currency": "NGN",
        "direction": "out",
        "source": "manual",
        "created_at": "2026-09-29T12:46:38.798398Z",
        "updated_at": "2026-09-29T12:46:38.798398Z"
      },
      {
        "id": "1f9a96e2-995f-4b8e-9fa1-eb7fde0f86a2",
        "date": "2026-09-29",
        "description": "Cloud hosting",
        "category": "Infrastructure",
        "amount_minor": 12000000,
        "currency": "NGN",
        "direction": "out",
        "source": "manual",
        "created_at": "2026-09-29T12:46:38.790441Z",
        "updated_at": "2026-09-29T12:46:38.790441Z"
      },
      {
        "id": "e0dc0310-c0ec-45c5-a64a-ba72159ec288",
        "date": "2026-08-15",
        "description": "Team salaries",
        "category": "Payroll",
        "amount_minor": 20000000,
        "currency": "NGN",
        "direction": "out",
        "source": "manual",
        "created_at": "2026-09-29T12:46:38.781039Z",
        "updated_at": "2026-09-29T12:46:38.781039Z"
      },
      {
        "id": "a510a9f5-25ab-4129-95e8-f4e9a7aaae46",
        "date": "2026-08-15",
        "description": "First customer invoice",
        "category": "Revenue",
        "amount_minor": 6000000,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-09-29T12:46:38.772705Z",
        "updated_at": "2026-09-29T12:46:38.772705Z"
      },
      {
        "id": "1f368374-4881-43d9-9d52-ac82792d708f",
        "date": "2026-05-15",
        "description": "Pre-seed round",
        "category": "Fundraising",
        "amount_minor": 100000000,
        "currency": "NGN",
        "direction": "in",
        "source": "manual",
        "created_at": "2026-09-29T12:46:38.759726Z",
        "updated_at": "2026-09-29T12:46:38.759726Z"
      }
    ]
  },
  "meta": null
}
```

Note the envelope nesting: the array is at `data.transactions`, not `data`. An empty ledger returns
`{"data": {"transactions": []}, "meta": null}` (⚠️ from source, not captured).

**`GET /finance/transactions?uncategorized=true`** — the "needs categorizing" queue. Verbatim from
`e2e/_captures/finance/transactions_list_uncategorized.json` (taken before the row was categorized
in §3):

```json
{
  "data": {
    "transactions": [
      {
        "id": "880cf9b3-cc08-4e3f-8d0a-d4bc9ebbd347",
        "date": "2026-09-29",
        "description": "Bank transfer - unknown vendor",
        "category": null,
        "amount_minor": 4000000,
        "currency": "NGN",
        "direction": "out",
        "source": "manual",
        "created_at": "2026-09-29T12:46:38.798398Z",
        "updated_at": "2026-09-29T12:46:38.798398Z"
      }
    ]
  },
  "meta": null
}
```

The `category`, `direction`, `date_from` and `date_to` filters are ⚠️ **not exercised in the live
capture** (only `uncategorized=true` was). They are straightforward equality/range filters in
`app/services/finance/service.py::list_transactions`; an invalid `direction` (not `in`/`out`) or
malformed date is rejected by request validation with a 422.

---

## 3. `PATCH /finance/transactions/{id}` — edit / categorize inline

Partial update: send only the fields you want to change. Fields you omit are untouched.

**Request** (exactly what the e2e journey sends to categorize the uncategorized row):

```json
{ "category": "Operations" }
```

**Response `200`** — verbatim from `e2e/_captures/finance/transaction_categorized.json`:

```json
{
  "data": {
    "id": "880cf9b3-cc08-4e3f-8d0a-d4bc9ebbd347",
    "date": "2026-09-29",
    "description": "Bank transfer - unknown vendor",
    "category": "Operations",
    "amount_minor": 4000000,
    "currency": "NGN",
    "direction": "out",
    "source": "manual",
    "created_at": "2026-09-29T12:46:38.798398Z",
    "updated_at": "2026-09-29T12:46:38.823734Z"
  },
  "meta": null
}
```

Note `updated_at` moved while `created_at` did not. Returns the full updated transaction.

**Null handling — the one PATCH trap:**

| Field | Explicit `null` in the body | Omitted |
|---|---|---|
| `category` | **allowed** — clears the category (re-uncategorize) → 200 | untouched |
| `amount_minor`, `direction`, `description`, `date`, `currency` | **422 `VALIDATION_ERROR`** | untouched |

So `{"category": null}` is valid, `{"amount_minor": null}` is a 422. The same value rules as create
apply to any field you do send (`amount_minor` 0..2,147,483,647, `direction` `in`/`out`, etc.).
⚠️ Both the 422 and the `category: null` → 200 case are unit-verified
(`test_patch_explicit_null_on_required_field_422`), not live-captured.

An unknown id, or an id belonging to another workspace, is **404 `NOT_FOUND`** (⚠️ unit-verified by
`test_cross_tenant_transaction_404`; a workspace can never see another's rows).

---

## 4. `DELETE /finance/transactions/{id}`

**Response `200`:**

```json
{
  "data": { "deleted": true },
  "meta": null
}
```

⚠️ **Not live-captured** — the e2e journey does not call DELETE. The `{"deleted": true}` body and
the 200 status are asserted by the unit test
`tests/api/test_finance.py::test_transaction_crud_roundtrip` (and read from
`app/api/v1/endpoints/finance.py::delete_transaction`). The delete is a hard
delete (the row is gone, cash-flow numbers change on the next read). Unknown / other-workspace id →
**404 `NOT_FOUND`**.

---

## 5. `GET /finance/cash-flow` — the Cash Flow summary

No parameters. Same access rule as the rest of `/finance`.

**Response `200`** — verbatim from `e2e/_captures/finance/cash_flow.json` (the data behind it is the
five transactions in §2, run on 2026-09-29):

```json
{
  "data": {
    "cash_on_hand": 70000000,
    "monthly_burn": 10000000,
    "monthly_revenue": 2000000,
    "runway_months": 7.0,
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
        "inflow": 6000000,
        "outflow": 20000000,
        "net": -14000000
      },
      {
        "month": "2026-09",
        "inflow": 0,
        "outflow": 16000000,
        "net": -16000000
      }
    ]
  },
  "meta": null
}
```

| Field | Type | Meaning |
|---|---|---|
| `cash_on_hand` | integer (minor units, **can be negative**) | all-time `Σ in − Σ out`, `date <= today` only |
| `monthly_burn` | integer (minor units, `≥ 0`) | **net** burn: `max(0, round((out − in) / 3))` over the trailing 3 calendar months (this month + 2 prior). `0` when not burning |
| `monthly_revenue` | integer (minor units, `≥ 0`) | `round(Σ in / 3)` over the same window. **All inflows count as "revenue"**, including a fundraise, if it falls in the window |
| `runway_months` | **float \| `null`** | `round(cash_on_hand / monthly_burn, 1)` when `monthly_burn > 0` and `cash_on_hand > 0`, otherwise **`null`** — see "Read this first" (3). One decimal place |
| `runway_low` | boolean | `true` iff burning AND (`runway_months < 6` OR `runway_months` is null because cash ≤ 0). Use this for the danger banner |
| `currency` | string | ISO code of the most common currency among counted transactions; `"NGN"` when there are none. **Not converted** — see (5) |
| `by_month` | array of exactly 6 | last 6 calendar months **oldest → newest**, current month last, **zero-filled** (a month with no transactions is present with `0`s — the FE does not need to gap-fill) |
| `by_month[].month` | string `"YYYY-MM"` | calendar month (UTC) |
| `by_month[].inflow` / `outflow` | integer (minor units) | Σ per direction that month |
| `by_month[].net` | integer (minor units, can be negative) | `inflow − outflow` |

How the captured numbers derive (so you can sanity-check your rendering): cash on hand =
100,000,000 + 6,000,000 − (20,000,000 + 12,000,000 + 4,000,000) = **70,000,000**. Burn window
(Jul–Sep): inflow 6,000,000, outflow 36,000,000 → net out 30,000,000 ÷ 3 = **10,000,000**/month;
revenue 6,000,000 ÷ 3 = **2,000,000**/month; runway 70,000,000 ÷ 10,000,000 = **7.0** months, which
is not below 6, so `runway_low` is `false`. Notice the 100,000,000 raise in `2026-05` is inside
`by_month` (6-month series) but **outside** the 3-month burn window — the two windows differ.

**Empty workspace** (no transactions): all numbers `0`, `runway_months` `null`, `runway_low`
`false`, `currency` `"NGN"`, and `by_month` still 6 zeroed points (⚠️ unit-verified by
`test_cash_flow_empty_is_zeroed`, not live-captured). Render an empty state ("Add your first
transaction"), not "runway healthy".

### Suggested runway rendering

```
if runway_low:                       show danger banner
    if runway_months is null:        "You're out of cash and still burning"   # cash_on_hand <= 0
    else:                            f"{runway_months} months of runway left"
elif runway_months is null:          "Not burning — no runway to project"      # monthly_burn == 0
else:                                f"{runway_months} months of runway"
```

---

## Errors

Standard envelope; not re-captured for the finance routes (⚠️ the shapes below are from source, the
status codes are unit-verified — none were live-captured).

| Status | Code | When |
|---|---|---|
| 401 | — | Missing or invalid access token |
| 403 | `EMAIL_NOT_VERIFIED` | Signed in, but email not verified |
| 403 | `FORBIDDEN` | Membership role is not founder / team_member / accountant |
| 404 | `NOT_FOUND` | PATCH/DELETE on an unknown id or another workspace's transaction |
| 422 | `VALIDATION_ERROR` | Negative or > 2,147,483,647 `amount_minor`; `direction` not `in`/`out`; bad `date`; empty or > 300-char `description`; > 120-char `category`; `currency` empty or > 3 chars; explicit `null` on a non-nullable PATCH field; bad filter value on list |

Error body (⚠️ from source `app/core/errors.py`, not captured; the `field` path and message text
vary per failure — do not string-match them):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [ { "field": "<loc path>", "message": "<pydantic message>" } ]
  }
}
```

`404` uses the default `NotFound` message `"Not found."` and `403` the default `Forbidden` message
`"You don't have permission to do that."` (⚠️ from source).

---

## UX consequences the FE must surface

- **Never show `null` runway as good news.** Show the danger banner whenever `runway_low` is
  `true`, including when `runway_months` is `null` (out of cash). See "Read this first" (3).
- **State the burn window.** Label burn/revenue "avg. of last 3 months". A new workspace will look
  healthier than it is because the divisor is always 3.
- **Explain why a future-dated entry did not change the numbers.** If a user adds a transaction
  dated in the future, it appears in the ledger but the summary won't move until that date. Consider
  a "scheduled" badge for `date > today` rows in the ledger.
- **Single currency per workspace.** Don't offer a per-row currency picker that lets users mix
  currencies until conversion ships; the summary would silently add unlike units.
- **Money display:** always `amount_minor / 100` with the symbol mapped from `currency`. Never
  display or send a float amount.
- **"Revenue" is all inflow.** A fundraise inside the 3-month window inflates `monthly_revenue` and
  reduces `monthly_burn`. If the UI labels it "Revenue", be aware of this; a revenue-vs-financing
  split is a later slice.
- **Deletes are immediate and hard**; confirm before calling DELETE.

## Scope of this slice

This slice exposes the runway **number** and the **`runway_low` flag** only. Runway scenarios
(what-if), the `finance.runway.low` notification/event and the Health-Score finance signal are
**Slice 2**. Invoices, expenses/budgets, the financial model and bank/accounting integrations are
later slices; the `source` field is reserved for them.

---

## Verification

| Claim | Status | Source |
|---|---|---|
| `POST /finance/transactions` → 200, body shape (10 fields, `direction` `"in"`, `source` `"manual"`) | ✅ verified live | `transaction_created.json` |
| The create request body | ✅ exact from the journey source (harness does not capture requests) | `e2e/test_finance.py` |
| `GET /finance/transactions` → `data.transactions[]`, ordered date desc then created desc; `category: null` present when uncategorized | ✅ verified live | `transactions_list.json` |
| `GET /finance/transactions?uncategorized=true` returns only the null-category row | ✅ verified live | `transactions_list_uncategorized.json` |
| `PATCH` `{"category": "Operations"}` → 200, full row, `updated_at` advances | ✅ verified live | `transaction_categorized.json` |
| `GET /finance/cash-flow` full body (7.0 runway, `runway_low` false, 6 zero-filled ascending months, raise outside the burn window) | ✅ verified live | `cash_flow.json` |
| Burn/revenue = trailing 3 calendar months ÷ 3 | ✅ verified live (numbers reconcile) | `cash_flow.json` + arithmetic above |
| `DELETE` → 200 `{"deleted": true}` | ⚠️ unit-verified (status and body asserted), **not live-captured** | `test_transaction_crud_roundtrip`, `finance.py` |
| Filters `category` / `direction` / `date_from` / `date_to` | ⚠️ from source only (only `uncategorized` was exercised live) | `service.py::list_transactions` |
| Negative `amount_minor` → 422; `amount_minor` > int32 → 422 | ⚠️ unit-verified status, body not captured | `test_negative_amount_422`, `test_amount_over_int32_422` |
| Explicit `null` on non-nullable PATCH field → 422; `category: null` → 200 | ⚠️ unit-verified | `test_patch_explicit_null_on_required_field_422` |
| Client cannot set `source` (always `manual`) | ⚠️ unit-verified | `test_client_cannot_set_source` |
| RBAC: founder / team_member / accountant allowed; other roles 403 | ⚠️ unit-verified, not captured | `test_rbac_forbidden`, `test_accountant_allowed` |
| Cross-workspace access → 404 | ⚠️ unit-verified | `test_cross_tenant_transaction_404` |
| `runway_months` null + `runway_low` false when net-positive | ⚠️ unit-verified, **not in live capture** | `test_cash_flow_runway_null_when_net_positive` |
| `runway_months` null + `runway_low` **true** when out of cash and burning | ⚠️ unit-verified, **not in live capture** | `test_cash_flow_runway_not_low_and_null_when_cash_not_positive` |
| `runway_low` true when runway < 6 months | ⚠️ unit-verified | `test_cash_flow_runway_and_low_flag` |
| Future-dated transactions excluded from cash_on_hand / by_month | ⚠️ unit-verified | `test_future_dated_excluded_from_cash_flow` |
| Empty workspace → zeroed summary, 6 `by_month` points | ⚠️ unit-verified | `test_cash_flow_empty_is_zeroed` |
| Multi-currency amounts summed without conversion | ⚠️ from source (`cashflow.py` sums `amount_minor` irrespective of `currency`); no test exercises mixed currencies | `app/services/finance/cashflow.py` |
| Error envelope shape / 404 & 403 message text | ⚠️ from source, not captured | `app/core/errors.py` |
