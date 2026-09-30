"""Pure deterministic 3-statement projection engine.

No I/O, no DB, no LLM. All figures are Python ints. The balance-sheet identity
    cash + AR == AP + paid_in_capital + retained_earnings
holds exactly every month by construction (net_income = revenue - total_costs,
collections = revenue - dAR, payments = total_costs - dAP), and is asserted per month.
"""

from datetime import date
from typing import Any

from app.services.finance.model_config import (
    BALANCE_SHEET_ROWS,
    CASH_FLOW_ROWS,
    MODEL_HORIZON_MONTHS,
    PNL_ROWS,
)


def _add_months(d: date, k: int) -> str:
    total = d.year * 12 + (d.month - 1) + k
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _rows(labels: tuple[str, ...], series: list[list[int]]) -> dict[str, Any]:
    return {
        "rows": [{"label": lb, "values": vals} for lb, vals in zip(labels, series, strict=True)]
    }


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

    g = assumptions["monthly_revenue_growth_pct"] / 100
    og = assumptions["monthly_opex_growth_pct"] / 100
    cp = assumptions["cogs_pct_of_revenue"] / 100
    ar_days = assumptions["ar_days"]
    ap_days = assumptions["ap_days"]

    cogs_0 = round(cp * revenue_0)
    opex_0 = max(0, monthly_costs_0 - cogs_0)
    ar_prev = round(revenue_0 * ar_days / 30)
    ap_prev = round((cogs_0 + opex_0) * ap_days / 30)
    cash_prev = cash_0
    re_prev = 0
    paid_in_capital = cash_prev + ar_prev - ap_prev - re_prev

    revenue: list[int] = []
    cogs: list[int] = []
    gross: list[int] = []
    opex: list[int] = []
    net_income: list[int] = []
    opening: list[int] = []
    collections: list[int] = []
    payments: list[int] = []
    net_cash: list[int] = []
    closing: list[int] = []
    ar: list[int] = []
    ap: list[int] = []
    re: list[int] = []

    for i in range(1, horizon + 1):
        rev_i = round(revenue_0 * (1 + g) ** i)
        cogs_i = round(cp * rev_i)
        gross_i = rev_i - cogs_i
        opex_i = round(opex_0 * (1 + og) ** i)
        ni_i = gross_i - opex_i
        costs_i = cogs_i + opex_i
        ar_i = round(rev_i * ar_days / 30)
        ap_i = round(costs_i * ap_days / 30)
        coll_i = rev_i - (ar_i - ar_prev)
        pay_i = costs_i - (ap_i - ap_prev)
        net_i = coll_i - pay_i
        close_i = cash_prev + net_i
        re_i = re_prev + ni_i

        # Defensive invariant: pure integer equality, no side effects. Stripping it under -O only
        # loses the guard; the identity still holds by construction.
        assert (
            close_i + ar_i == ap_i + paid_in_capital + re_i
        ), f"balance sheet out of balance at month {i}"  # nosec B101

        revenue.append(rev_i)
        cogs.append(cogs_i)
        gross.append(gross_i)
        opex.append(opex_i)
        net_income.append(ni_i)
        opening.append(cash_prev)
        collections.append(coll_i)
        payments.append(pay_i)
        net_cash.append(net_i)
        closing.append(close_i)
        ar.append(ar_i)
        ap.append(ap_i)
        re.append(re_i)

        ar_prev, ap_prev, cash_prev, re_prev = ar_i, ap_i, close_i, re_i

    pic_series = [paid_in_capital] * horizon
    return {
        "months": [_add_months(today, k) for k in range(1, horizon + 1)],
        "currency": starting["currency"],
        "pnl": _rows(PNL_ROWS, [revenue, cogs, gross, opex, net_income]),
        "cash_flow": _rows(CASH_FLOW_ROWS, [opening, collections, payments, net_cash, closing]),
        "balance_sheet": _rows(BALANCE_SHEET_ROWS, [closing, ar, ap, re, pic_series]),
        "pnl_raw": {
            "revenue": revenue,
            "cogs": cogs,
            "gross_profit": gross,
            "opex": opex,
            "net_income": net_income,
        },
        "cash_flow_raw": {
            "opening_cash": opening,
            "collections": collections,
            "payments": payments,
            "net_cash": net_cash,
            "closing_cash": closing,
        },
        "balance_sheet_raw": {
            "cash": closing,
            "accounts_receivable": ar,
            "accounts_payable": ap,
            "retained_earnings": re,
            "paid_in_capital": paid_in_capital,
        },
    }
