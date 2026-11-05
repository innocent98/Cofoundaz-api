"""Marketplace constants that more than one module needs (design 2026-11-05, D-MP-6)."""

from datetime import timedelta
from typing import Final

CATEGORIES: Final[tuple[str, ...]] = ("Legal", "Accounting", "Design", "Development", "Marketing")
# Professions where a missing licence is unlicensed practice attributed to the platform (§1.3.6).
REGULATED: Final[frozenset[str]] = frozenset({"Legal", "Accounting"})

# D-MP-6 (a) rubric: the checks staff tick, and how many client references each category needs.
CHECK_KEYS: Final[tuple[str, ...]] = ("id", "licence", "references", "portfolio")
CHECK_LABELS: Final[dict[str, str]] = {
    "id": "Identity verified",
    "licence": "Professional licence verified",
    "references": "Client references checked",
    "portfolio": "Portfolio reviewed",
}
REFERENCES_REQUIRED: Final[dict[bool, int]] = {True: 2, False: 1}  # keyed by "is regulated"

TERMS_VERSION: Final = "draft-2026-11"  # counsel's real terms replace this (design §1.3.9)
INVITE_TTL: Final = timedelta(days=14)
DAILY_INVITE_CAP: Final = 50  # outbound-email abuse guard, per inviting staff member
LICENCE_EXPIRY_WARNING_DAYS: Final = 30

MAX_FILE_BYTES: Final = 15 * 1024 * 1024  # same cap as Documents
MAX_AVATAR_BYTES: Final = 5 * 1024 * 1024
MAX_FILES_PER_PURPOSE: Final[dict[str, int]] = {"credential": 10, "portfolio": 24, "avatar": 1}
MAX_PORTFOLIO_ITEMS: Final = 12


def required_checks(category: str | None) -> tuple[str, ...]:
    if category is None:
        return ()
    if category in REGULATED:
        return ("id", "licence", "references")
    return ("id", "portfolio", "references")
