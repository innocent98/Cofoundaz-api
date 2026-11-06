"""Worked examples for the cap table engine. Every expected number is derived by hand in the
comment above it, so a regression cannot hide behind a copied output."""

from datetime import date

import pytest

from app.services.funding.cap_engine import (
    Convertible,
    EngineError,
    Position,
    RoundInput,
    apply_round,
    converting_amount,
    holder_shares,
    largest_remainder_bps,
    model_rounds,
    ownership_table,
    term_candidates,
    vested_shares,
)

AS_OF = date(2026, 6, 1)


def founders(shares=8_000_000, pool=1_000_000):
    out = [Position("f1", "Ada", "founder", "common", shares)]
    if pool:
        out.append(Position("pool", "Option pool", "pool", "option", pool))
    return out


def by_key(positions):
    out = {}
    for p in positions:
        out[p.key] = out.get(p.key, 0) + p.shares
    return out


# ------------------------------------------------------------- plain round, no SAFEs, no pool
def test_plain_round_issues_new_money_over_post_money():
    # S0 = 9,000,000; V = 40M; N = 10M. Price = 40M / 9M = 4.444444 per share.
    # Investors: 10M / 4.4444 = 2,250,000 shares = 10M / 50M = 20% of 11,250,000.
    pos, step = apply_round(founders(), [], RoundInput("Seed", 10_000_000, 40_000_000), as_of=AS_OF)
    assert step.price_per_share == "4.444444"
    assert step.new_investor_shares == 2_250_000 and step.post_round_shares == 11_250_000
    rows = {r["ref"]: r for r in ownership_table(pos)}
    assert rows["round-1"]["bps"] == 2000
    assert rows["f1"]["bps"] == 7111 and rows["pool"]["bps"] == 889  # 8/11.25 and 1/11.25
    assert sum(r["bps"] for r in rows.values()) == 10_000
    assert step.founders_before_bps == 8889 and step.founders_after_bps == 7111


# ------------------------------------------------------------- post-money SAFE at a cap
def test_post_money_safe_at_cap_owns_amount_over_cap():
    # One post-money SAFE: 1,000,000 at a 10,000,000 cap = exactly 10% of the capitalisation
    # before the round. CC = 9,000,000 / (1 - 0.1) = 10,000,000, so the SAFE gets 1,000,000
    # shares at cap price 1.0. Round price = 40M / 10M = 4.0, so the cap price wins.
    safe = Convertible("s1", "Angel", "safe", 1_000_000, cap_minor=10_000_000)
    pos, step = apply_round(
        founders(), [safe], RoundInput("Seed", 10_000_000, 40_000_000), as_of=AS_OF
    )
    c = step.conversions[0]
    assert c["shares"] == 1_000_000 and c["term"] == "cap" and c["conversion_price"] == "1.000000"
    assert step.price_per_share == "4.000000"
    assert step.new_investor_shares == 2_500_000  # 10M / 4.0
    assert step.post_round_shares == 12_500_000
    rows = {r["ref"]: r["bps"] for r in ownership_table(pos)}
    # founders 8M/12.5M = 64%, SAFE 8%, pool 8%, investors 20% (= N / (V + N))
    assert rows == {"f1": 6400, "s1": 800, "pool": 800, "round-1": 2000}


def test_pre_money_safe_at_cap_prices_on_the_capitalisation_without_safes():
    # Pre-money SAFE: cap price = 10,000,000 / 9,000,000 = 1.111111; 1,000,000 buys 900,000.
    # Round price = 40M / (9M + 0.9M) = 4.040404, so the cap price is lower and wins.
    safe = Convertible(
        "s1", "Angel", "safe", 1_000_000, cap_minor=10_000_000, safe_kind="pre_money"
    )
    _, step = apply_round(
        founders(), [safe], RoundInput("Seed", 10_000_000, 40_000_000), as_of=AS_OF
    )
    assert step.conversions[0]["shares"] == 900_000 and step.conversions[0]["term"] == "cap"


# ------------------------------------------------------------- discount
def test_discount_safe_converts_at_the_round_price_less_the_discount():
    # 20% discount, no cap. sigma = A*S0/(0.8*V - A) with A = 1,000,000, V = 40M, S0 = 9M:
    # sigma = 1M * 9M / (32M - 1M) = 290,322.58 -> 290,322 shares (rounded down).
    safe = Convertible("s1", "Angel", "safe", 1_000_000, discount_bps=2000)
    _, step = apply_round(
        founders(), [safe], RoundInput("Seed", 10_000_000, 40_000_000), as_of=AS_OF
    )
    c = step.conversions[0]
    assert c["term"] == "discount" and c["shares"] == 290_322


def test_safe_with_neither_cap_nor_discount_converts_at_the_round_price():
    # sigma = A*S0/(V - A) = 1M * 9M / 39M = 230,769.23 -> 230,769.
    safe = Convertible("s1", "Angel", "safe", 1_000_000)
    _, step = apply_round(
        founders(), [safe], RoundInput("Seed", 10_000_000, 40_000_000), as_of=AS_OF
    )
    assert step.conversions[0]["term"] == "round" and step.conversions[0]["shares"] == 230_769


def test_the_better_of_cap_and_discount_wins():
    # Cap 40,000,000 post-money is a 2.5% stake (cap price 4.0... above the round price), the 20%
    # discount price is 0.8 * round price: the discount wins. With a 5M cap the cap wins.
    pos = founders()
    loose = Convertible("s1", "A", "safe", 1_000_000, cap_minor=40_000_000, discount_bps=2000)
    tight = Convertible("s2", "B", "safe", 1_000_000, cap_minor=5_000_000, discount_bps=2000)
    r = RoundInput("Seed", 10_000_000, 40_000_000)
    assert apply_round(pos, [loose], r, as_of=AS_OF)[1].conversions[0]["term"] == "discount"
    assert apply_round(pos, [tight], r, as_of=AS_OF)[1].conversions[0]["term"] == "cap"


def test_several_safes_with_mixed_terms_settle_and_add_up():
    safes = [
        Convertible("s1", "A", "safe", 500_000, cap_minor=8_000_000),
        Convertible("s2", "B", "safe", 750_000, discount_bps=1500),
        Convertible("s3", "C", "note", 250_000, cap_minor=12_000_000, safe_kind="pre_money"),
    ]
    pos, step = apply_round(
        founders(), safes, RoundInput("Seed", 10_000_000, 40_000_000), as_of=AS_OF
    )
    assert sum(c["shares"] for c in step.conversions) > 0
    total = sum(by_key(pos).values())
    assert total == step.post_round_shares
    assert sum(r["bps"] for r in ownership_table(pos)) == 10_000
    # the new investors really hold N / (V + N) of the company (within one share of rounding)
    held = by_key(pos)["round-1"]
    assert abs(held / total - 0.2) < 1e-4


# ------------------------------------------------------------- notes and MFN
def test_note_interest_is_simple_actual_365_half_even():
    n = Convertible("n1", "Bank", "note", 1_000_000, interest_bps=800, issued_on=date(2025, 6, 1))
    # 365 days at 8%: exactly 80,000.
    assert converting_amount(n, date(2026, 6, 1)) == 1_080_000
    # 100 days: 1,000,000 * 0.08 * 100/365 = 21,917.808 -> 21,918
    assert converting_amount(n, date(2025, 9, 9)) == 1_021_918
    # a note without a date, or a SAFE, accrues nothing
    assert converting_amount(Convertible("n2", "x", "note", 5, interest_bps=800), AS_OF) == 5
    assert converting_amount(Convertible("s", "x", "safe", 7, interest_bps=800), AS_OF) == 7


def test_mfn_candidates_are_whole_term_sets_of_later_non_mfn_instruments():
    mfn = Convertible("m", "MFN", "safe", 1_000_000, issued_on=date(2025, 1, 1), mfn=True)
    later = Convertible(
        "l",
        "Later",
        "safe",
        1_000_000,
        issued_on=date(2025, 6, 1),
        cap_minor=8_000_000,
        discount_bps=2000,
    )
    earlier = Convertible(
        "e",
        "Earlier",
        "safe",
        1_000_000,
        issued_on=date(2024, 6, 1),
        cap_minor=1_000_000,
        discount_bps=5000,
    )
    other_kind = Convertible(
        "p",
        "Pre",
        "safe",
        1_000_000,
        issued_on=date(2025, 7, 1),
        cap_minor=2_000_000,
        safe_kind="pre_money",
    )
    other_mfn = Convertible("o", "O", "safe", 1, issued_on=date(2025, 8, 1), mfn=True, cap_minor=1)
    convs = [mfn, later, earlier, other_kind, other_mfn]
    got = term_candidates(mfn, convs)
    assert got == [
        (None, None, "post_money"),  # its own
        (8_000_000, 2000, "post_money"),  # the later SAFE, cap AND discount together
        (2_000_000, None, "pre_money"),  # the later pre-money SAFE, as it is
    ]
    assert term_candidates(later, convs) == [(8_000_000, 2000, "post_money")]  # non-MFN: own only
    undated = Convertible("u", "U", "safe", 1, mfn=True)
    assert term_candidates(undated, [undated, later]) == [
        (None, None, "post_money")
    ]  # needs a date


def test_mfn_never_mixes_the_cap_of_one_instrument_with_the_discount_of_another():
    """Donor A: 10M post-money cap, no discount. Donor B: no cap, 30% discount. The MFN SAFE
    must convert exactly as A or exactly as B, whichever gives more shares, never on a blend."""
    base = {"security": "safe", "principal_minor": 1_000_000}
    a = Convertible("a", "A", issued_on=date(2025, 6, 1), cap_minor=10_000_000, **base)
    b = Convertible("b", "B", issued_on=date(2025, 6, 1), discount_bps=3000, **base)
    mfn = Convertible("m", "M", issued_on=date(2025, 1, 1), mfn=True, **base)
    r = RoundInput("Seed", 10_000_000, 40_000_000)

    def shares(convs):
        return {
            c["ref"]: c["shares"]
            for c in apply_round(founders(), convs, r, as_of=AS_OF)[1].conversions
        }

    got = shares([a, b, mfn])
    assert got["m"] == max(got["a"], got["b"])
    # and it follows the better donor as the round changes: here the cap (A) wins, so M matches A
    assert got["m"] == got["a"] and got["a"] > got["b"]


def test_mfn_safe_converts_on_the_adopted_terms():
    mfn = Convertible("m", "MFN", "safe", 1_000_000, issued_on=date(2025, 1, 1), mfn=True)
    later = Convertible(
        "l", "Later", "safe", 1_000_000, issued_on=date(2025, 6, 1), cap_minor=10_000_000
    )
    _, step = apply_round(
        founders(), [mfn, later], RoundInput("Seed", 10_000_000, 40_000_000), as_of=AS_OF
    )
    shares = {c["ref"]: c["shares"] for c in step.conversions}
    assert shares["m"] == shares["l"]  # identical economics


# ------------------------------------------------------------- the option pool
def test_pool_top_up_sits_in_the_pre_money_and_hits_the_target():
    # No SAFE, existing pool 0, target 10% of post. m = 0.1 * 50/40 = 0.125;
    # X = 0.125 * 9,000,000 / 0.875 = 1,285,714.29 -> rounded UP to 1,285,715.
    pos = [Position("f1", "Ada", "founder", "common", 9_000_000)]
    out, step = apply_round(pos, [], RoundInput("Seed", 10_000_000, 40_000_000, 1000), as_of=AS_OF)
    assert step.pool_top_up_shares == 1_285_715
    assert step.post_round_shares == 9_000_000 + 1_285_715 + step.new_investor_shares
    pool = by_key([p for p in out if p.kind == "pool"])["pool"]
    assert pool / step.post_round_shares >= 0.10
    assert pool / step.post_round_shares < 0.1001


def test_an_existing_pool_above_target_is_left_alone():
    _, step = apply_round(
        founders(shares=8_000_000, pool=3_000_000),
        [],
        RoundInput("S", 10_000_000, 40_000_000, 500),
        as_of=AS_OF,
    )
    assert step.pool_top_up_shares == 0


def test_an_existing_pool_is_counted_toward_the_target():
    # Existing pool 1,000,000, founders 8,000,000, target 10%: m = 0.125,
    # X = (0.125 * 9,000,000 - 1,000,000) / 0.875 = 142,857.14 -> 142,858.
    _, step = apply_round(
        founders(), [], RoundInput("S", 10_000_000, 40_000_000, 1000), as_of=AS_OF
    )
    assert step.pool_top_up_shares == 142_858


def test_pool_target_and_safes_together():
    safe = Convertible("s1", "A", "safe", 1_000_000, cap_minor=10_000_000)
    pos, step = apply_round(
        founders(), [safe], RoundInput("S", 10_000_000, 40_000_000, 1000), as_of=AS_OF
    )
    pool = sum(p.shares for p in pos if p.kind == "pool")
    assert pool / step.post_round_shares >= 0.10


# ------------------------------------------------------------- rounds in sequence
def test_a_second_round_dilutes_everyone_including_the_first_investors():
    pos, steps = model_rounds(
        founders(),
        [Convertible("s1", "A", "safe", 1_000_000, cap_minor=10_000_000)],
        [
            RoundInput("Seed", 10_000_000, 40_000_000),
            RoundInput("Series A", 50_000_000, 150_000_000),
        ],
        as_of=AS_OF,
    )
    assert len(steps) == 2
    rows = {r["ref"]: r["bps"] for r in ownership_table(pos)}
    assert rows["round-2"] == 2500  # 50M / 200M
    # everyone else is diluted by the same factor (1 - 25%): seed investors 20% -> 15%
    assert rows["round-1"] == 1500 and rows["f1"] == 4800  # 64% * 0.75
    assert steps[1].conversions == []  # SAFEs convert once
    assert steps[1].founders_before_bps == 6400 and steps[1].founders_after_bps == 4800


# ------------------------------------------------------------- refusals
@pytest.mark.parametrize(
    "round_,code",
    [
        (RoundInput("x", 0, 40_000_000), "INVALID_ROUND"),
        (RoundInput("x", 10_000_000, 0), "INVALID_ROUND"),
        (RoundInput("x", 10_000_000, 40_000_000, 10_000), "INVALID_ROUND"),
        (RoundInput("x", 10_000_000, 40_000_000, 9000), "POOL_TARGET_TOO_HIGH"),
    ],
)
def test_invalid_rounds(round_, code):
    with pytest.raises(EngineError) as e:
        apply_round(founders(), [], round_, as_of=AS_OF)
    assert e.value.code == code


def test_empty_cap_table_is_refused():
    with pytest.raises(EngineError) as e:
        apply_round([], [], RoundInput("x", 1, 1), as_of=AS_OF)
    assert e.value.code == "EMPTY_CAP_TABLE"


def test_post_money_safes_owning_everything_are_refused():
    safes = [
        Convertible("a", "A", "safe", 6_000_000, cap_minor=10_000_000),
        Convertible("b", "B", "safe", 5_000_000, cap_minor=10_000_000),
    ]
    with pytest.raises(EngineError) as e:
        apply_round(founders(), safes, RoundInput("x", 10_000_000, 40_000_000), as_of=AS_OF)
    assert e.value.code == "SAFE_CAPS_EXCEED"


def test_safes_larger_than_the_pre_money_cannot_convert():
    safe = Convertible("a", "A", "safe", 60_000_000)
    with pytest.raises(EngineError) as e:
        apply_round(founders(), [safe], RoundInput("x", 10_000_000, 40_000_000), as_of=AS_OF)
    assert e.value.code == "CONVERSION_DID_NOT_SETTLE"


# ------------------------------------------------------------- percentages, vesting, views
def test_basis_points_always_sum_to_ten_thousand_and_tie_break_deterministically():
    assert sum(largest_remainder_bps({"a": 1, "b": 1, "c": 1}).values()) == 10_000
    assert largest_remainder_bps({"a": 1, "b": 1, "c": 1}) == {"a": 3334, "b": 3333, "c": 3333}
    assert largest_remainder_bps({"a": 0, "b": 0}) == {"a": 0, "b": 0}
    w = {"a": 2_999_999, "b": 3_000_001, "c": 4_000_000, "d": 1}
    assert sum(largest_remainder_bps(w).values()) == 10_000


def test_basic_view_leaves_options_and_pool_out():
    pos = founders() + [Position("e1", "Eve", "employee", "option", 100_000)]
    assert holder_shares(pos, fully_diluted=False) == {"f1": 8_000_000}
    assert holder_shares(pos, fully_diluted=True) == {
        "f1": 8_000_000,
        "pool": 1_000_000,
        "e1": 100_000,
    }


def test_vesting_before_start_is_zero():
    kw = {"start": date(2026, 3, 1), "cliff_months": 12, "months": 48}
    assert vested_shares(48_000, as_of=date(2026, 1, 1), **kw) == 0
    assert vested_shares(48_000, as_of=date(2026, 2, 28), **kw) == 0


def test_vesting_cliff_then_linear_rounded_down():
    kw = {"start": date(2025, 3, 15), "cliff_months": 12, "months": 48}
    assert (
        vested_shares(48_000, as_of=date(2026, 3, 14), **kw) == 0
    )  # 11 whole months: cliff not met
    assert vested_shares(48_000, as_of=date(2026, 3, 15), **kw) == 12_000  # 12 months
    assert vested_shares(48_001, as_of=date(2027, 3, 15), **kw) == 24_000  # 24/48 of 48,001, down
    assert vested_shares(48_000, as_of=date(2031, 1, 1), **kw) == 48_000  # capped at the term
    assert vested_shares(100, start=None, cliff_months=0, months=0, as_of=AS_OF) == 100


# ------------------------------------------------------------- review fixes
def test_pre_money_safe_cap_price_includes_the_pool_created_in_the_round():
    # 9M founder + 1M pool (S0 = 10M). Pre-money SAFE: 1,000,000 at a 10,000,000 cap. Round:
    # V = 20M, N = 5M, pool target 20% (m = 0.2 * 25/20 = 0.25).
    # X = (0.25 * (S0 + sigma) - 1M) / 0.75 = 2M + sigma/3.
    # Pre-money capitalisation includes X, not sigma: cap price = 10M / (S0 + X) = 10M / (12M + sigma/3).
    # sigma = 1M / cap price = 1.2M + sigma/30  ->  sigma = 1.2M * 30/29 = 1,241,379.3 -> 1,241,379.
    pos = founders(shares=9_000_000, pool=1_000_000)
    safe = Convertible("s", "Angel", "safe", 1_000_000, cap_minor=10_000_000, safe_kind="pre_money")
    _, step = apply_round(pos, [safe], RoundInput("A", 5_000_000, 20_000_000, 2000), as_of=AS_OF)
    c = step.conversions[0]
    assert c["term"] == "cap" and c["shares"] == 1_241_379
    assert c["conversion_price"] == "0.805556"  # 10M / 12,413,793.1


def test_a_holder_with_stock_and_a_safe_is_one_row_and_one_headline():
    # Founder holds 9M common and also a 1,000,000 SAFE at a 10,000,000 post-money cap:
    # CC = 9M / 0.9 = 10M, so the SAFE converts to 1,000,000 shares. Round V = 20M, N = 1M:
    # investors = 1/21 of the post-money, so total = 10M / (1 - 1/21) = 10.5M and the founder
    # holds 10M / 10.5M = 95.24% (9524 bps), as ONE row.
    pos = [Position("f", "Ada", "founder", "common", 9_000_000)]
    safe = Convertible(
        "s1", "Ada", "safe", 1_000_000, holder_key="f", holder_kind="founder", cap_minor=10_000_000
    )
    out, step = apply_round(pos, [safe], RoundInput("Seed", 1_000_000, 20_000_000), as_of=AS_OF)
    assert step.conversions[0]["ref"] == "s1" and step.conversions[0]["shares"] == 1_000_000
    rows = ownership_table(out)
    assert [r["ref"] for r in rows if r["kind"] == "founder"] == ["f"]
    assert len(rows) == 2  # the founder, and the new investors
    assert step.founders_after_bps == 9524
    assert next(r for r in rows if r["ref"] == "f")["shares"] == 10_000_000


def test_slow_convergence_cases_are_solved_exactly():
    # 10M shares, a 570,000 SAFE at a 50% discount, V = 1.2M, N = 1M:
    # sigma = 570k * (10M + sigma) / (0.5 * 1.2M) = 0.95 * (10M + sigma)  ->  sigma = 190M exactly.
    pos = [Position("f", "Ada", "founder", "common", 10_000_000)]
    safe = Convertible("s", "A", "safe", 570_000, discount_bps=5000)
    _, step = apply_round(pos, [safe], RoundInput("Seed", 1_000_000, 1_200_000), as_of=AS_OF)
    assert step.conversions[0]["shares"] == 190_000_000


def test_safes_that_would_own_the_whole_company_say_so():
    # The same 50% discount with 700,000 invested: slope 700k / 600k > 1, there is no solution.
    pos = [Position("f", "Ada", "founder", "common", 10_000_000)]
    safe = Convertible("s", "A", "safe", 700_000, discount_bps=5000)
    with pytest.raises(EngineError) as e:
        apply_round(pos, [safe], RoundInput("Seed", 1_000_000, 1_200_000), as_of=AS_OF)
    assert (
        e.value.code == "CONVERSION_DID_NOT_SETTLE" and "own all of the company" in e.value.message
    )


def test_mfn_follows_whichever_donor_is_better_in_this_round():
    base = {"security": "safe", "principal_minor": 1_000_000}
    weak_cap = Convertible("a", "A", issued_on=date(2025, 6, 1), cap_minor=60_000_000, **base)
    discount = Convertible("b", "B", issued_on=date(2025, 6, 1), discount_bps=3000, **base)
    mfn = Convertible("m", "M", issued_on=date(2025, 1, 1), mfn=True, **base)
    _, step = apply_round(
        founders(),
        [weak_cap, discount, mfn],
        RoundInput("Seed", 10_000_000, 40_000_000),
        as_of=AS_OF,
    )
    got = {c["ref"]: c for c in step.conversions}
    assert got["m"]["shares"] == got["b"]["shares"] > got["a"]["shares"]
    assert got["m"]["term"] == "discount"
