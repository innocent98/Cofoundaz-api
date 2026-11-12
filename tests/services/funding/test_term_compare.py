"""Side by side comparison: N columns, best and worst per term, derived ownership."""

from app.services.funding.term_compare import compare, derived, investor_ownership_bps
from app.services.funding.term_rules import evaluate


def sheet(sid, label, **terms):
    return {
        "id": sid,
        "label": label,
        "investor_id": None,
        "investor_name": None,
        "terms": terms,
        "flags": evaluate(terms),
    }


def row(out, term):
    return next(r for r in out["rows"] if r["term"] == term)


def test_investor_ownership_is_investment_over_post_money_half_even():
    assert investor_ownership_bps({"pre_money_minor": 40, "investment_minor": 10}) == 2000
    assert investor_ownership_bps({"pre_money_minor": 2, "investment_minor": 1}) == 3333  # 3333.33
    # exact halves go to the even neighbour: 0.5 -> 0, 1.5 -> 2, 2.5 -> 2 (post-money 20,000)
    assert investor_ownership_bps({"pre_money_minor": 19_999, "investment_minor": 1}) == 0
    assert investor_ownership_bps({"pre_money_minor": 19_997, "investment_minor": 3}) == 2
    assert investor_ownership_bps({"pre_money_minor": 19_995, "investment_minor": 5}) == 2
    assert investor_ownership_bps({"pre_money_minor": 40}) is None
    assert investor_ownership_bps({"pre_money_minor": 0, "investment_minor": 0}) is None


def test_derived_adds_post_money_and_ownership():
    d = derived({"pre_money_minor": 40_000_000, "investment_minor": 10_000_000})
    assert d["post_money_minor"] == 50_000_000 and d["investor_ownership_bps"] == 2000


def test_one_column_per_sheet_and_best_and_worst_per_term():
    a = sheet(
        "a",
        "Alpha",
        pre_money_minor=40_000_000,
        investment_minor=10_000_000,
        liq_pref_x100=100,
        participating=False,
        pro_rata="standard",
    )
    b = sheet(
        "b",
        "Beta",
        pre_money_minor=50_000_000,
        investment_minor=10_000_000,
        liq_pref_x100=200,
        participating=True,
        pro_rata="super",
    )
    c = sheet(
        "c",
        "Gamma",
        pre_money_minor=45_000_000,
        investment_minor=10_000_000,
        liq_pref_x100=150,
        participating=False,
        pro_rata="none",
    )
    out = compare([a, b, c])
    assert [col["id"] for col in out["columns"]] == ["a", "b", "c"]
    assert out["columns"][1]["flag_count"] == 3  # >1x, participating, super pro-rata
    pre = row(out, "pre_money_minor")
    assert pre["best"] == ["b"] and pre["worst"] == ["a"]  # higher is better for the founder
    assert pre["values"] == {"a": 40_000_000, "b": 50_000_000, "c": 45_000_000}
    liq = row(out, "liq_pref_x100")
    assert liq["best"] == ["a"] and liq["worst"] == ["b"]  # lower is better
    part = row(out, "participating")
    assert part["best"] == ["a", "c"] and part["worst"] == ["b"]
    pr = row(out, "pro_rata")
    assert pr["best"] == ["c"] and pr["worst"] == ["b"]  # none < standard < super
    own = row(out, "investor_ownership_bps")
    assert own["best"] == ["b"] and own["worst"] == ["a"]  # fewer points sold is better
    assert out["flags"]["b"][0]["rule"] == "liq_pref_above_1x"


def test_no_highlight_when_values_tie_or_only_one_sheet_states_the_term():
    a = sheet("a", "A", liq_pref_x100=100, exclusivity_days=30)
    b = sheet("b", "B", liq_pref_x100=100)
    out = compare([a, b])
    assert row(out, "liq_pref_x100")["best"] == [] and row(out, "liq_pref_x100")["worst"] == []
    ex = row(out, "exclusivity_days")
    assert ex["values"] == {"a": 30, "b": None} and ex["best"] == [] and ex["worst"] == []


def test_terms_nobody_states_are_left_out_and_unstated_values_are_null():
    out = compare([sheet("a", "A", liq_pref_x100=100), sheet("b", "B", liq_pref_x100=150)])
    assert [r["term"] for r in out["rows"]] == ["liq_pref_x100"]
    out = compare(
        [
            sheet("a", "A", pre_money_minor=10, investment_minor=5),
            sheet("b", "B", liq_pref_x100=100),
        ]
    )
    pre = row(out, "pre_money_minor")
    assert pre["values"]["b"] is None and pre["best"] == []


def test_neutral_terms_are_never_highlighted():
    out = compare(
        [
            sheet(
                "a", "A", investment_minor=5_000_000, participation_cap_x100=200, participating=True
            ),
            sheet(
                "b", "B", investment_minor=9_000_000, participation_cap_x100=300, participating=True
            ),
        ]
    )
    for term in ("investment_minor", "post_money_minor", "participation_cap_x100"):
        r = next((r for r in out["rows"] if r["term"] == term), None)
        assert r is None or (r["best"] == [] and r["worst"] == [] and r["better"] is None)


def test_founder_board_seats_more_is_better():
    out = compare([sheet("a", "A", founder_board_seats=2), sheet("b", "B", founder_board_seats=1)])
    r = row(out, "founder_board_seats")
    assert r["best"] == ["a"] and r["worst"] == ["b"]
