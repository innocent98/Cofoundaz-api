from app.services.legal.catalog import (
    CATALOG_REVIEWED_ON,
    CATALOG_VERSION,
    ESTIMATE_DISCLAIMER,
    JURISDICTIONS,
    STEPS,
    steps_for,
)


def test_only_the_two_jurisdictions_with_real_content_are_supported():
    # Kenya and Ghana are deliberately absent (spec D6): the FE prototype gives them the same
    # generic placeholder steps as everyone else, and shipping placeholder legal steps as
    # guidance is worse than not supporting the jurisdiction.
    assert JURISDICTIONS == {"NG": "Nigeria", "US-DE": "Delaware, USA"}
    assert set(STEPS) == set(JURISDICTIONS)


def test_step_keys_are_unique_within_each_jurisdiction():
    for code, steps in STEPS.items():
        keys = [s.key for s in steps]
        assert len(keys) == len(set(keys)), f"duplicate step key in {code}"


def test_the_same_key_may_appear_in_two_jurisdictions():
    # `bank_account` is in both, which is fine: progress is keyed on
    # (startup_id, jurisdiction, step_key), so the two never collide.
    assert "bank_account" in {s.key for s in STEPS["NG"]}
    assert "bank_account" in {s.key for s in STEPS["US-DE"]}


def test_every_step_is_fully_described():
    for code, steps in STEPS.items():
        assert steps, f"{code} has no steps"
        for s in steps:
            assert s.key and s.key == s.key.strip()
            assert s.title and s.title == s.title.strip()
            assert s.authority and s.authority == s.authority.strip()
            assert isinstance(s.est_days, int) and s.est_days > 0


def test_cost_is_minor_units_plus_a_currency_or_neither():
    # Money follows Finance: integer minor units + a currency code, never floats (spec D5).
    # "We don't know the fee" is both fields null, never a zero that reads as "free".
    for steps in STEPS.values():
        for s in steps:
            both = s.est_cost_minor is not None and s.est_cost_currency is not None
            neither = s.est_cost_minor is None and s.est_cost_currency is None
            assert both or neither, f"{s.key} has a half-specified cost"
            if both:
                assert isinstance(s.est_cost_minor, int) and s.est_cost_minor > 0
                assert len(s.est_cost_currency) == 3


def test_the_figures_are_marked_unverified():
    # The lead verifies the catalog before production; until then this stays None (spec D6).
    assert CATALOG_REVIEWED_ON is None
    assert ESTIMATE_DISCLAIMER.strip()
    assert CATALOG_VERSION >= 1


def test_disclaimer_points_at_no_hidden_module():
    # Marketplace is hidden until Adebayo unhides it; the disclaimer must not send founders there.
    assert "Marketplace" not in ESTIMATE_DISCLAIMER
    assert ESTIMATE_DISCLAIMER.endswith("involve a licensed professional.")


def test_steps_for_an_unsupported_jurisdiction_is_none():
    assert steps_for("KE") is None
    assert steps_for("GH") is None
    assert steps_for("") is None
    assert steps_for("ng") is None  # codes are exact, not case-insensitive


def test_steps_for_a_supported_jurisdiction_returns_catalog_order():
    ng = steps_for("NG")
    assert ng is not None
    assert [s.key for s in ng] == [s.key for s in STEPS["NG"]]
