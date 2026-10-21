"""Every figure in the weekly readout is computed here, in Python, never by the model.

`build_figures` is the single source: the FE renders it as-is, `llm_facts` turns it into
pre-formatted strings for the prompt, and `unverified_numbers` checks the model's prose against
those strings. Prose that states any other number is retried once and then replaced by
`fallback_output`, so a readout is never stored with a number nobody computed.
"""

import re
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.enums import MarketingMetricName
from app.db.models.marketing import Campaign, MarketingMetric

# Marketing metrics carry no currency of their own; the whole workspace books in naira
# (spend is recorded in kobo, the same convention the Marketing page uses).
MARKETING_CURRENCY = "NGN"
_SYMBOLS = {"NGN": "₦"}
NA = "n/a"
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|\.\d+")
# Words that state a ratio without a digit; the model has no figure to back them.
_RATIO_WORDS = re.compile(r"\b(doubled|tripled|quadrupled|halved|twice|thrice)\b", re.IGNORECASE)


def format_money(minor: int, currency: str = MARKETING_CURRENCY) -> str:
    """Minor units -> major units, e.g. 450075 NGN -> "₦4,500.75"."""
    major = f"{Decimal(minor) / 100:,.2f}"
    symbol = _SYMBOLS.get(currency)
    return f"{symbol}{major}" if symbol else f"{major} {currency}"


def _fig(value: int | float | None, display: str) -> dict[str, Any]:
    return {"value": value, "display": display}


def _half_up(numerator: int, denominator: int) -> int:
    return int((Decimal(numerator) / Decimal(denominator)).quantize(Decimal(1), ROUND_HALF_UP))


def _pct(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return float(
        (Decimal(100 * numerator) / Decimal(denominator)).quantize(Decimal("0.1"), ROUND_HALF_UP)
    )


def _totals(
    db: Session, campaign_id: uuid.UUID, start: date | None, end: date | None
) -> dict[MarketingMetricName, int]:
    q = db.query(MarketingMetric.metric, func.sum(MarketingMetric.value)).filter(
        MarketingMetric.campaign_id == campaign_id
    )
    if start is not None:
        q = q.filter(MarketingMetric.ts >= start)
    if end is not None:
        q = q.filter(MarketingMetric.ts < end)
    return {m: int(v or 0) for m, v in q.group_by(MarketingMetric.metric)}


def _block(totals: dict[MarketingMetricName, int], currency: str) -> dict[str, Any]:
    spend = totals.get(MarketingMetricName.spend, 0)
    impressions = totals.get(MarketingMetricName.impressions, 0)
    clicks = totals.get(MarketingMetricName.clicks, 0)
    conversions = totals.get(MarketingMetricName.conversions, 0)
    ctr = _pct(clicks, impressions)
    cac = _half_up(spend, conversions) if conversions else None
    return {
        "spend": _fig(spend, format_money(spend, currency)),
        "impressions": _fig(impressions, f"{impressions:,}"),
        "clicks": _fig(clicks, f"{clicks:,}"),
        "ctr": _fig(ctr, NA if ctr is None else f"{ctr:.1f}%"),
        "conversions": _fig(conversions, f"{conversions:,}"),
        "cac": _fig(cac, NA if cac is None else format_money(cac, currency)),
    }


def _pct_change(cur: int | None, prev: int | None) -> dict[str, Any]:
    if cur is None or not prev:
        return _fig(None, NA)
    delta = _pct(cur - prev, prev)  # prev is truthy here, so this is never None
    if delta is None:
        return _fig(None, NA)
    return _fig(delta, "0.0%" if delta == 0 else f"{delta:+.1f}%")


def _points_change(cur: float | None, prev: float | None) -> dict[str, Any]:
    if cur is None or prev is None:
        return _fig(None, NA)
    delta = float((Decimal(str(cur)) - Decimal(str(prev))).quantize(Decimal("0.1")))
    return _fig(delta, "0.0 pts" if delta == 0 else f"{delta:+.1f} pts")


def build_figures(db: Session, campaign: Campaign, today: date) -> dict[str, Any]:
    """The last 7 days against the 7 before them, plus all time. `value` is the raw number
    (minor units for money, percentage points for rates), `display` the string to show."""
    cur_start, prev_start = today - timedelta(days=7), today - timedelta(days=14)
    this_week = _block(_totals(db, campaign.id, cur_start, today), MARKETING_CURRENCY)
    last_week = _block(_totals(db, campaign.id, prev_start, cur_start), MARKETING_CURRENCY)
    change: dict[str, Any] = {
        key: _pct_change(this_week[key]["value"], last_week[key]["value"])
        for key in ("spend", "impressions", "clicks", "conversions", "cac")
    }
    change["ctr"] = _points_change(this_week["ctr"]["value"], last_week["ctr"]["value"])
    days = (today - campaign.launched_at.date()).days if campaign.launched_at else None
    return {
        "currency": MARKETING_CURRENCY,
        "windows": {
            "this_week": {"start": cur_start.isoformat(), "end": today.isoformat()},
            "last_week": {"start": prev_start.isoformat(), "end": cur_start.isoformat()},
        },
        "this_week": this_week,
        "last_week": last_week,
        "change": change,
        "all_time": _block(_totals(db, campaign.id, None, None), MARKETING_CURRENCY),
        "days_live": None if days is None else _fig(days, f"{days} days"),
    }


def _displays(block: dict[str, Any]) -> dict[str, str]:
    return {k: v["display"] for k, v in block.items()}


def llm_facts(figures: dict[str, Any], campaign: Campaign | None = None) -> dict[str, Any]:
    """What the model is told: the objective and plan, and strings only. No raw numbers (so no
    raw kobo to repeat), no campaign name, no free text."""
    facts: dict[str, Any] = {
        "currency": figures["currency"],
        "window": "last 7 days",
        "this_week": _displays(figures["this_week"]),
        "last_week": _displays(figures["last_week"]),
        "change_vs_last_week": _displays(figures["change"]),
        "all_time": _displays(figures["all_time"]),
    }
    if figures["days_live"] is not None:
        facts["days_live"] = figures["days_live"]["display"]
    if campaign is not None:
        facts["objective"] = campaign.objective.value
        facts["status"] = campaign.status.value
        facts["channel_mix"] = {
            str(ch): f"{Decimal(str(pct)).normalize():f}%"
            for ch, pct in campaign.channel_mix.items()
        }
    return facts


def _numbers(text: str) -> list[str]:
    return [m.replace(",", "") for m in _NUMBER.findall(text)]


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def unverified_numbers(output: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """Numbers (and ratio words) in the prose that do not match a provided figure."""
    allowed = {Decimal(n) for s in _strings(facts) for n in _numbers(s)}
    bad: list[str] = []
    for text in _strings(output):
        bad += [n for n in _numbers(text) if Decimal(n) not in allowed]
        bad += [w.lower() for w in _RATIO_WORDS.findall(text)]
    return bad


def _trend(change: dict[str, Any], key: str) -> int:
    value = change[key]["value"]
    return 0 if value is None else (1 if value > 0 else -1 if value < 0 else 0)


def fallback_output(figures: dict[str, Any]) -> dict[str, Any]:
    """Deterministic template prose from the same figures, used when the model's numbers fail
    verification twice."""
    tw, lw, ch = figures["this_week"], figures["last_week"], figures["change"]

    def line(b: dict[str, Any]) -> str:
        return (
            f"spend {b['spend']['display']}, {b['impressions']['display']} impressions, "
            f"{b['clicks']['display']} clicks (CTR {b['ctr']['display']}), "
            f"{b['conversions']['display']} conversions (CAC {b['cac']['display']})"
        )

    if not any(tw[k]["value"] for k in ("spend", "impressions", "clicks", "conversions")):
        summary = "There is no data for this campaign in the last 7 days."
        return {
            "headline": "No campaign data this week",
            "summary": summary,
            "wins": [],
            "watch": ["No activity was recorded, so there is nothing to compare yet."],
            "next_step": "Record this campaign's metrics so next week's readout has data.",
        }
    labels = {"clicks": "Clicks", "conversions": "Conversions", "impressions": "Impressions"}
    wins = [
        f"{label} {ch[k]['display']} on last week ({tw[k]['display']})."
        for k, label in labels.items()
        if _trend(ch, k) > 0
    ][:3]
    watch = [
        f"{label} {ch[k]['display']} on last week ({tw[k]['display']})."
        for k, label in labels.items()
        if _trend(ch, k) < 0
    ]
    if not tw["conversions"]["value"]:
        watch.append("There were no conversions this week.")
    up = _trend(ch, "clicks") + _trend(ch, "conversions")
    return {
        "headline": (
            "Campaign picking up" if up > 0 else "Campaign slowing" if up < 0 else "Campaign steady"
        ),
        "summary": f"Last 7 days: {line(tw)}. Previous 7 days: {line(lw)}.",
        "wins": wins,
        "watch": watch[:3],
        "next_step": "Review the channel that drove the most clicks and shift effort toward it.",
    }
