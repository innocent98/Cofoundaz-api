"""Pure runway math shared by cash-flow and runway services.

Deliberately free of DB/ORM imports and of any import of cashflow.py so the import graph
stays acyclic (cashflow -> runway_math; runway -> cashflow, runway_math).
"""

import math
from datetime import date

from app.services.finance.scenario_config import RUNWAY_HORIZON_MONTHS, SCENARIOS


def _runway_is_low(monthly_burn: int, runway_months: float | None) -> bool:
    # Single source of truth shared with cash_flow_summary's runway_low flag.
    return monthly_burn > 0 and (runway_months is None or runway_months < 6)


def _add_months(d: date, n: int) -> str:
    y, m = d.year, d.month + n
    while m > 12:
        m -= 12
        y += 1
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _project_one(
    cash0: int,
    monthly_rev: int,
    monthly_costs: int,
    growth_pct: int,
    hire: int,
    cost_mult: float,
    horizon: int,
) -> tuple[list[dict], float | None, int]:
    """Returns (balances, UNROUNDED fractional runway or None, avg net burn)."""
    balances: list[dict] = []
    prev = cash0
    runway: float | None = None
    net_sum = 0
    if cash0 <= 0:  # no starting cash (after one-offs): out of cash now, in every scenario
        runway = 0.0
    for i in range(1, horizon + 1):
        rev_i = round(monthly_rev * ((1 + growth_pct / 100) ** i))
        cost_i = round(monthly_costs * cost_mult + hire)
        net_i = rev_i - cost_i
        net_sum += cost_i - rev_i  # burn = costs - revenue
        cur = prev + net_i
        balances.append({"month": None, "cash_balance": cur, "net": net_i})  # month set by caller
        if runway is None and prev > 0 >= cur:  # crossed zero this month
            frac = prev / (prev - cur) if prev != cur else 0.0
            runway = (i - 1) + frac  # unrounded; callers round for display only
        prev = cur
    avg_net_burn = max(0, round(net_sum / horizon))
    return balances, runway, avg_net_burn


def project_scenarios(baseline: dict, assumptions: dict, *, today: date) -> dict[str, dict]:
    """Project base/best/worst cash over RUNWAY_HORIZON_MONTHS.

    `baseline` keys: cash_on_hand, monthly_revenue, monthly_costs (actual, unclamped), currency.

    `by_month[i]` is the projected END-of-month-i cash balance, labelled i months after `today`.
    `cash_out_date` is the interpolated month the balance reaches zero, so it can differ from the
    first non-positive `by_month` label by up to one month. `runway_months` is rounded to 1
    decimal for display; `cash_out_date` is derived from the unrounded value.
    """
    monthly_rev = int(baseline["monthly_revenue"])
    monthly_costs = int(baseline["monthly_costs"])
    cash0 = int(baseline["cash_on_hand"]) - int(assumptions["one_off_costs_minor"])
    g = int(assumptions["mom_growth_percent"])
    hire = int(assumptions["hiring_spend_minor"])
    out: dict[str, dict] = {}
    for name, (delta_pp, cost_mult) in SCENARIOS.items():
        growth = max(0, g + delta_pp)
        balances, runway, avg_burn = _project_one(
            cash0, monthly_rev, monthly_costs, growth, hire, cost_mult, RUNWAY_HORIZON_MONTHS
        )
        for idx, b in enumerate(balances, start=1):
            b["month"] = _add_months(today, idx)
        cash_out = _add_months(today, math.floor(runway)) if runway is not None else None
        out[name] = {
            "runway_months": round(runway, 1) if runway is not None else None,
            "cash_out_date": cash_out,
            "avg_net_burn_minor": avg_burn,
            "by_month": balances,
        }
    return out
