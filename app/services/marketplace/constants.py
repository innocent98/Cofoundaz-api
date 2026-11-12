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

# ---- slice C: booking, checkout and ledger
ACCEPT_WINDOW = timedelta(hours=48)  # D-MP-L7: the expert must accept within 48 h or it expires
SLOT_HOLD = timedelta(minutes=15)  # D-MP-L6: a slot (or a package order) is held 15 min at checkout
BRIEF_MIN = 10
BRIEF_MAX = 4000
MARKETPLACE_TERMS_VERSION: Final = "draft-2026-11"  # counsel's founder terms replace this (§1.3.9)
CONSENT_KIND_TERMS: Final = "marketplace_terms"
TIMER_BATCH = 200
# A hold Paystack can't answer for (outage, or an unexpected refusal) is retried every minute for
# this long past its expiry, then closed so it can never starve the rest of the batch. Closing is
# safe: a payment that turns up later still revives or refunds the booking (apply_payment).
UNVERIFIED_HOLD_GRACE = timedelta(hours=1)
REFUND_RETRY_BACKOFF_MIN: Final = (1, 5, 30, 120, 720)  # minutes, by attempt number
MAX_REFUND_ATTEMPTS: Final = 12
REFUND_INFLIGHT_WAIT = timedelta(hours=2)  # an unconfirmed refund isn't re-sent before this

# ---- slice D: fulfilment and messaging
AUTO_RELEASE_PACKAGE = timedelta(
    days=7
)  # a delivered package is accepted for the founder after this
AUTO_RELEASE_CONSULTATION = timedelta(hours=48)  # ... and a finished consultation after this
MAX_MESSAGE_CHARS: Final = 4000
MESSAGE_COLLAPSE = timedelta(minutes=10)  # one notification / email per thread per window
MAX_BRIEF_FILES: Final = 5
MAX_MESSAGE_FILES: Final = 5
MAX_DELIVERY_FILES: Final = 10
MAX_UNATTACHED_FILES: Final = 20  # per user and purpose: uploads not yet sent anywhere
MAX_BRIEF_FILE_BYTES: Final = 10 * 1024 * 1024  # D-MP-L10: briefs and messages, 10 MB each
BOOKING_DOCUMENT_TTL = timedelta(days=30)
MAX_BOOKING_DOCUMENTS: Final = 10
OVERDUE_BATCH: Final = 200
MAX_NEW_THREADS_PER_DAY: Final = 20  # new pre-booking conversations a workspace may open a day
