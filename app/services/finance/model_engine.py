"""Pure deterministic 3-statement projection engine.

No I/O, no DB, no LLM. All figures are Python ints (kobo).

The opening balance sheet comes from actuals: cash on hand, receivables and payables from the
day-count assumptions, paid-in capital from the funding the ledger recorded, and retained
earnings as whatever equity is left (the history of profit and burn). From there every month is
rolled forward by two independent routes, which is what makes the balance check meaningful:

  - cash moves through the cash-flow statement (collections - payments - capex);
  - retained earnings move through the P&L (net income, after depreciation).

    total assets = cash + receivables + net fixed assets
    total liabilities + equity = payables + paid-in capital + retained earnings

The two sides agree every month (the `Balance check` row is zero) only if the receivable,
payable, depreciation and capex treatment is consistent. The result reports the check instead
of asserting it, so a bad edit surfaces as `balanced: false` in the API and never as a crash in
the worker.
"""

from datetime import date
from typing import Any

from app.services.finance.model_config import (
    BALANCE_SHEET_ROWS,
    CASH_FLOW_ROWS,
    MODEL_HORIZON_MONTHS,
    MONTHS_PER_YEAR,
    PNL_ROWS,
    ROLLUP_KIND,
)

DEFAULT_DEPRECIATION_MONTHS = 36


def add_months(d: date, k: int) -> str:
    total = d.year * 12 + (d.month - 1) + k
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _rows(labels: tuple[str, ...], series: list[list[int]]) -> dict[str, Any]:
    return {
        "rows": [{"label": lb, "values": vals} for lb, vals in zip(labels, series, strict=True)]
    }


def _month_index(months: list[str]) -> dict[str, int]:
    """'YYYY-MM' -> 1-based projection month."""
    return {m: i for i, m in enumerate(months, start=1)}


def _depreciation(capex_by_month: dict[int, int], life: int, horizon: int) -> list[int]:
    """Straight-line from the month after purchase; integer-exact (the last month absorbs rounding)."""
    out = [0] * horizon
    for bought, cost in capex_by_month.items():
        for i in range(bought + 1, horizon + 1):
            elapsed = i - bought
            if elapsed > life:
                break
            out[i - 1] += cost * elapsed // life - cost * (elapsed - 1) // life
    return out


def project_three_statements(
    starting: dict[str, Any],
    assumptions: dict[str, Any],
    *,
    today: date,
    horizon: int = MODEL_HORIZON_MONTHS,
) -> dict[str, Any]:
    revenue_0 = int(starting["revenue_0"])
    monthly_costs_0 = int(starting["monthly_costs_0"])
    cash_0 = int(starting["cash_on_hand"])
    funding_total = max(0, int(starting.get("funding_total") or 0))

    g = assumptions["monthly_revenue_growth_pct"] / 100
    og = assumptions["monthly_opex_growth_pct"] / 100
    cp = assumptions["cogs_pct_of_revenue"] / 100
    ar_days = assumptions["ar_days"]
    ap_days = assumptions["ap_days"]
    life = max(1, int(assumptions.get("depreciation_months") or DEFAULT_DEPRECIATION_MONTHS))

    months = [add_months(today, k) for k in range(1, horizon + 1)]
    idx = _month_index(months)

    hires = [0] * horizon
    for hire in assumptions.get("hiring_plan") or []:
        start = idx.get(hire["start_month"], 1 if hire["start_month"] < months[0] else horizon + 1)
        for i in range(start, horizon + 1):
            hires[i - 1] += int(hire["monthly_cost_minor"])
    one_offs = [0] * horizon
    for item in assumptions.get("one_offs") or []:
        if item["month"] in idx:
            one_offs[idx[item["month"]] - 1] += int(item["amount_minor"])
    capex_by_month: dict[int, int] = {}
    for item in assumptions.get("capex") or []:
        if item["month"] in idx:
            i = idx[item["month"]]
            capex_by_month[i] = capex_by_month.get(i, 0) + int(item["amount_minor"])
    dep = _depreciation(capex_by_month, life, horizon)

    cogs_0 = round(cp * revenue_0)
    opex_0 = max(0, monthly_costs_0 - cogs_0)
    ar_prev = round(revenue_0 * ar_days / 30)
    ap_prev = round((cogs_0 + opex_0) * ap_days / 30)
    cash_prev = cash_0
    fa_prev = 0
    equity_0 = cash_prev + ar_prev + fa_prev - ap_prev
    paid_in_capital = funding_total
    re_prev = equity_0 - paid_in_capital

    series: dict[str, list[int]] = {
        k: []
        for k in (
            "revenue",
            "cogs",
            "gross",
            "opex",
            "dep",
            "ni",
            "opening",
            "collections",
            "payments",
            "capex",
            "net_cash",
            "closing",
            "ar",
            "fa",
            "assets",
            "ap",
            "liabilities",
            "equity",
            "le",
            "check",
            "re",
        )
    }

    for i in range(1, horizon + 1):
        rev_i = round(revenue_0 * (1 + g) ** i)
        cogs_i = round(cp * rev_i)
        gross_i = rev_i - cogs_i
        opex_i = round(opex_0 * (1 + og) ** i) + hires[i - 1] + one_offs[i - 1]
        dep_i = dep[i - 1]
        ni_i = gross_i - opex_i - dep_i
        costs_i = cogs_i + opex_i
        capex_i = capex_by_month.get(i, 0)
        ar_i = round(rev_i * ar_days / 30)
        ap_i = round(costs_i * ap_days / 30)
        coll_i = rev_i - (ar_i - ar_prev)
        pay_i = costs_i - (ap_i - ap_prev)
        net_i = coll_i - pay_i - capex_i
        close_i = cash_prev + net_i
        fa_i = fa_prev + capex_i - dep_i
        re_i = re_prev + ni_i

        assets_i = close_i + ar_i + fa_i
        equity_i = paid_in_capital + re_i
        le_i = ap_i + equity_i

        for key, val in (
            ("revenue", rev_i),
            ("cogs", cogs_i),
            ("gross", gross_i),
            ("opex", opex_i),
            ("dep", dep_i),
            ("ni", ni_i),
            ("opening", cash_prev),
            ("collections", coll_i),
            ("payments", pay_i),
            ("capex", capex_i),
            ("net_cash", net_i),
            ("closing", close_i),
            ("ar", ar_i),
            ("fa", fa_i),
            ("assets", assets_i),
            ("ap", ap_i),
            ("liabilities", ap_i),
            ("equity", equity_i),
            ("le", le_i),
            ("check", assets_i - le_i),
            ("re", re_i),
        ):
            series[key].append(val)

        ar_prev, ap_prev, cash_prev, fa_prev, re_prev = ar_i, ap_i, close_i, fa_i, re_i

    s = series
    pic_series = [paid_in_capital] * horizon
    worst = max((abs(c) for c in s["check"]), default=0)
    return {
        "months": months,
        "currency": starting["currency"],
        "pnl": _rows(PNL_ROWS, [s["revenue"], s["cogs"], s["gross"], s["opex"], s["dep"], s["ni"]]),
        "cash_flow": _rows(
            CASH_FLOW_ROWS,
            [
                s["opening"],
                s["collections"],
                s["payments"],
                s["capex"],
                s["net_cash"],
                s["closing"],
            ],
        ),
        "balance_sheet": _rows(
            BALANCE_SHEET_ROWS,
            [
                s["closing"],
                s["ar"],
                s["fa"],
                s["assets"],
                s["ap"],
                s["liabilities"],
                pic_series,
                s["re"],
                s["equity"],
                s["le"],
                s["check"],
            ],
        ),
        "balance_check": {"balanced": worst == 0, "max_abs_difference_minor": worst},
    }


def rollup_yearly(
    statement: dict[str, Any] | None, months: list[str] | None
) -> dict[str, Any] | None:
    """Fold one stored statement (rows of monthly values) into projection years.

    Year 1 is projection months 1-12, and so on; a final partial year (a 10-month legacy model)
    is rolled up as it stands. Flows add, opening balances take the first month, balances take
    the last month (see ROLLUP_KIND). Unknown labels are treated as balances.
    """
    if not statement or not months:
        return None
    n = len(months)
    spans = [(a, min(a + MONTHS_PER_YEAR, n)) for a in range(0, n, MONTHS_PER_YEAR)]
    rows = []
    for row in statement["rows"]:
        kind = ROLLUP_KIND.get(row["label"], "last")
        vals = row["values"]
        folded = []
        for a, b in spans:
            window = vals[a:b]
            folded.append(
                sum(window) if kind == "sum" else window[0] if kind == "first" else window[-1]
            )
        rows.append({"label": row["label"], "values": folded})
    return {"rows": rows}


def yearly_periods(months: list[str]) -> list[dict[str, Any]]:
    n = len(months)
    out = []
    for number, a in enumerate(range(0, n, MONTHS_PER_YEAR), start=1):
        b = min(a + MONTHS_PER_YEAR, n)
        out.append(
            {"label": f"Year {number}", "start_month": months[a], "end_month": months[b - 1]}
        )
    return out
