"""The term sheet rules table (D-FUND-6): thresholds are exact, unstated terms never flag, and the
fixed wording is never a verdict."""

import pytest

from app.services.funding.term_rules import RULES, RULES_BY_ID, evaluate

CLEAN = {
    "stage": "seed",
    "liq_pref_x100": 100,
    "participating": False,
    "option_pool_pre_bps": 1000,
    "exclusivity_days": 30,
    "investor_board_seats": 1,
    "pro_rata": "standard",
    "anti_dilution": "broad_based",
}


def rules_of(terms):
    return [f["rule"] for f in evaluate(terms)]


def test_a_clean_seed_sheet_has_no_flags():
    assert evaluate(CLEAN) == []


def test_an_empty_sheet_has_no_flags():
    assert evaluate({}) == []


@pytest.mark.parametrize(
    "term,at,above,rule",
    [
        ("liq_pref_x100", 100, 101, "liq_pref_above_1x"),
        ("option_pool_pre_bps", 1500, 1501, "option_pool_above_15pct"),
        ("exclusivity_days", 45, 46, "exclusivity_above_45_days"),
    ],
)
def test_numeric_thresholds_flag_strictly_above(term, at, above, rule):
    assert rule not in rules_of({**CLEAN, term: at})
    assert rule in rules_of({**CLEAN, term: above})


def test_participating_super_pro_rata_and_full_ratchet():
    assert rules_of({"participating": True}) == ["participating_preferred"]
    assert rules_of({"participating": False}) == []
    assert rules_of({"pro_rata": "super"}) == ["super_pro_rata"]
    assert rules_of({"pro_rata": "standard"}) == []
    assert rules_of({"anti_dilution": "full_ratchet"}) == ["full_ratchet"]
    assert rules_of({"anti_dilution": "narrow_based"}) == []  # not in the table: not flagged


@pytest.mark.parametrize(
    "stage,seats,flagged",
    [
        ("seed", 1, False),
        ("seed", 2, True),
        ("pre_seed", 2, True),
        ("series_a", 2, False),  # two seats is ordinary from Series A
        ("series_a", 3, False),
        (None, 2, False),  # the stage is unknown: we do not guess
    ],
)
def test_board_seats_rule_depends_on_the_stage(stage, seats, flagged):
    terms = {"investor_board_seats": seats, **({"stage": stage} if stage else {})}
    assert ("investor_board_seats_early" in rules_of(terms)) is flagged


def test_flags_come_in_table_order_with_their_values_and_thresholds():
    terms = {"liq_pref_x100": 200, "participating": True, "pro_rata": "super", "stage": "seed"}
    flags = evaluate(terms)
    assert [f["rule"] for f in flags] == [
        "liq_pref_above_1x",
        "participating_preferred",
        "super_pro_rata",
    ]
    first = flags[0]
    assert first["value"] == 200 and first["threshold"] == "1x" and first["ai_text"] is None
    assert "2x" in first["text"]


def test_messages_state_the_numbers_and_never_pass_a_verdict():
    everything = {
        "stage": "seed",
        "liq_pref_x100": 150,
        "participating": True,
        "participation_cap_x100": 300,
        "option_pool_pre_bps": 2000,
        "exclusivity_days": 60,
        "investor_board_seats": 2,
        "pro_rata": "super",
        "anti_dilution": "full_ratchet",
    }
    flags = evaluate(everything)
    assert len(flags) == len(RULES) == 7
    joined = " ".join(f["text"] + " " + f["title"] for f in flags).lower()
    for verdict in (
        "bad",
        "unfair",
        "reject",
        "avoid",
        "walk away",
        "predatory",
        "scam",
        "illegal",
    ):
        assert verdict not in joined
    texts = {f["rule"]: f["text"] for f in flags}
    assert "1.5x" in texts["liq_pref_above_1x"] and "3x" in texts["participating_preferred"]
    assert (
        "20%" in texts["option_pool_above_15pct"]
        and "60 days" in texts["exclusivity_above_45_days"]
    )
    assert "2 board seats" in texts["investor_board_seats_early"]
    assert "—" not in joined and "–" not in joined  # product copy: no em or en dashes


def test_a_participating_sheet_without_a_cap_says_so():
    text = evaluate({"participating": True})[0]["text"]
    assert "no stated cap" in text


def test_the_table_has_unique_ids_that_index_it():
    assert len({r.id for r in RULES}) == len(RULES)
    assert set(RULES_BY_ID) == {r.id for r in RULES}
