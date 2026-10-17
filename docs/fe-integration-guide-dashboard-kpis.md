# FE Integration Guide — Dashboard KPI tiles (`kpis.metrics`)

Every body below is copied from a **live capture**: `e2e/test_dashboard_kpis.py`, run on 2026-10-17
through `scripts/e2e_run.sh` (real uvicorn, real Postgres). The captures are committed at
`e2e/_captures/dashboard_kpis/*.json`. Each excerpt below is the `data.kpis` object of the
full `GET /dashboard/summary` response in that file, trimmed to that one section only. Nothing was
retyped by hand. The same shape was also confirmed on the local docker stack (`:8000`) against
the local test founder, an empty workspace that returned `summary_empty`'s shape exactly.

This **extends** `fe-integration-guide-dashboard.md`. That guide's KPI rows (§ "always `null` in v1")
still hold for the **legacy scalar keys**. The real figures now live in a new nested object.

---

## 1. What changed, in FE terms

The four dashboard tiles **Monthly Revenue / Runway / Pipeline Value / Campaign CTR** used to be
unconditionally "Coming Soon", because the backend sent `null` for all four. The backend now reads
them from the Finance and Marketing modules and returns them, **each with a `status`**, under
`data.kpis.metrics`.

- Same endpoint, same auth: `GET /api/v1/dashboard/summary`, Bearer token + `X-Workspace-Id`, any
  active member. No new route, no polling: values are computed on every summary load.
- **Additive, non-breaking.** `kpis.revenue`, `kpis.runway`, `kpis.pipeline_value` and
  `kpis.campaign_performance` are **still present and still always `null`**. The pre-change FE keeps
  rendering exactly as before. Read the new values from `kpis.metrics.*` and stop reading the
  legacy scalars. They will be removed in a later BE change.
- `kpis.tasks_done_this_week` is unchanged.

## 2. The `status` field — what each one means and what to render

| `status` | Meaning | Render |
|---|---|---|
| `ok` | A real figure. **May legitimately be `0`** (e.g. no inflows this quarter). | The value. |
| `no_data` | The module exists, but this workspace has recorded nothing it can use yet. | An empty state with a CTA into the module (e.g. "Add a transaction" → `/finance`). **Never "₦0", never "Coming Soon".** |
| `restricted` | The viewer's role can't read the source module. All value fields are `null`. | Hide the tile, or show a neutral "Not available for your role". Never a number. |
| `not_built` | The source module has no backend yet. Only `pipeline` uses this (Sales). | "Coming Soon". This is the **only** status that should still say Coming Soon. |
| `not_burning` | **Runway only.** Break-even or profitable over the trailing window: there is no runway to count down. `months` is `null`. | e.g. "Profitable" / "Not burning". Never "0 mo", never "∞ mo" computed client-side. |
| `out_of_cash` | **Runway only.** Burning, and cash on hand is ≤ 0. `months` is `0`, `is_low` is `true`. | Alert styling, e.g. "Out of cash". |

A tile can also be **`{"error": true}`** (and nothing else) if its source query failed. Each metric
is isolated, so one failing tile doesn't blank the others. The whole `kpis` section can also be
`{"error": true}`, as before. **Check for the error marker before reading `status`.**

## 3. Field reference

| Path | Type | Notes |
|---|---|---|
| `metrics.revenue.value_minor` | int \| null | **Minor units (kobo)**. Average monthly **inflow** over the trailing 3 calendar-month window (`basis: "trailing_3mo_avg"`). Divide by 100 for naira. |
| `metrics.revenue.currency` | string \| null | ISO code (`"NGN"`), the workspace's most common transaction currency. `null` unless `ok`. |
| `metrics.runway.months` | float \| null | One decimal. Number only when `status == "ok"`; `0` when `out_of_cash`; `null` otherwise. |
| `metrics.runway.is_low` | bool | **Always a boolean, never null.** `true` when burning and either < 6 months or out of cash. Drive the red alert border from this, **not** from a client-side `< 6` check. |
| `metrics.runway.monthly_burn_minor` / `cash_on_hand_minor` | int \| null | Minor units. `cash_on_hand_minor` **can be negative** (`out_of_cash`). Populated for `ok` / `not_burning` / `out_of_cash`. |
| `metrics.pipeline.*` | — | Always `{"status":"not_built","value_minor":null,"currency":null}` until Sales ships. The shape is final, so wiring it now means no FE change later. |
| `metrics.campaign_ctr.ctr_percent` | float \| null | **Already a percentage** (`2.5` means 2.5%). Don't multiply by 100. One decimal. |
| `metrics.campaign_ctr.impressions` / `clicks` | int \| null | Totals over `range` (`"30d"`, the trailing 30 days). |

## 4. Captured responses

### 4.1 Empty workspace — `summary_empty.json`
```json
{
  "tasks_done_this_week": 0,
  "metrics": {
    "revenue": {
      "status": "no_data",
      "value_minor": null,
      "currency": null,
      "basis": "trailing_3mo_avg"
    },
    "runway": {
      "status": "no_data",
      "months": null,
      "is_low": false,
      "monthly_burn_minor": null,
      "cash_on_hand_minor": null,
      "currency": null
    },
    "pipeline": {
      "status": "not_built",
      "value_minor": null,
      "currency": null
    },
    "campaign_ctr": {
      "status": "no_data",
      "ctr_percent": null,
      "impressions": null,
      "clicks": null,
      "range": "30d"
    }
  },
  "revenue": null,
  "runway": null,
  "pipeline_value": null,
  "campaign_performance": null
}
```

### 4.2 Founder, burning, with campaign metrics — `summary_burning.json`
Seed: ₦600,000 inflow 200 days ago, then ₦30,000 in and ₦90,000 out today. Marketing: 2,000
impressions and 50 clicks today.
```json
{
  "tasks_done_this_week": 0,
  "metrics": {
    "revenue": {
      "status": "ok",
      "value_minor": 1000000,
      "currency": "NGN",
      "basis": "trailing_3mo_avg"
    },
    "runway": {
      "status": "ok",
      "months": 27.0,
      "is_low": false,
      "monthly_burn_minor": 2000000,
      "cash_on_hand_minor": 54000000,
      "currency": "NGN"
    },
    "pipeline": {
      "status": "not_built",
      "value_minor": null,
      "currency": null
    },
    "campaign_ctr": {
      "status": "ok",
      "ctr_percent": 2.5,
      "impressions": 2000,
      "clicks": 50,
      "range": "30d"
    }
  },
  "revenue": null,
  "runway": null,
  "pipeline_value": null,
  "campaign_performance": null
}
```

### 4.3 Out of cash — `summary_out_of_cash.json` (`metrics.runway` only)
After a further ₦1,000,000 outflow today.
```json
"runway": {
  "status": "out_of_cash",
  "months": 0,
  "is_low": true,
  "monthly_burn_minor": 35333333,
  "cash_on_hand_minor": -46000000,
  "currency": "NGN"
}
```

### 4.4 Not burning — `summary_not_burning.json` (`metrics.revenue` + `metrics.runway`)
After a further ₦2,000,000 inflow today.
```json
"revenue": {
  "status": "ok",
  "value_minor": 67666667,
  "currency": "NGN",
  "basis": "trailing_3mo_avg"
},
"runway": {
  "status": "not_burning",
  "months": null,
  "is_low": false,
  "monthly_burn_minor": 0,
  "cash_on_hand_minor": 154000000,
  "currency": "NGN"
}
```

### 4.5 Investor viewing the same workspace — `summary_investor.json` (`metrics` only)
```json
"metrics": {
  "revenue": {
    "status": "restricted",
    "value_minor": null,
    "currency": null,
    "basis": "trailing_3mo_avg"
  },
  "runway": {
    "status": "restricted",
    "months": null,
    "is_low": false,
    "monthly_burn_minor": null,
    "cash_on_hand_minor": null,
    "currency": null
  },
  "pipeline": {
    "status": "not_built",
    "value_minor": null,
    "currency": null
  },
  "campaign_ctr": {
    "status": "restricted",
    "ctr_percent": null,
    "impressions": null,
    "clicks": null,
    "range": "30d"
  }
}
```

### 4.6 Accountant viewing the same workspace — `summary_accountant.json`
`revenue` and `runway` are identical to §4.2 (`ok`, ₦10,000 / 27.0 mo). `campaign_ctr` is
`restricted` with the same null shape as §4.5.

## 5. Role matrix (mirrors the module route gates)

| Role | revenue / runway | campaign_ctr | pipeline |
|---|---|---|---|
| founder, team_member | real | real | `not_built` |
| accountant | real | `restricted` | `not_built` |
| mentor, investor, legal_advisor, business_consultant | `restricted` | `restricted` | `not_built` |

The sets come from `FINANCE_ROLES` / `MARKETING_ROLES` in `app/db/models/enums.py`. The Finance
and Marketing routes use the same constants, so the tiles can't drift from what those pages allow.

## 6. Traps

- **Nesting:** the real values are at `data.kpis.metrics.revenue`, **not** `data.kpis.revenue` (that
  key still exists and is still `null`). Reading the old key keeps "Coming Soon" forever.
- **Units:** money is **minor units**, as everywhere else in Finance. The current FE mapper's
  `formatCurrency(Number(v))` assumes major units, so divide by 100 first or it shows 100× too much.
- **`ctr_percent` is already a percent.**
- **`0` is data, `null` is not.** A `revenue.status == "ok"` with `value_minor: 0` is a real "no
  inflows this quarter". Show ₦0 for it. Only `no_data` gets the empty state.
- **Negative cash** is possible (`out_of_cash`). Format it with a minus sign; don't clamp it.
- **`is_low` vs the FE's own `< 6` check:** `kpi-strip.tsx` currently re-derives the alert from the
  label text and `parseFloat(value)`. Drive it from `is_low` instead. `out_of_cash` has `months: 0`,
  and `not_burning` has `months: null`, which the string heuristic can't tell apart.

## 7. UX consequences the FE must surface

- **"Monthly Revenue" is every inflow, raises included.** The Finance module has no revenue
  category yet, so an equity raise or a loan inside the trailing 3 months counts as "revenue" (see
  §4.4: one ₦2M inflow lifted revenue from ₦10K to ₦676.7K). It is the same figure the Finance hub
  shows (`/finance/cash-flow` → `monthly_revenue`). Label the tile honestly, e.g. a tooltip "Average
  monthly money in, last 3 months". Don't promise "recurring revenue".
- **Runway alerts are serious.** `out_of_cash` and `is_low` must show the alert styling. Don't hide
  them behind a hover.
- **Restricted roles:** an investor seeing four "Not available" tiles reads as broken. Prefer
  hiding the restricted tiles (the strip already reflows on its grid).
- **The empty state needs a working CTA.** As of this change the FE Finance and Marketing hubs are
  still mock data (`hooks/useFinanceApi.ts`, `hooks/useMarketingApi.ts`), so a founder **cannot yet
  create the data that lights these tiles from the UI**. A CTA to `/finance` lands on a mock page. Keep
  the CTA (it will be right once the hubs are wired) but don't promise "add a transaction to see
  this" in a way that a mock page contradicts.

## 8. Verification

| Behaviour | Verified live? | Source |
|---|---|---|
| Empty workspace → `no_data` ×3 + `not_built` | ✅ | `summary_empty.json`; also local docker `:8000` |
| Burning: revenue 1,000,000 kobo, runway 27.0 mo, CTR 2.5% | ✅ | `summary_burning.json` |
| `out_of_cash`: months 0, is_low true, negative cash | ✅ | `summary_out_of_cash.json` |
| `not_burning`: months null, burn 0 | ✅ | `summary_not_burning.json` |
| Investor → all `restricted`, no money fields | ✅ | `summary_investor.json` |
| Accountant → finance real, CTR `restricted` | ✅ | `summary_accountant.json` |
| Per-tile `{"error": true}` isolation | ❌ not live (needs an injected DB fault) | Unit test `tests/services/test_dashboard_kpis.py::test_one_failing_metric_does_not_blank_the_others`. Shape `{"error": true}` matches the existing section-error marker. |
| `ok` revenue with `value_minor: 0` | ❌ not live | Unit test `test_real_zero_revenue_is_ok_not_no_data` |
| Future-dated-only transactions → `no_data` | ❌ not live | Unit test `test_future_dated_transactions_alone_are_no_data` |
| `team_member`, `mentor`, `legal_advisor`, `business_consultant` roles | ❌ not live (investor + accountant were) | Unit tests `test_non_finance_roles_never_see_money` (parametrized) |
