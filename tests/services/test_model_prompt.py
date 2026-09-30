from datetime import date

import pytest

from app.platform.llm import LLMMessage
from app.services.finance.model_engine import project_three_statements
from app.services.finance.model_prompt import (
    ASSUMPTIONS_SCHEMA,
    build_model_messages,
    validate_assumptions,
)

_KEYS = {
    "monthly_revenue_growth_pct",
    "cogs_pct_of_revenue",
    "monthly_opex_growth_pct",
    "ar_days",
    "ap_days",
    "narrative",
}


def _start(rev=10_000_000, costs=8_000_000, cash=50_000_000):
    return {"revenue_0": rev, "monthly_costs_0": costs, "cash_on_hand": cash, "currency": "NGN"}


@pytest.mark.parametrize(
    ("field", "given", "expected"),
    [
        ("monthly_revenue_growth_pct", 999, 100),
        ("monthly_revenue_growth_pct", -999, -50),
        ("cogs_pct_of_revenue", 150, 100),
        ("cogs_pct_of_revenue", -5, 0),
        ("monthly_opex_growth_pct", 999, 100),
        ("monthly_opex_growth_pct", -999, -50),
        ("ar_days", 500, 120),
        ("ar_days", -3, 0),
        ("ap_days", 500, 120),
        ("ap_days", -3, 0),
    ],
)
def test_clamps_out_of_range(field, given, expected):
    out = validate_assumptions({field: given})
    assert out[field] == expected


def test_in_range_values_preserved():
    raw = {
        "monthly_revenue_growth_pct": 7.5,
        "cogs_pct_of_revenue": 32.5,
        "monthly_opex_growth_pct": 2,
        "ar_days": 30,
        "ap_days": 45,
        "narrative": "ok",
    }
    out = validate_assumptions(raw)
    assert out == {**raw, "monthly_opex_growth_pct": 2.0}


def test_missing_fields_default():
    out = validate_assumptions({})
    assert out == {
        "monthly_revenue_growth_pct": 0,
        "cogs_pct_of_revenue": 0,
        "monthly_opex_growth_pct": 0,
        "ar_days": 0,
        "ap_days": 0,
        "narrative": "",
    }


@pytest.mark.parametrize("bad", ["abc", None, [], {}, float("nan"), float("inf"), object()])
def test_non_numeric_defaults_without_raising(bad):
    raw = dict.fromkeys(_KEYS - {"narrative"}, bad)
    out = validate_assumptions(raw)
    assert out["monthly_revenue_growth_pct"] == 0
    assert out["cogs_pct_of_revenue"] == 0
    assert out["monthly_opex_growth_pct"] == 0
    assert out["ar_days"] == 0
    assert out["ap_days"] == 0


def test_days_are_ints():
    out = validate_assumptions({"ar_days": 30.7, "ap_days": "45"})
    assert isinstance(out["ar_days"], int)
    assert isinstance(out["ap_days"], int)
    assert out["ap_days"] == 45


def test_narrative_truncated_and_defaulted():
    long_out = validate_assumptions({"narrative": "x" * 1000})
    none_out = validate_assumptions({"narrative": None})
    assert len(long_out["narrative"]) == 600
    assert none_out["narrative"] == ""


def test_extra_keys_ignored():
    out = validate_assumptions({"evil": 1, "narrative": "n", "ar_days": 10})
    assert set(out) == _KEYS


def test_non_dict_input_does_not_raise():
    out = validate_assumptions(None)  # type: ignore[arg-type]
    assert set(out) == _KEYS


def test_stub_llm_shape_validates_to_all_zero():
    stub = {
        "monthly_revenue_growth_pct": 0,
        "cogs_pct_of_revenue": 0,
        "monthly_opex_growth_pct": 0,
        "ar_days": 0,
        "ap_days": 0,
        "narrative": "[stub-llm] narrative",
    }
    out = validate_assumptions(stub)
    assert out == stub


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"monthly_revenue_growth_pct": 9999, "cogs_pct_of_revenue": 500, "ar_days": 9999},
        {"monthly_revenue_growth_pct": "abc", "ap_days": None, "monthly_opex_growth_pct": -999},
        {
            "monthly_revenue_growth_pct": 10,
            "cogs_pct_of_revenue": 30,
            "monthly_opex_growth_pct": 5,
            "ar_days": 30,
            "ap_days": 45,
            "narrative": "n",
        },
    ],
)
def test_validated_output_is_engine_safe(raw):
    assumptions = validate_assumptions(raw)
    model = project_three_statements(_start(), assumptions, today=date(2026, 4, 1))
    bs = model["balance_sheet_raw"]
    for i in range(12):
        assets = bs["cash"][i] + bs["accounts_receivable"][i]
        liab_equity = bs["accounts_payable"][i] + bs["paid_in_capital"] + bs["retained_earnings"][i]
        assert assets == liab_equity


def test_schema_shape():
    props = ASSUMPTIONS_SCHEMA["properties"]
    required = ASSUMPTIONS_SCHEMA["required"]
    extra = ASSUMPTIONS_SCHEMA["additionalProperties"]
    assert set(props) == _KEYS
    assert set(required) == _KEYS
    assert extra is False
    assert props["narrative"]["type"] == "string"
    assert props["ar_days"]["type"] == "number"


def test_build_model_messages():
    msgs = build_model_messages(_start())
    assert len(msgs) == 2
    assert all(isinstance(m, LLMMessage) for m in msgs)
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
    user = msgs[1].content
    assert "100,000.00" in user  # revenue_0 10_000_000 minor -> major
    assert "80,000.00" in user
    assert "500,000.00" in user
    assert "NGN" in user
    assert "fixed facts" in user
    assert "Set only the forward-looking assumptions" in user
