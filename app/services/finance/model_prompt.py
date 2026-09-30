"""Financial-model LLM assumptions: JSON schema, prompt builder, and validation.

Pure (no I/O). `validate_assumptions` is the safety layer between the LLM and the
deterministic engine: it never raises and always returns six in-range keys.
"""

import math
from typing import Any

from app.platform.llm import LLMMessage

NARRATIVE_MAX_CHARS = 600

ASSUMPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "monthly_revenue_growth_pct": {
            "type": "number",
            "description": "Expected month-over-month revenue growth, in percent (e.g. 5 = 5%).",
        },
        "cogs_pct_of_revenue": {
            "type": "number",
            "description": "Cost of goods sold as a percent of revenue (0-100).",
        },
        "monthly_opex_growth_pct": {
            "type": "number",
            "description": "Expected month-over-month operating-expense growth, in percent.",
        },
        "ar_days": {
            "type": "number",
            "description": "Average days to collect receivables (days sales outstanding, 0-120).",
        },
        "ap_days": {
            "type": "number",
            "description": "Average days to pay suppliers (days payables outstanding, 0-120).",
        },
        "narrative": {
            "type": "string",
            "description": "Two or three sentences explaining the reasoning behind the assumptions.",
        },
    },
    "required": [
        "monthly_revenue_growth_pct",
        "cogs_pct_of_revenue",
        "monthly_opex_growth_pct",
        "ar_days",
        "ap_days",
        "narrative",
    ],
    "additionalProperties": False,
}


def _major(minor: Any) -> str:
    try:
        return f"{int(minor) / 100:,.2f}"
    except (TypeError, ValueError):
        return "unknown"


def build_model_messages(starting: dict[str, Any]) -> list[LLMMessage]:
    system = (
        "You are a startup CFO building a 12-month 3-statement financial model. Given the "
        "company's actuals, return ONLY forward-looking assumptions: monthly revenue growth, "
        "COGS as a share of revenue, monthly operating-expense growth, and receivable / payable "
        "collection timing in days. Use realistic, conservative values for an early-stage "
        "company. Do not restate or change the starting figures."
    )
    currency = starting.get("currency") or "unspecified"
    user = (
        f"Company actuals (currency: {currency}, shown in major units):\n"
        f"- Current monthly revenue: {_major(starting.get('revenue_0'))}\n"
        f"- Current total monthly costs: {_major(starting.get('monthly_costs_0'))}\n"
        f"- Cash on hand: {_major(starting.get('cash_on_hand'))}\n\n"
        "These starting numbers are fixed facts supplied to you. Set only the forward-looking "
        "assumptions: monthly_revenue_growth_pct, cogs_pct_of_revenue, "
        "monthly_opex_growth_pct, ar_days, ap_days, and a short narrative explaining them."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _num(raw: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(raw.get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(value):
        return default
    return value


def validate_assumptions(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce + clamp raw LLM output into a valid, engine-safe assumptions dict. Never raises."""
    if not isinstance(raw, dict):
        raw = {}
    return {
        "monthly_revenue_growth_pct": _clamp(
            _num(raw, "monthly_revenue_growth_pct", 0.0), -50, 100
        ),
        "cogs_pct_of_revenue": _clamp(_num(raw, "cogs_pct_of_revenue", 0.0), 0, 100),
        "monthly_opex_growth_pct": _clamp(_num(raw, "monthly_opex_growth_pct", 0.0), -50, 100),
        "ar_days": int(_clamp(_num(raw, "ar_days", 0), 0, 120)),
        "ap_days": int(_clamp(_num(raw, "ap_days", 0), 0, 120)),
        "narrative": str(raw.get("narrative") or "")[:NARRATIVE_MAX_CHARS],
    }
