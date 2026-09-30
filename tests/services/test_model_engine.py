from datetime import date

import pytest

from app.services.finance.model_engine import project_three_statements


def _assume(g=0.0, cogs=0.0, og=0.0, ar=0, ap=0):
    return {
        "monthly_revenue_growth_pct": g,
        "cogs_pct_of_revenue": cogs,
        "monthly_opex_growth_pct": og,
        "ar_days": ar,
        "ap_days": ap,
        "narrative": "x",
    }


def _start(rev=10_000_000, costs=8_000_000, cash=50_000_000):
    return {"revenue_0": rev, "monthly_costs_0": costs, "cash_on_hand": cash, "currency": "NGN"}


@pytest.mark.parametrize(
    "a",
    [
        _assume(),
        _assume(g=10, cogs=30, og=5, ar=30, ap=45),
        _assume(g=-5, cogs=60, og=0, ar=0, ap=0),
        _assume(g=100, cogs=90, og=20, ar=90, ap=120),
    ],
)
def test_balance_sheet_balances_every_month(a):
    m = project_three_statements(_start(), a, today=date(2026, 4, 1))
    bs = m["balance_sheet_raw"]
    for i in range(12):
        assets = bs["cash"][i] + bs["accounts_receivable"][i]
        liab_equity = bs["accounts_payable"][i] + bs["paid_in_capital"] + bs["retained_earnings"][i]
        assert assets == liab_equity


def test_pnl_identities_and_cash_chain():
    m = project_three_statements(
        _start(rev=10_000_000, costs=8_000_000, cash=50_000_000),
        _assume(g=0, cogs=25, og=0, ar=0, ap=0),
        today=date(2026, 4, 1),
    )
    p = m["pnl_raw"]
    for i in range(12):
        assert p["gross_profit"][i] == p["revenue"][i] - p["cogs"][i]
        assert p["net_income"][i] == p["gross_profit"][i] - p["opex"][i]
    cf = m["cash_flow_raw"]
    assert cf["closing_cash"][0] == 50_000_000 + p["net_income"][0]


def test_twelve_months_and_no_overflow_on_extreme_growth():
    m = project_three_statements(
        _start(), _assume(g=100, cogs=50, og=50, ar=60, ap=60), today=date(2026, 4, 1)
    )
    assert len(m["months"]) == 12
    assert len(m["pnl_raw"]["revenue"]) == 12


def test_months_start_after_today_with_year_rollover():
    m = project_three_statements(_start(), _assume(), today=date(2026, 11, 15))
    assert m["months"][0] == "2026-12"
    assert m["months"][1] == "2027-01"
    assert m["months"][-1] == "2027-11"


def test_display_statement_shapes():
    m = project_three_statements(
        _start(), _assume(g=5, cogs=30, ar=30, ap=30), today=date(2026, 4, 1)
    )
    assert [r["label"] for r in m["pnl"]["rows"]] == [
        "Revenue",
        "COGS",
        "Gross profit",
        "Opex",
        "Net income",
    ]
    assert [r["label"] for r in m["cash_flow"]["rows"]] == [
        "Opening cash",
        "Collections",
        "Payments",
        "Net cash flow",
        "Closing cash",
    ]
    assert [r["label"] for r in m["balance_sheet"]["rows"]] == [
        "Cash",
        "Accounts receivable",
        "Accounts payable",
        "Retained earnings",
        "Paid-in capital",
    ]
    assert all(
        len(r["values"]) == 12 for k in ("pnl", "cash_flow", "balance_sheet") for r in m[k]["rows"]
    )
    assert m["currency"] == "NGN"
    opening = m["cash_flow"]["rows"][0]["values"]
    closing = m["cash_flow"]["rows"][4]["values"]
    assert opening[0] == 50_000_000
    assert opening[1:] == closing[:-1]
