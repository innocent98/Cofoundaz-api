"""Compare confirmed term sheets side by side, one column per sheet (not a fixed A and B).

For each term, the best and worst sheet from the founder's side are highlighted, but only when two
or more sheets state the term and the values differ. "Best" is a plain ordering on the numbers
(higher pre-money, lower preference, ...), not advice: terms interact and some trade off, which the
page says. A term nobody states is left out of the table, and a sheet that does not state a term
shows `null` for it.
"""

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

MIN_SHEETS = 2
MAX_SHEETS = 6

PRO_RATA_RANK = {"none": 0, "standard": 1, "super": 2}
ANTI_DILUTION_RANK = {"none": 0, "broad_based": 1, "narrow_based": 2, "full_ratchet": 3}
STAGES = ("pre_seed", "seed", "series_a", "series_b", "later", "other")

# (key, label, unit, better) where better is "higher", "lower" or None (shown, never highlighted)
TERMS: tuple[tuple[str, str, str, str | None], ...] = (
    ("pre_money_minor", "Pre-money valuation", "money", "higher"),
    ("investment_minor", "Investment", "money", None),
    ("post_money_minor", "Post-money valuation", "money", None),
    ("investor_ownership_bps", "Investor ownership (investment / post-money)", "bps", "lower"),
    ("option_pool_pre_bps", "Option pool in the pre-money", "bps", "lower"),
    ("liq_pref_x100", "Liquidation preference", "multiple_x100", "lower"),
    ("participating", "Participating preferred", "bool", "lower"),
    ("participation_cap_x100", "Participation cap", "multiple_x100", None),
    ("investor_board_seats", "Investor board seats", "count", "lower"),
    ("founder_board_seats", "Founder board seats", "count", "higher"),
    ("pro_rata", "Pro-rata rights", "enum", "lower"),
    ("exclusivity_days", "Exclusivity (no-shop)", "days", "lower"),
    ("anti_dilution", "Anti-dilution", "enum", "lower"),
)


def investor_ownership_bps(terms: dict[str, Any]) -> int | None:
    """What the new money buys: investment / (pre-money + investment), half-even to a basis
    point. None when either number is missing or the sum is zero."""
    pre, inv = terms.get("pre_money_minor"), terms.get("investment_minor")
    if pre is None or inv is None or pre + inv <= 0:
        return None
    return int(
        (Decimal(inv) * 10_000 / Decimal(pre + inv)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    )


def derived(terms: dict[str, Any]) -> dict[str, Any]:
    """The stated terms plus the numbers we can compute from them."""
    out = dict(terms)
    pre, inv = terms.get("pre_money_minor"), terms.get("investment_minor")
    out["post_money_minor"] = pre + inv if pre is not None and inv is not None else None
    out["investor_ownership_bps"] = investor_ownership_bps(terms)
    return out


def _rank(key: str, value: Any) -> float | None:
    if value is None:
        return None
    if key == "pro_rata":
        return PRO_RATA_RANK.get(value)
    if key == "anti_dilution":
        return ANTI_DILUTION_RANK.get(value)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return float(value)


def _extremes(key: str, better: str | None, values: dict[str, Any]) -> tuple[list[str], list[str]]:
    if better is None:
        return [], []
    ranked: dict[str, float] = {}
    for sid, v in values.items():
        r = _rank(key, v)
        if r is not None:
            ranked[sid] = r
    if len(ranked) < 2 or len(set(ranked.values())) < 2:
        return [], []
    lo, hi = min(ranked.values()), max(ranked.values())
    low_ids = sorted(s for s, r in ranked.items() if r == lo)
    high_ids = sorted(s for s, r in ranked.items() if r == hi)
    return (high_ids, low_ids) if better == "higher" else (low_ids, high_ids)


def compare(sheets: list[dict[str, Any]]) -> dict[str, Any]:
    """`sheets` are `{id, label, investor_id, investor_name, terms, flags}` dicts, confirmed ones
    only. Returns the columns, one row per stated term and the flags of each column."""
    cols = [
        {
            "id": s["id"],
            "label": s["label"],
            "investor_id": s.get("investor_id"),
            "investor_name": s.get("investor_name"),
            "stage": s["terms"].get("stage"),
            "flag_count": len(s["flags"]),
        }
        for s in sheets
    ]
    derived_terms = {s["id"]: derived(s["terms"]) for s in sheets}
    rows = []
    for key, label, unit, better in TERMS:
        values = {sid: d.get(key) for sid, d in derived_terms.items()}
        if all(v is None for v in values.values()):
            continue
        best, worst = _extremes(key, better, values)
        rows.append(
            {
                "term": key,
                "label": label,
                "unit": unit,
                "better": better,
                "values": values,
                "best": best,
                "worst": worst,
            }
        )
    return {"columns": cols, "rows": rows, "flags": {s["id"]: s["flags"] for s in sheets}}
