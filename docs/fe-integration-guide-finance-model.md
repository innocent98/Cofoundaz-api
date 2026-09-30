# FE Integration Guide — Finance Hub: Financial Model (Module 12, Slice 5)

> **Provenance.** `e2e/test_financial_model.py::test_financial_model_journey` was run against the live app
> (`bash scripts/e2e_run.sh`, **65 e2e passed**, no Resend-429 flake on the final run) with `LLM_PROVIDER=stub`, and every
> **response body** below is pasted **verbatim** from the 5 captures it wrote to
> `e2e/_captures/financial_model/` (named inline as `capture: <file>.json`). The guide was assembled by a script
> that substitutes each capture file's exact contents into the code blocks; nothing was retyped from the
> schema, the DTO or memory. Nothing in a captured body is trimmed.
>
> The harness captures **responses only**. `POST /finance/model/generate` takes **no request body**, so there is
> no request JSON to show. Everything **not** exercised live is marked ⚠️ inline; the verification table at the
> end says where each shape came from. Amounts are **minor units** (kobo for NGN).
>
> All ids and timestamps are throwaway values from the test run — treat them as **placeholders**, not real data.
> The captured run was on 2026-09-30, so the projection starts at `2026-10`.
>
> **Stub-LLM caveat (important).** The e2e ran with the deterministic **stub** LLM, which returns all-zero
> assumptions, so the captured model is a **flat projection** (no growth, no COGS, no AR/AP delay) and the
> `narrative` reads `[stub-llm] narrative`. A real model returns non-zero, clamped assumptions and non-flat
> numbers; the **shape** is identical.

The Financial Model is the Finance Hub's **12-month, 3-statement projection** — a **P&L**, a **cash-flow
statement** and a **balance sheet** — seeded from the workspace's real actuals (Cash Flow ledger). The user
presses **Generate**; the backend asks the AI for a small set of forward-looking **assumptions**, then a
**deterministic engine** turns those assumptions plus the actuals into the three statements. Generation is
**asynchronous** (202 + poll).

Base path: `/api/v1/finance`. Every route requires `Authorization: Bearer <token>` and an `X-Workspace-Id`
header (the workspace id from `GET /auth/me` → `active_workspace_id`). Every success response is the standard
envelope `{"data": …, "meta": null}`.

**Access: `founder`, `team_member` or `accountant`** (same as the rest of `/finance`). Any other membership role
gets **403 `FORBIDDEN`** on all three routes. The model is workspace-scoped: another workspace's model id is
**404 `NOT_FOUND`**, and `GET /finance/model` only ever returns your own workspace's latest. ⚠️ 403,
accountant-allowed and cross-tenant 404 are unit-verified (`test_model_rbac_forbidden`,
`test_accountant_can_generate_and_get`, `test_cross_tenant_get_is_404`, `test_latest_is_scoped_to_own_startup`),
not e2e-captured.

| # | Route | Purpose |
|---|---|---|
| 1 | `POST /finance/model/generate` | start generating a new model → **202** `{id, status: "generating"}` |
| 2 | `GET /finance/model` | the workspace's **latest** model (the one to poll), or `{status: "none"}` |
| 3 | `GET /finance/model/{id}` | one specific model by id |

---

## Read this first — things that will produce a wrong-looking screen

1. **202 + poll.** `POST /generate` returns **immediately** with `status: "generating"` and **no numbers**.
   Poll `GET /finance/model` until `status` is `"complete"` or `"failed"`. Statements (`pnl`, `cash_flow`,
   `balance_sheet`) and `months` are **`null` until `complete`**.
2. **`GET /finance/model` has a fourth shape.** When the workspace has never generated a model it returns
   **200** with `{"data": {"status": "none"}}` — **no `id`, no other keys**. Handle `none` as "show the
   Generate button / empty state". (`GET /finance/model/{id}` never returns `none`; an unknown id is 404.)
3. **`months` is top-level only.** The 12 month labels live in `data.months`. Each statement is just
   `{ "rows": [...] }` — it does **not** repeat `months`. Zip `data.months[i]` with each row's `values[i]`.
4. **Money is minor units, and aggregates can be large.** Every `values` entry is an integer in minor units
   (kobo). Projected balances (retained earnings, paid-in capital, cash) are aggregates that can exceed
   JavaScript's safe integer range (2^53 − 1 ≈ 9.0 × 10^15) for a large company or a long compounding run
   with high growth. Parse response bodies so integers beyond 2^53 are not silently rounded (e.g. a
   BigInt-aware JSON parser) if you expect that scale; for typical startup figures plain `number` is safe.
   Divide by 100 only at display time.
5. **Values can be negative.** Net income, net cash flow, closing cash and retained earnings go negative for a
   loss-making company (in the capture, closing cash first goes negative in month 9, `2027-06`). The
   engine does **not** floor cash at zero — a negative closing cash is the model telling the founder they run
   out of money. Render negatives and treat that as the "funding gap" signal.
6. **`assumptions` is `null` until `complete`.** It is the AI's *validated* inputs, always six keys (below).
7. **Re-generating creates a new row**; `GET /finance/model` always returns the **newest**. There is no
   concurrent-generation dedup: two rapid POSTs create two models (and two AI jobs). Disable the button while
   the latest is `generating`.
8. **The `failed` state must be surfaced** (see below) — do not poll forever.

## The model object

`capture: latest_complete.json` — the full object (see the endpoint 2 section for the whole body).

| Field | Type | Notes |
|---|---|---|
| `id` | uuid string | model id |
| `status` | `"generating"` \| `"complete"` \| `"failed"` | (plus the special `"none"` on `GET /finance/model` only) |
| `horizon_months` | int | always `12` in v1 |
| `currency` | string | `"NGN"` in the capture; the workspace's currency (hardcoded NGN default in v1) |
| `assumptions` | object \| `null` | six keys, below; `null` until `complete` |
| `pnl` / `cash_flow` / `balance_sheet` | `{rows}` \| `null` | `null` until `complete` |
| `months` | `["YYYY-MM" × 12]` \| `null` | first projected month = the month after generation; `null` until `complete` |
| `error` | string \| `null` | only set on `failed` |
| `generated_at` | ISO datetime \| `null` | set when `complete` |
| `created_at` | ISO datetime | when the POST happened |

**Statement shape** — each of `pnl`, `cash_flow`, `balance_sheet` is `{ "rows": [{ "label": string, "values": [12 ints] }] }`.
Row **labels** (stable, in this order; match on `label`, not index, if you prefer):

| Statement | Rows |
|---|---|
| `pnl` | `Revenue`, `COGS`, `Gross profit`, `Opex`, `Net income` |
| `cash_flow` | `Opening cash`, `Collections`, `Payments`, `Net cash flow`, `Closing cash` |
| `balance_sheet` | `Cash`, `Accounts receivable`, `Accounts payable`, `Retained earnings`, `Paid-in capital` |

**Assumptions object** — always exactly these six keys once `complete` (the backend validates and **clamps**
whatever the AI returned, so the FE never sees out-of-range or missing values):

| Key | Type | Range (clamped) | Meaning |
|---|---|---|---|
| `monthly_revenue_growth_pct` | number | −50 … 100 | month-over-month revenue growth, percent |
| `cogs_pct_of_revenue` | number | 0 … 100 | cost of goods as % of revenue |
| `monthly_opex_growth_pct` | number | −50 … 100 | month-over-month operating-expense growth, percent |
| `ar_days` | int | 0 … 120 | days to collect receivables |
| `ap_days` | int | 0 … 120 | days to pay suppliers |
| `narrative` | string | ≤ 600 chars | the AI's short explanation of its assumptions (may be empty) |

## AI-assisted, but deterministic

The AI **only** picks the six assumptions above. It never produces a number in a statement. The **engine**
(pure code, no AI) computes every figure from (actuals + assumptions), so:

- the same assumptions always produce the same statements;
- the **balance sheet always balances**, exactly, every month:
  **Cash + Accounts receivable = Accounts payable + Retained earnings + Paid-in capital**
  (no rounding drift — all integer arithmetic). This is asserted in the e2e against the captured body (below);
- bad AI output can't break the model: values are coerced and clamped into the ranges above.

Starting points come from the workspace's real transactions: current monthly revenue and total monthly costs
(trailing 3-month average, as on Cash Flow) and cash on hand. **Paid-in capital** is the balancing opening
equity (opening cash + AR − AP): a pragmatic startup model, **not** full GAAP.

⚠️ **Every inflow in the trailing 3-month window counts as revenue** (same rule as the Cash Flow
`monthly_revenue`). A raise recorded this month therefore inflates projected revenue. The e2e deliberately dates
its raise 4 months back to keep the capture representative. Consider telling users to categorise/date financing
inflows accordingly, or expect a follow-up to split financing from revenue.

---

## 1. `POST /finance/model/generate` — start generating

No request body. **Status `202 Accepted`.**

`capture: generate_accepted.json`:

```json
{
  "data": {
    "id": "654d2b2e-1db5-4bcb-a88f-14821025aca3",
    "status": "generating"
  },
  "meta": null
}
```

Returns the new model's `id` and `status: "generating"`. Nothing else is filled yet.

## 2. `GET /finance/model` — the latest model (poll this)

### 2a. No model yet

`capture: latest_none.json`:

```json
{
  "data": {
    "status": "none"
  },
  "meta": null
}
```

### 2b. Still generating

Polled immediately after the POST, before the worker ran. Every payload field is `null` — note that `id` **is**
present here, unlike `none`. `capture: latest_generating.json`:

```json
{
  "data": {
    "id": "654d2b2e-1db5-4bcb-a88f-14821025aca3",
    "status": "generating",
    "horizon_months": 12,
    "currency": "NGN",
    "assumptions": null,
    "pnl": null,
    "cash_flow": null,
    "balance_sheet": null,
    "months": null,
    "error": null,
    "generated_at": null,
    "created_at": "2026-09-30T15:37:35.243559Z"
  },
  "meta": null
}
```

### 2c. Complete

`capture: latest_complete.json` (verbatim, untrimmed — one integer per line, 12 per row):

```json
{
  "data": {
    "id": "654d2b2e-1db5-4bcb-a88f-14821025aca3",
    "status": "complete",
    "horizon_months": 12,
    "currency": "NGN",
    "assumptions": {
      "ap_days": 0,
      "ar_days": 0,
      "narrative": "[stub-llm] narrative",
      "cogs_pct_of_revenue": 0,
      "monthly_opex_growth_pct": 0.0,
      "monthly_revenue_growth_pct": 0.0
    },
    "pnl": {
      "rows": [
        {
          "label": "Revenue",
          "values": [
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000
          ]
        },
        {
          "label": "COGS",
          "values": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0
          ]
        },
        {
          "label": "Gross profit",
          "values": [
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000
          ]
        },
        {
          "label": "Opex",
          "values": [
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667
          ]
        },
        {
          "label": "Net income",
          "values": [
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667
          ]
        }
      ]
    },
    "cash_flow": {
      "rows": [
        {
          "label": "Opening cash",
          "values": [
            74000000,
            65333333,
            56666666,
            47999999,
            39333332,
            30666665,
            21999998,
            13333331,
            4666664,
            -4000003,
            -12666670,
            -21333337
          ]
        },
        {
          "label": "Collections",
          "values": [
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000,
            2000000
          ]
        },
        {
          "label": "Payments",
          "values": [
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667,
            10666667
          ]
        },
        {
          "label": "Net cash flow",
          "values": [
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667,
            -8666667
          ]
        },
        {
          "label": "Closing cash",
          "values": [
            65333333,
            56666666,
            47999999,
            39333332,
            30666665,
            21999998,
            13333331,
            4666664,
            -4000003,
            -12666670,
            -21333337,
            -30000004
          ]
        }
      ]
    },
    "balance_sheet": {
      "rows": [
        {
          "label": "Cash",
          "values": [
            65333333,
            56666666,
            47999999,
            39333332,
            30666665,
            21999998,
            13333331,
            4666664,
            -4000003,
            -12666670,
            -21333337,
            -30000004
          ]
        },
        {
          "label": "Accounts receivable",
          "values": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0
          ]
        },
        {
          "label": "Accounts payable",
          "values": [
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0
          ]
        },
        {
          "label": "Retained earnings",
          "values": [
            -8666667,
            -17333334,
            -26000001,
            -34666668,
            -43333335,
            -52000002,
            -60666669,
            -69333336,
            -78000003,
            -86666670,
            -95333337,
            -104000004
          ]
        },
        {
          "label": "Paid-in capital",
          "values": [
            74000000,
            74000000,
            74000000,
            74000000,
            74000000,
            74000000,
            74000000,
            74000000,
            74000000,
            74000000,
            74000000,
            74000000
          ]
        }
      ]
    },
    "months": [
      "2026-10",
      "2026-11",
      "2026-12",
      "2027-01",
      "2027-02",
      "2027-03",
      "2027-04",
      "2027-05",
      "2027-06",
      "2027-07",
      "2027-08",
      "2027-09"
    ],
    "error": null,
    "generated_at": "2026-09-30T15:37:35.329126Z",
    "created_at": "2026-09-30T15:37:35.243559Z"
  },
  "meta": null
}
```

### 2d. Failed

⚠️ **Not captured live** — with the stub LLM the model always completes; the failed path is **unit-verified**
(`tests/worker/test_finance_model_handler.py::test_over_budget_marks_failed`,
`test_mid_call_none_marks_failed_without_partial_model`). Per the serializer
(`app/services/finance/model_service.py::serialize_model`) a failed model has `status: "failed"`,
`error: "AI budget exceeded"` (the unit-tested string), and `assumptions`, `pnl`, `cash_flow`,
`balance_sheet`, `months` and `generated_at` all `null`; `id`, `horizon_months`, `currency` and `created_at`
are still present.

## 3. `GET /finance/model/{id}` — one model

Same object as 2b/2c/2d for that id (works for `generating`, `complete` and `failed` rows).
`capture: model_by_id.json` — for the completed model above this body is **byte-for-byte identical to the
`latest_complete.json` body** (the e2e asserts equality of the two `data` objects), so it is not pasted a
second time. An unknown or another workspace's id is **404 `NOT_FOUND`** (⚠️ unit-verified,
`test_cross_tenant_get_is_404`; body shape is the standard error envelope, e.g. `{"error": {"code": "NOT_FOUND",
"message": "Not found.", "field_errors": []}}` as captured for the budgets guide). A malformed (non-UUID)
`id` is a FastAPI **422** validation error (⚠️ from source only).

---

## State machine and polling

```
POST /generate ──► generating ──► complete   (statements + assumptions + months present)
                              └─► failed     (error set; statements null)
```

- `none` (only on `GET /finance/model`): never generated → show empty state + **Generate**.
- `generating`: show a spinner/skeleton. **Poll `GET /finance/model` every 2 s** for the first ~10 s, then
  back off to every 5 s. In the e2e the stub completes within one worker pass; a real LLM call takes longer
  (not measured). Stop polling on `complete` or `failed`; consider giving up with a "taking longer than usual —
  refresh" message after ~60 s while leaving the row to finish server-side.
- `complete`: render the three statements from `months` × `rows`.
- `failed`: see next section.

## UX consequences the FE must surface

- **`failed` is a real, expected state — show it.** If the workspace's AI budget is exhausted the model comes
  back `status: "failed"` with `error: "AI budget exceeded"`. The FE must render an error state with the
  message and a **Regenerate** action (a new POST). Never spin forever on a `failed` row. (Because the model
  is a projection the user asked for, failing loudly beats showing stale numbers.)
- **Show the assumptions and the narrative** next to the statements, so the user sees what the AI assumed
  (growth %, COGS %, opex growth %, AR/AP days, and the narrative text). They are the "why" behind the numbers.
- **Label it a projection, AI-assisted.** The figures are estimates derived from the last three months of
  actuals plus AI-chosen assumptions — not forecasts guaranteed by the system.
- **Negative cash = running out.** When `Closing cash` / `Cash` goes negative (as it does from month 9 in the
  capture), highlight it; that is the runway signal, not a bug.
- **Regenerating replaces the latest model** (a new row); the previous model remains reachable by id only.
- **Sensitivity is client-side.** The revenue "what-if" slider is computed **by the FE** from the returned
  numbers/assumptions; there is **no** server endpoint for sensitivity in v1.
- **XLSX export is DEFERRED.** There is no export endpoint; the **Export** button is FE-only for now
  (e.g. build a CSV from `months` × `rows` client-side, or hide/disable the button). Do not wait for a backend
  export route.

---

## Balance-sheet check (verified live)

The e2e reads the balance-sheet rows **from the captured response body** and asserts, for each of the 12
months, `Cash + Accounts receivable == Accounts payable + Retained earnings + Paid-in capital`, and that
`Closing cash` in the cash-flow statement equals the balance-sheet `Cash` row. Both hold exactly (integer
arithmetic). The FE can rely on the identity and may show a "balanced" indicator, but should not re-derive
the statements.

## Errors

Standard envelope (no `data`/`meta`), same as the rest of `/finance`:

| Status | Code | When | Live capture |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | missing/invalid access token | ⚠️ not captured for this feature (identical shared behaviour) |
| 403 | `EMAIL_NOT_VERIFIED` | email not verified | ⚠️ not captured |
| 403 | `FORBIDDEN` | membership role is not founder / team_member / accountant | ⚠️ not captured (unit) |
| 404 | `NOT_FOUND` | unknown model id or another workspace's model | ⚠️ not captured (unit) |
| 422 | `VALIDATION_ERROR` | malformed (non-UUID) `{id}` path | ⚠️ not captured (from source) |

There is **no** 4xx for "generation failed": that is a **200** with `status: "failed"` (poll result), not an
HTTP error. Only the POST returns 202.

## Known limitations (be honest with users)

- 12-month horizon only (no 36-month view yet).
- Opex is a single line (not itemised by category).
- Actuals come from the Cash Flow ledger only (bank-account actuals arrive with the Integrations slice).
- Currency is hardcoded `NGN`; no multi-currency conversion.
- Financing inflows in the trailing window are counted as revenue (see above).
- No concurrent-generation dedup (two POSTs → two AI jobs).
- The paid-in-capital balancing plug makes this a startup planning model, not GAAP-compliant statements.

## Verification

Legend: ✅ **verified live** = asserted in `e2e/test_financial_model.py` against the running app and the body is in
`e2e/_captures/financial_model/`. ⚠️ **unit-only / source-only** = asserted by the named unit test or read from
source; not exercised live.

| Claim | Status | Source |
|---|---|---|
| `POST /finance/model/generate` → **202** `{id, status: "generating"}` | ✅ verified live | `generate_accepted.json` |
| `GET /finance/model` with no model → 200 `{"status": "none"}` | ✅ verified live | `latest_none.json`; `test_get_latest_when_none` |
| `GET /finance/model` while generating → `status: "generating"`, `id` present, statements/assumptions/months `null` | ✅ verified live | `latest_generating.json` |
| Worker completes → `status: "complete"`, `error: null`, `horizon_months: 12`, `months` has 12 `YYYY-MM` entries | ✅ verified live (stub LLM) | `latest_complete.json` |
| All 3 statements present; each is `{rows}` only (no nested `months`); every row has 12 integer values | ✅ verified live | `latest_complete.json` |
| Row labels per statement as tabulated above | ✅ verified live | `latest_complete.json` |
| `assumptions` has the six keys; stub returns zeros + `[stub-llm] narrative` | ✅ verified live (stub); real-LLM values ⚠️ not exercised | `latest_complete.json` |
| **Balance sheet balances every month** (Cash + AR == AP + Retained earnings + Paid-in capital) | ✅ verified live | asserted in the e2e on the captured body; `tests/services/test_model_engine.py` |
| Cash-flow `Closing cash` == balance-sheet `Cash` | ✅ verified live | `latest_complete.json` |
| Seeded from actuals (revenue 2,000,000 = 6M inflow ÷ 3; opening cash 74,000,000 = 100M + 6M − 32M) | ✅ verified live | `latest_complete.json` |
| `GET /finance/model/{id}` returns the identical `data` object | ✅ verified live | `model_by_id.json` |
| Assumptions are clamped / never raise on bad LLM output | ⚠️ unit-only | `tests/services/test_model_prompt.py` |
| `failed` state with `error: "AI budget exceeded"` (over budget) | ⚠️ unit-only (stub LLM always completes) | `tests/worker/test_finance_model_handler.py` |
| Re-generate creates a second row; latest is the newest | ⚠️ unit-only | `test_regenerate_creates_second_row_and_latest_is_newest` |
| RBAC: founder / team_member / accountant allowed; other roles → 403 | ⚠️ unit-only | `test_model_rbac_forbidden`, `test_accountant_can_generate_and_get` |
| Cross-workspace id → 404; latest scoped to own workspace | ⚠️ unit-only | `test_cross_tenant_get_is_404`, `test_latest_is_scoped_to_own_startup` |
| Error bodies (401 / 403 / 404 / 422) for these routes | ⚠️ not captured | `app/core/errors.py` |
| Real-LLM (non-stub) numbers | ⚠️ not exercised live (e2e is stub-only) | — |
| Sensitivity is client-side; XLSX export deferred | ⚠️ by design — no backend route | — |
