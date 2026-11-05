from app.services.readiness.config import DIMENSION_WEIGHTS
from app.services.readiness.scoring import (
    Reading,
    apply_cap,
    band_and_verdict,
    dimension_score,
    rank_gaps,
    raw_overall,
    signal_score,
)


def _r(key, dim, score, weight=1.0, note="n"):
    return Reading(key, dim, key, "x", weight, 1.0, 2.0, score, f"/{key}", note)


def test_weights_sum_to_100():
    assert sum(DIMENSION_WEIGHTS.values()) == 100


def test_signal_score_caps_at_100_and_handles_zero_target():
    assert signal_score(1, 4) == 25.0
    assert signal_score(9, 4) == 100.0
    assert signal_score(0, 0) == 100.0


def test_dimension_score_is_weighted_and_none_when_empty():
    assert dimension_score([]) is None
    assert dimension_score([_r("a", "team", 100, 1), _r("b", "team", 0, 3)]) == 25


def test_raw_overall_renormalises_over_scored_dimensions():
    assert raw_overall({"traction": 100, "story": None, "team": None}) == 100
    assert raw_overall(dict.fromkeys(DIMENSION_WEIGHTS, 50)) == 50
    assert raw_overall(dict.fromkeys(DIMENSION_WEIGHTS)) == 0
    # traction 25 @100, story 10 @0 -> 2500 / 35
    assert raw_overall({"traction": 100, "story": 0}) == 71


def test_cap_only_bites_at_70_plus_with_a_missing_must_have():
    missing = [{"met": True}, {"met": False}]
    assert apply_cap(85, missing) == (69, True)
    assert apply_cap(69, missing) == (69, False)
    assert apply_cap(85, [{"met": True}]) == (85, False)


def test_bands_and_verdict_copy():
    assert band_and_verdict(0)[0] == "not_yet"
    assert band_and_verdict(39)[1] == "Not yet, and that's fine. Here's the shortest path."
    assert band_and_verdict(40)[0] == "getting_close"
    assert band_and_verdict(69)[1] == "Getting close. Close these gaps before outreach."
    assert band_and_verdict(70) == ("ready", "You're ready to run a process.")


def test_gaps_rank_by_lift_cap_two_per_dimension_and_five_total():
    readings = [
        _r("t1", "traction", 0),
        _r("t2", "traction", 10),
        _r("t3", "traction", 20),  # third traction gap is dropped
        _r("f1", "financials", 0),
        _r("f2", "financials", 50),
        _r("p1", "product", 0),
        _r("m1", "market", 0),
        _r("s1", "story", 0),
        _r("done", "team", 100, note=""),
    ]
    gaps = rank_gaps(readings)
    keys = [g["signal_key"] for g in gaps]
    assert len(gaps) == 5
    assert "t3" not in keys and "done" not in keys
    # A lone signal carries its whole dimension: market and product (15 each) lead.
    assert keys == ["m1", "p1", "f1", "s1", "t1"]
    only_traction = rank_gaps(
        [_r("t1", "traction", 0), _r("t2", "traction", 0), _r("t3", "traction", 0)]
    )
    assert len(only_traction) == 2
    assert all(g["lift"] >= 1 and g["href"] for g in gaps)
    lifts = [g["lift"] for g in gaps]
    assert lifts == sorted(lifts, reverse=True)
