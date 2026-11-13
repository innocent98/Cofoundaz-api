"""The term sheet rules table (D-FUND-6): which terms are "unusual for this stage".

This file IS the policy. A reviewer reads the table below; the model never decides whether
something is flagged, it only rewords the message in plainer language (and may not change a
number). Wording is deliberately never a verdict: "unusual for this stage", never "bad" or "reject".
Each rule has a stable id (stored on the flag), the term it reads, its threshold and a fixed
message built from the sheet's own values.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DISCLAIMER = (
    "Negotiate with clarity. This is an automated reading of the terms you entered, not legal or "
    "financial advice. Ask a lawyer to review a real term sheet before you sign."
)

EARLY_STAGES = ("pre_seed", "seed")


@dataclass(frozen=True)
class Rule:
    id: str
    term: str  # the key in `terms` it reads (the one shown next to the flag)
    title: str
    threshold: str  # shown to the founder: what we compared against
    applies: Callable[[dict[str, Any]], bool]
    message: Callable[[dict[str, Any]], str]


def _x(v: int) -> str:
    return f"{v / 100:g}x"


def _pct(bps: int) -> str:
    return f"{bps / 100:g}%"


RULES: tuple[Rule, ...] = (
    Rule(
        "liq_pref_above_1x",
        "liq_pref_x100",
        "Liquidation preference above 1x",
        "1x",
        lambda t: (t.get("liq_pref_x100") or 0) > 100,
        lambda t: (
            f"The investor gets {_x(t['liq_pref_x100'])} their money back before anyone else "
            "shares in a sale. 1x is the usual ask at this stage."
        ),
    ),
    Rule(
        "participating_preferred",
        "participating",
        "Participating preferred",
        "non-participating",
        lambda t: t.get("participating") is True,
        lambda t: (
            "The investor takes their money back first and then also shares in what is left "
            "(double dip)"
            + (
                f", up to {_x(t['participation_cap_x100'])} in total."
                if t.get("participation_cap_x100")
                else ", with no stated cap."
            )
        ),
    ),
    Rule(
        "option_pool_above_15pct",
        "option_pool_pre_bps",
        "Large option pool in the pre-money",
        "15% of pre-money",
        lambda t: (t.get("option_pool_pre_bps") or 0) > 1500,
        lambda t: (
            f"The option pool is {_pct(t['option_pool_pre_bps'])} of the pre-money. Because it sits "
            "in the pre-money, founders carry all of that dilution."
        ),
    ),
    Rule(
        "exclusivity_above_45_days",
        "exclusivity_days",
        "Long exclusivity (no-shop)",
        "45 days",
        lambda t: (t.get("exclusivity_days") or 0) > 45,
        lambda t: (
            f"You agree not to talk to other investors for {t['exclusivity_days']} days. "
            "Up to 45 days is common."
        ),
    ),
    Rule(
        "investor_board_seats_early",
        "investor_board_seats",
        "More than one investor board seat on an early round",
        "1 investor seat",
        lambda t: (t.get("investor_board_seats") or 0) > 1 and t.get("stage") in EARLY_STAGES,
        lambda t: (
            f"The investor gets {t['investor_board_seats']} board seats on a "
            f"{'pre-seed' if t.get('stage') == 'pre_seed' else 'seed'} round. At this stage one "
            "seat, or an observer, is more common."
        ),
    ),
    Rule(
        "super_pro_rata",
        "pro_rata",
        "Super pro-rata",
        "standard pro-rata",
        lambda t: t.get("pro_rata") == "super",
        lambda t: (
            "The investor can buy more than their share of future rounds, which can crowd out "
            "new investors."
        ),
    ),
    Rule(
        "full_ratchet",
        "anti_dilution",
        "Full-ratchet anti-dilution",
        "broad-based weighted average",
        lambda t: t.get("anti_dilution") == "full_ratchet",
        lambda t: (
            "If you later raise at a lower price, the investor's price resets all the way down "
            "to it. Broad-based weighted average is the usual protection."
        ),
    ),
)

RULES_BY_ID = {r.id: r for r in RULES}


def evaluate(terms: dict[str, Any]) -> list[dict[str, Any]]:
    """The flags for a set of terms, in table order. An unstated term never flags."""
    flags = []
    for r in RULES:
        if r.applies(terms):
            flags.append(
                {
                    "rule": r.id,
                    "term": r.term,
                    "title": r.title,
                    "value": terms.get(r.term),
                    "threshold": r.threshold,
                    "text": r.message(terms),
                    "ai_text": None,
                }
            )
    return flags
