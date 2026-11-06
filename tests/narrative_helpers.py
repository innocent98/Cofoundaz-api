"""Fixtures for the narrative check tests: a canned claim extractor, a ready deck review on a real
PDF, and recorded transactions."""

import json
from datetime import UTC, datetime

from app.db.models.enums import TransactionSource
from app.db.models.finance import Transaction
from app.db.models.readiness import DeckReview
from tests.deck_helpers import store_deck

DRAFT = (
    "We make 2m NGN MRR today and burn 500k NGN a month. We are raising 90m NGN: 60% for "
    "engineering hires and 40% to open Ghana."
)

CLAIMS = {
    "claims": [
        # Deck (GOOD_SLIDES in tests/deck_helpers.py, slide 5 and 8).
        {
            "metric": "mrr",
            "value": 1600000,
            "currency": "NGN",
            "source": "deck",
            "slide": 2,  # wrong on purpose: the quote is on slide 5
            "quote": "MRR 1.6m NGN",
        },
        {
            "metric": "growth_pct",
            "value": 12,
            "currency": "NGN",  # not money: dropped to null
            "source": "deck",
            "slide": 5,
            "quote": "12% month on month growth",
        },
        {
            "metric": "ask",
            "value": 90000000,
            "currency": "NGN",
            "source": "deck",
            "slide": 8,
            "quote": "Raising 90m NGN",
        },
        # Draft.
        {
            "metric": "mrr",
            "value": 2000000,
            "currency": "NGN",
            "source": "draft",
            "slide": None,
            "quote": "2m NGN MRR",
        },
        {
            "metric": "burn",
            "value": 500000,
            "currency": "NGN",
            "source": "draft",
            "slide": None,
            "quote": "burn 500k NGN a month",
        },
        # Invented: these words are not in the draft.
        {
            "metric": "cash",
            "value": 5000000,
            "currency": "NGN",
            "source": "draft",
            "quote": "We have 5m NGN in the bank",
        },
        # Misread: the quote is real but says 2m, not 3m.
        {
            "metric": "mrr",
            "value": 3000000,
            "currency": "NGN",
            "source": "draft",
            "quote": "2m NGN MRR",
        },
        {"metric": "valuation", "value": 1, "source": "draft", "quote": "2m"},
    ],
    "use_of_funds": [
        {"label": "Engineering hires", "amount": None, "percent": 60, "source": "draft"},
        {"label": "Open Ghana", "amount": None, "percent": 40, "source": "draft"},
        {"label": "From nowhere", "percent": 10, "source": "email"},
    ],
}


class ClaimsLLM:
    """Stands in for `get_llm_client()`: returns fixed claims and records the prompt."""

    last_usage_tokens = 6100

    def __init__(self, payload: dict | None = None, *, boom: Exception | None = None):
        self.payload = payload if payload is not None else CLAIMS
        self.boom = boom
        self.calls: list[list] = []

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls.append(messages)
        if self.boom:
            raise self.boom
        return json.loads(json.dumps(self.payload))


def ready_review(db, startup, user, slides=None) -> DeckReview:
    f, _ = store_deck(db, startup, user, slides)
    r = DeckReview(
        startup_id=startup.id,
        file_id=f.id,
        filename=f.filename,
        version=1,
        content_sha256="x" * 64,
        status="ready",
        scores={"narrative": 7, "data_clarity": 6, "design": 8, "overall": 7.0},
        reviewed_at=datetime.now(UTC),
    )
    db.add(r)
    db.flush()
    return r


def txn(db, startup, direction, amount_minor, currency="NGN"):
    db.add(
        Transaction(
            startup_id=startup.id,
            date=datetime.now(UTC).date(),
            description="x",
            category="Other",
            amount_minor=amount_minor,
            currency=currency,
            direction=direction,
            source=TransactionSource.manual,
        )
    )
    db.flush()
