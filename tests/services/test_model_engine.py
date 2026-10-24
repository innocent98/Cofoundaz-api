from datetime import date

import pytest

from app.services.finance.model_engine import (
    project_three_statements,
    rollup_yearly,
    yearly_periods,
)

TODAY = date(2026, 4, 1)


def _assume(g=0.0, cogs=0.0, og=0.0, ar=0, ap=0, **extra):
    return {
        "monthly_revenue_growth_pct": g,
        "cogs_pct_of_revenue": cogs,
        "monthly_opex_growth_pct": og,
        "ar_days": ar,
        "ap_days": ap,
        "narrative": "x",
        **extra,
    }


def _start(rev=10_000_000, costs=8_000_000, cash=50_000_000, funding=0):
    return {
        "revenue_0": rev,
        "monthly_costs_0": costs,
        "cash_on_hand": cash,
        "currency": "NGN",
        "funding_total": funding,
    }


def _row(m, statement, label):
    return next(r["values"] for r in m[statement]["rows"] if r["label"] == label)


@pytest.mark.parametrize(
    "a",
    [
        _assume(),
        _assume(g=10, cogs=30, og=5, ar=30, ap=45),
        _assume(g=-5, cogs=60, og=0, ar=0, ap=0),
        _assume(g=100, cogs=90, og=20, ar=90, ap=120),
        _assume(
            g=3,
            cogs=40,
            og=2,
            ar=45,
            ap=30,
            hiring_plan=[{"role": "Dev", "start_month": "2026-08", "monthly_cost_minor": 700_000}],
            one_offs=[{"label": "Legal", "month": "2026-06", "amount_minor": 1_234_567}],
            capex=[
                {"label": "Laptops", "month": "2026-05", "amount_minor": 9_999_999},
                {"label": "Server", "month": "2027-01", "amount_minor": 3_333_333},
            ],
            depreciation_months=7,
        ),
    ],
)
@pytest.mark.parametrize("funding", [0, 30_000_000, 90_000_000])
def test_balance_sheet_balances_every_month(a, funding):
    m = project_three_statements(_start(funding=funding), a, today=TODAY)
    assert m["balance_check"] == {"balanced": True, "max_abs_difference_minor": 0}
    assets = _row(m, "balance_sheet", "Total assets")
    le = _row(m, "balance_sheet", "Total liabilities + equity")
    check = _row(m, "balance_sheet", "Balance check")
    assert len(assets) == 36
    assert all(a_ - l_ == c for a_, l_, c in zip(assets, le, check, strict=True))
    assert set(check) == {0}


def test_opening_equity_is_seeded_from_funding_and_history():
    m = project_three_statements(
        _start(rev=10_000_000, costs=8_000_000, cash=50_000_000, funding=80_000_000),
        _assume(),
        today=TODAY,
    )
    # No AR/AP, no growth: cash 50m, equity 50m. Funding 80m is paid-in capital, so retained
    # earnings carry the 30m of history burn (-30m) plus this month's profit.
    pic = _row(m, "balance_sheet", "Paid-in capital")
    re_ = _row(m, "balance_sheet", "Retained earnings")
    ni = _row(m, "pnl", "Net income")
    assert pic == [80_000_000] * 36
    assert re_[0] == -30_000_000 + ni[0]
    assert re_[1] == re_[0] + ni[1]


def test_pnl_identities_and_cash_chain():
    m = project_three_statements(
        _start(rev=10_000_000, costs=8_000_000, cash=50_000_000),
        _assume(g=0, cogs=25, og=0, ar=0, ap=0),
        today=TODAY,
    )
    rev, cogs = _row(m, "pnl", "Revenue"), _row(m, "pnl", "COGS")
    gross, opex = _row(m, "pnl", "Gross profit"), _row(m, "pnl", "Opex")
    dep, ni = _row(m, "pnl", "Depreciation"), _row(m, "pnl", "Net income")
    for i in range(36):
        assert gross[i] == rev[i] - cogs[i]
        assert ni[i] == gross[i] - opex[i] - dep[i]
    assert _row(m, "cash_flow", "Closing cash")[0] == 50_000_000 + ni[0]


def test_thirty_six_months_and_no_overflow_on_extreme_growth():
    m = project_three_statements(
        _start(), _assume(g=100, cogs=50, og=50, ar=60, ap=60), today=TODAY
    )
    assert len(m["months"]) == 36
    assert len(_row(m, "pnl", "Revenue")) == 36
    assert m["balance_check"]["balanced"] is True


def test_months_start_after_today_with_year_rollover():
    m = project_three_statements(_start(), _assume(), today=date(2026, 11, 15))
    assert m["months"][0] == "2026-12"
    assert m["months"][1] == "2027-01"
    assert m["months"][-1] == "2029-11"


def test_display_statement_shapes():
    m = project_three_statements(_start(), _assume(g=5, cogs=30, ar=30, ap=30), today=TODAY)
    assert [r["label"] for r in m["pnl"]["rows"]] == [
        "Revenue",
        "COGS",
        "Gross profit",
        "Opex",
        "Depreciation",
        "Net income",
    ]
    assert [r["label"] for r in m["cash_flow"]["rows"]] == [
        "Opening cash",
        "Collections",
        "Payments",
        "Capex",
        "Net cash flow",
        "Closing cash",
    ]
    assert [r["label"] for r in m["balance_sheet"]["rows"]] == [
        "Cash",
        "Accounts receivable",
        "Fixed assets (net)",
        "Total assets",
        "Accounts payable",
        "Total liabilities",
        "Paid-in capital",
        "Retained earnings",
        "Total equity",
        "Total liabilities + equity",
        "Balance check",
    ]
    assert all(
        len(r["values"]) == 36 for k in ("pnl", "cash_flow", "balance_sheet") for r in m[k]["rows"]
    )
    assert m["currency"] == "NGN"
    opening = _row(m, "cash_flow", "Opening cash")
    closing = _row(m, "cash_flow", "Closing cash")
    assert opening[0] == 50_000_000
    assert opening[1:] == closing[:-1]


def test_edits_move_the_numbers_in_the_expected_direction():
    base = project_three_statements(_start(), _assume(g=2, cogs=30, og=1), today=TODAY)
    edited = project_three_statements(
        _start(),
        _assume(
            g=2,
            cogs=30,
            og=1,
            hiring_plan=[{"role": "Eng", "start_month": "2026-06", "monthly_cost_minor": 500_000}],
            one_offs=[{"label": "Audit", "month": "2026-07", "amount_minor": 2_000_000}],
        ),
        today=TODAY,
    )
    base_opex, new_opex = _row(base, "pnl", "Opex"), _row(edited, "pnl", "Opex")
    assert new_opex[0] == base_opex[0]  # 2026-05: before the hire
    assert new_opex[1] == base_opex[1] + 500_000  # 2026-06: hire starts
    assert new_opex[2] == base_opex[2] + 500_000 + 2_000_000  # 2026-07: hire + one-off
    assert new_opex[3] == base_opex[3] + 500_000  # the one-off does not repeat
    assert (
        _row(edited, "cash_flow", "Closing cash")[-1] < _row(base, "cash_flow", "Closing cash")[-1]
    )


def test_hire_starting_before_the_window_applies_from_month_one():
    m = project_three_statements(
        _start(),
        _assume(hiring_plan=[{"role": "X", "start_month": "2025-01", "monthly_cost_minor": 100}]),
        today=TODAY,
    )
    base = project_three_statements(_start(), _assume(), today=TODAY)
    assert _row(m, "pnl", "Opex")[0] == _row(base, "pnl", "Opex")[0] + 100


def test_items_outside_the_window_are_ignored():
    base = project_three_statements(_start(), _assume(), today=TODAY)
    m = project_three_statements(
        _start(),
        _assume(
            hiring_plan=[{"role": "X", "start_month": "2040-01", "monthly_cost_minor": 100}],
            one_offs=[{"label": "L", "month": "2040-01", "amount_minor": 100}],
            capex=[{"label": "C", "month": "2040-01", "amount_minor": 100}],
        ),
        today=TODAY,
    )
    assert m["pnl"] == base["pnl"]
    assert m["balance_sheet"] == base["balance_sheet"]


def test_capex_is_cash_out_then_depreciates_straight_line():
    m = project_three_statements(
        _start(),
        _assume(capex=[{"label": "Kit", "month": "2026-05", "amount_minor": 1_000_001}]),
        today=TODAY,
    )
    capex = _row(m, "cash_flow", "Capex")
    dep = _row(m, "pnl", "Depreciation")
    fa = _row(m, "balance_sheet", "Fixed assets (net)")
    assert capex[0] == 1_000_001 and sum(capex) == 1_000_001
    assert dep[0] == 0  # bought in month 1, depreciates from month 2
    # default life 36 months, 35 of them inside the horizon (bought in month 1)
    assert sum(dep) == 1_000_001 * 35 // 36
    assert fa[0] == 1_000_001
    assert fa[-1] == 1_000_001 - sum(dep)
    assert all(a >= b for a, b in zip(fa, fa[1:], strict=False))


def test_depreciation_life_is_respected():
    m = project_three_statements(
        _start(),
        _assume(
            capex=[{"label": "Kit", "month": "2026-05", "amount_minor": 1_200_000}],
            depreciation_months=12,
        ),
        today=TODAY,
    )
    dep = _row(m, "pnl", "Depreciation")
    assert dep[1:13] == [100_000] * 12
    assert sum(dep) == 1_200_000 and dep[13:] == [0] * 23


def test_yearly_rollup_sums_flows_and_takes_year_end_balances():
    m = project_three_statements(
        _start(funding=20_000_000), _assume(g=2, cogs=30, ar=15, ap=10), today=TODAY
    )
    months = m["months"]
    periods = yearly_periods(months)
    assert [p["label"] for p in periods] == ["Year 1", "Year 2", "Year 3"]
    assert periods[0] == {"label": "Year 1", "start_month": "2026-05", "end_month": "2027-04"}
    y = {
        key: {r["label"]: r["values"] for r in rollup_yearly(m[key], months)["rows"]}
        for key in ("pnl", "cash_flow", "balance_sheet")
    }
    rev = _row(m, "pnl", "Revenue")
    assert y["pnl"]["Revenue"] == [sum(rev[0:12]), sum(rev[12:24]), sum(rev[24:36])]
    opening = _row(m, "cash_flow", "Opening cash")
    closing = _row(m, "cash_flow", "Closing cash")
    assert y["cash_flow"]["Opening cash"] == [opening[0], opening[12], opening[24]]
    assert y["cash_flow"]["Closing cash"] == [closing[11], closing[23], closing[35]]
    assert y["balance_sheet"]["Balance check"] == [0, 0, 0]
    # opening + the year's net cash flow lands on the year's closing cash
    net = y["cash_flow"]["Net cash flow"]
    for k in range(3):
        assert y["cash_flow"]["Opening cash"][k] + net[k] == y["cash_flow"]["Closing cash"][k]


def test_rollup_handles_partial_year_and_missing_input():
    stmt = {
        "rows": [
            {"label": "Revenue", "values": list(range(1, 15))},
            {"label": "Odd", "values": list(range(14))},
        ]
    }
    months = [f"2026-{m:02d}" for m in range(1, 13)] + ["2027-01", "2027-02"]
    out = rollup_yearly(stmt, months)
    assert out["rows"][0]["values"] == [sum(range(1, 13)), 13 + 14]
    assert out["rows"][1]["values"] == [11, 13]  # unknown labels are balances: the last month
    assert yearly_periods(months)[1] == {
        "label": "Year 2",
        "start_month": "2027-01",
        "end_month": "2027-02",
    }
    assert rollup_yearly(None, months) is None
    assert rollup_yearly(stmt, None) is None
