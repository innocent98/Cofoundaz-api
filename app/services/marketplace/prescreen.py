"""Deterministic pre-screen of a profile's text (D-MP-L9). No AI: a handful of rules whose result
staff see as chips next to the application. It never blocks a save; it only tells the reviewer
where to look."""

import re
from typing import Any

from app.services.marketplace.constants import PRICE_MIN_MINOR, PRICE_SANE_MAX_MINOR

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]?){9,}(?!\d)")
_HANDLE = re.compile(r"\b(whatsapp|telegram|t\.me|wa\.me|signal)\b", re.IGNORECASE)
_GUARANTEE = re.compile(
    r"\b(guarantee[sd]?|100\s?%\s*(?:success|approval|returns?))\b.{0,40}"
    r"\b(approval|returns?|funding|results?|success|visa|loan)\b",
    re.IGNORECASE,
)
_PROHIBITED = re.compile(
    r"\b(forged?|fake (?:documents?|certificates?)|evade tax)\b", re.IGNORECASE
)

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("contact_details", _EMAIL),
    ("contact_details", _PHONE),
    ("contact_details", _HANDLE),
    ("guaranteed_outcome", _GUARANTEE),
    ("prohibited_content", _PROHIBITED),
)
SCANNED_FIELDS = ("display_name", "headline", "bio", "location")


def prescreen(fields: dict[str, str | None]) -> dict[str, Any]:
    flags: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name in SCANNED_FIELDS:
        text = fields.get(name) or ""
        for rule, pattern in _RULES:
            if (rule, name) not in seen and pattern.search(text):
                seen.add((rule, name))
                flags.append({"rule": rule, "field": name})
    return {"flags": flags, "clean": not flags}


LISTING_FIELDS = ("title", "summary", "description", "deliverables", "tags")


def prescreen_listing(fields: dict[str, object], price_minor: int | None) -> dict[str, Any]:
    """The same deterministic rules over a listing's text, plus a price sanity chip."""
    flat: dict[str, str | None] = {}
    for name in LISTING_FIELDS:
        value = fields.get(name)
        if isinstance(value, list):
            flat[name] = " ".join(str(v) for v in value)
        else:
            flat[name] = str(value) if value else None
    flags: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, text in flat.items():
        for rule, pattern in _RULES:
            if (rule, name) not in seen and text and pattern.search(text):
                seen.add((rule, name))
                flags.append({"rule": rule, "field": name})
    if price_minor is not None and not PRICE_MIN_MINOR <= price_minor <= PRICE_SANE_MAX_MINOR:
        flags.append({"rule": "price_out_of_range", "field": "price_minor"})
    return {"flags": flags, "clean": not flags}
