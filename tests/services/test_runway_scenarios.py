from datetime import date

from app.services.finance.runway_math import _runway_is_low, project_scenarios

TODAY = date(2026, 4, 1)


def _baseline(cash, burn, rev, currency="NGN"):
    return {
        "cash_on_hand": cash,
        "monthly_burn": burn,
        "monthly_revenue": rev,
        "currency": currency,
    }


def _assume(g=0, hire=0, oneoff=0):
    return {"mom_growth_percent": g, "hiring_spend_minor": hire, "one_off_costs_minor": oneoff}


def _months(scenario: dict) -> float:
    # A None runway means "does not run out within the horizon", i.e. the longest runway.
    r = scenario["runway_months"]
    return float("inf") if r is None else r


def test_worst_le_base_le_best_runway():
    s = project_scenarios(_baseline(10_000_000, 2_000_000, 1_000_000), _assume(), today=TODAY)
    assert _months(s["worst"]) <= _months(s["base"]) <= _months(s["best"])
    assert _months(s["worst"]) < _months(s["best"])


def test_crosses_zero_gives_fractional_month():
    # 7M cash, 2M/month net burn: balances 5M, 3M, 1M, -1M -> crosses between month 3 and 4.
    s = project_scenarios(_baseline(7_000_000, 2_000_000, 1_000_000), _assume(), today=TODAY)
    r = s["base"]["runway_months"]
    assert r is not None and 3 < r < 5  # not the integer index, not None
    assert r == 3.5
    assert s["base"]["cash_out_date"] == "2026-07"


def test_already_out_of_cash_while_burning_is_zero():
    s = project_scenarios(_baseline(0, 2_000_000, 500_000), _assume(), today=TODAY)
    assert s["base"]["runway_months"] == 0.0
    assert s["base"]["cash_out_date"] == "2026-04"


def test_never_runs_out_is_none():
    s = project_scenarios(_baseline(50_000_000, 0, 5_000_000), _assume(g=20), today=TODAY)
    assert s["base"]["runway_months"] is None and s["base"]["cash_out_date"] is None


def test_one_off_reduces_starting_cash():
    with_oneoff = project_scenarios(
        _baseline(10_000_000, 2_000_000, 1_000_000), _assume(oneoff=1_000_000), today=TODAY
    )
    without = project_scenarios(_baseline(10_000_000, 2_000_000, 1_000_000), _assume(), today=TODAY)
    assert with_oneoff["base"]["runway_months"] < without["base"]["runway_months"]


def test_horizon_length_and_no_overflow_on_extreme_growth():
    s = project_scenarios(_baseline(10_000_000, 2_000_000, 1_000_000), _assume(g=1000), today=TODAY)
    for name in ("base", "best", "worst"):
        assert len(s[name]["by_month"]) == 12  # RUNWAY_HORIZON_MONTHS
    assert s["base"]["by_month"][0]["month"] == "2026-05"
    assert s["base"]["by_month"][-1]["month"] == "2027-04"


def test_runway_is_low_predicate():
    assert _runway_is_low(2_000_000, None) is True  # burning, no runway number -> low
    assert _runway_is_low(2_000_000, 3.0) is True  # < 6 months
    assert _runway_is_low(2_000_000, 12.0) is False  # healthy
    assert _runway_is_low(0, None) is False  # not burning -> not low
