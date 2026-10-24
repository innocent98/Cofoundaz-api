# FE integration guide: Funding is cash, not revenue (D-FIN-11)

**What changed (2026-10-24, branch `fix/funding-not-revenue`):** a ledger row in the **`Funding`** category (a raise, a founder top-up, a grant) no longer counts as revenue and no longer reduces burn. It still adds to cash on hand, still shows as money in on the cash-flow chart, and is the financial model's paid-in capital.

**No response shape changed.** No field was added, renamed or removed; only the numbers in `monthly_revenue`, `monthly_burn`, `runway_months` / `runway_low`, the dashboard Revenue and Runway tiles, and a newly generated model's starting revenue now leave Funding out.

Every body below is copied from `e2e/_captures/finance_funding/*.json`, captured live by `e2e/test_finance_funding_not_revenue.py` against the running app (isolated `cofoundaz_e2e` DB). Amounts are kobo (`/ 100` for naira).

## The scenario captured
All three rows are dated today:

| Row | category | direction | amount_minor |
|---|---|---|---|
| Seed round | `Funding` | in | 30,000,000 (₦300,000) |
| Customer payments | `Revenue` | in | 1,500,000 (₦15,000) |
| Team salaries | `Payroll` | out | 4,500,000 (₦45,000) |

Before this change, revenue would have been (30,000,000 + 1,500,000) / 3 = 10,500,000 a month, burn 0, and runway `null` ("not burning"). *These "before" numbers are derived from the old formula, not captured; the old code is no longer on this branch.*

## `GET /api/v1/finance/cash-flow` (200)
`e2e/_captures/finance_funding/cash_flow.json`, verbatim:
```json
{"data": {"cash_on_hand": 27000000, "monthly_burn": 1000000, "monthly_revenue": 500000, "runway_months": 27.0, "runway_low": false, "currency": "NGN", "by_month": [{"month": "2026-05", "inflow": 0, "outflow": 0, "net": 0}, {"month": "2026-06", "inflow": 0, "outflow": 0, "net": 0}, {"month": "2026-07", "inflow": 0, "outflow": 0, "net": 0}, {"month": "2026-08", "inflow": 0, "outflow": 0, "net": 0}, {"month": "2026-09", "inflow": 0, "outflow": 0, "net": 0}, {"month": "2026-10", "inflow": 31500000, "outflow": 4500000, "net": 27000000}]}, "meta": null}
```
- `monthly_revenue` 500,000 = 1,500,000 / 3: the raise is out.
- `monthly_burn` 1,000,000 = (4,500,000 − 1,500,000) / 3: the raise does not cancel it.
- `runway_months` 27.0 = 27,000,000 / 1,000,000.
- **Trap:** `by_month[].inflow` for October is 31,500,000 and **includes** the raise. The chart is money moving; the KPI cards are operating figures. Don't add `by_month` inflows up and label the result "revenue".

## `GET /api/v1/finance/runway` (200), `baseline`
`e2e/_captures/finance_funding/runway.json`, trimmed to `assumptions` + `baseline` (the `scenarios` block is unchanged in shape):
```json
{
  "assumptions": {"mom_growth_percent": 0, "hiring_spend_minor": 0, "one_off_costs_minor": 0},
  "baseline": {"cash_on_hand": 27000000, "monthly_burn": 1000000, "monthly_revenue": 500000, "currency": "NGN"}
}
```
In the same capture, `scenarios.base.runway_months` is `null` with `avg_net_burn_minor: 1000000`: 27 months is past the projection horizon, so there's no cash-out date inside it. That's existing behaviour, see `fe-integration-guide-finance-runway.md`.

## `GET /api/v1/dashboard/summary` (200), KPI tiles
`e2e/_captures/finance_funding/dashboard_summary.json`, trimmed to `data.kpis.metrics.revenue` / `.runway`:
```json
{"revenue": {"status": "ok", "value_minor": 500000, "currency": "NGN", "basis": "trailing_3mo_avg", "trend": {"delta": null, "series": []}}, "runway": {"status": "ok", "months": 27.0, "is_low": false, "monthly_burn_minor": 1000000, "cash_on_hand_minor": 27000000, "currency": "NGN", "trend": {"delta": null, "series": []}}}
```

## `GET /api/v1/finance/model/{id}` (200) after `POST /finance/model/generate`
`e2e/_captures/finance_funding/model_complete.json`, `assumptions` verbatim (the narrative is the stub model's):
```json
{
  "ap_days": 0,
  "ar_days": 0,
  "narrative": "[stub-llm] narrative",
  "revenue_source": "actuals",
  "revenue_0_minor": 500000,
  "cogs_pct_of_revenue": 0,
  "monthly_opex_growth_pct": 0.0,
  "monthly_revenue_growth_pct": 0.0,
  "monthly_costs_0_minor": 1500000
}
```
From the same capture, first three months: `pnl` "Revenue" `[500000, 500000, 500000]`; `balance_sheet` "Paid-in capital" `[30000000, 30000000, 30000000]`; "Cash" `[26000000, 25000000, 24000000]`; `balance_check` `{"balanced": true, "max_abs_difference_minor": 0}`. The raise is now capital only, not also revenue.

## What the FE should do
1. **Make "Funding" easy to pick for a raise.** The rule keys off the category, so an inflow left uncategorized still counts as revenue. In the transaction form and the uncategorized-review list, make `Funding` (already in the category list) easy to choose. Consider a nudge on a large one-off inflow: "Is this a raise or investment? Categorise it as Funding so it isn't counted as revenue."
2. **Label the cards honestly.** "Monthly revenue (last 3 months, funding excluded)" and "Net burn". Keep the chart labelled "Money in / Money out".
3. **Saved models don't change by themselves.** A model generated before this change keeps its old starting revenue. "Recalculate with fresh actuals" (`POST /finance/model/{id}/recalculate` with `refresh_actuals`) or a new generate picks up the new figure. If the founder sees an old model next to new KPIs, the model's `stale` flag and the recalculate control are how they reconcile.
4. **Expect founders to see a visible change.** A founder who recorded a raise in the last 3 months will see revenue drop and runway appear on their next visit. That's correct, but surprising. A one-line note on the Finance overview ("Funding is no longer counted as revenue") avoids a support question.

## Errors
None new. Every endpoint keeps its existing error shapes; see the guides linked above.

## Verification
| Behaviour | Status |
|---|---|
| Cash-flow `monthly_revenue` / `monthly_burn` / `runway_months` exclude Funding | ✅ verified live (capture above) |
| `by_month` still counts Funding as inflow; `cash_on_hand` includes it | ✅ verified live |
| Runway `baseline` excludes Funding | ✅ verified live |
| Dashboard Revenue / Runway tiles exclude Funding | ✅ verified live |
| New model: `revenue_0_minor` excludes Funding; paid-in capital = the raise; balanced | ✅ verified live |
| Category match is case-insensitive (`funding` works) | unit test only (`tests/api/test_finance.py::test_funding_is_cash_but_not_revenue_or_burn_offset`) |
| AI briefing prompt wording ("funding excluded") | unit-level only; the stub model doesn't echo the prompt |
| "Before" numbers in the scenario section | derived from the old formula, not captured |
