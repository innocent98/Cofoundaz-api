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


# ---- slice B: listings and availability
DEFAULT_COMMISSION_BPS: Final = 1500  # D-MP-5: 15% taken from the expert's side
PRICE_MIN_MINOR: Final = 100_000  # N1,000 in kobo: below this is flagged, not refused
PRICE_SANE_MAX_MINOR: Final = 1_000_000_000  # N10,000,000: above this is flagged
PRICE_HARD_MAX_MINOR: Final = 10_000_000_000  # N100,000,000: refused outright
CONSULT_DURATIONS: Final = tuple(range(15, 241, 15))
MAX_DELIVERABLES: Final = 10
MAX_TAGS: Final = 8
MAX_LISTINGS_PER_PROVIDER: Final = 20
DEFAULT_MIN_NOTICE_H: Final = 24
SLOT_STEPS: Final = (15, 30, 60)
MAX_SLOT_RANGE_DAYS: Final = 14
MAX_RULES: Final = 28
MAX_EXCEPTIONS: Final = 90
DIRECTORY_PAGE_SIZE: Final = 12
