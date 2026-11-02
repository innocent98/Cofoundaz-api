"""Finance categories: one spelling per category per workspace.

Category is free text in the ledger, expenses and budgets, so "hosting" and "Hosting" used to be
two categories and a budget never matched an expense typed differently. Writes now resolve the
typed text to an existing spelling (case-insensitive), in this order: a default category, a
category the workspace already uses in any of the three tables, else the text as typed (first
spelling wins). Reads that match by category compare case-insensitively, so rows written before
this change still line up.
"""

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.budget import Budget
from app.db.models.expense import Expense
from app.db.models.finance import Transaction

# Spellings the product itself writes (the payments importer) plus the usual startup cost heads.
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "Revenue",
    "Funding",
    "Paystack payout",
    "Payment fees",
    "Refunds",
    "Payroll",
    "Software",
    "Infrastructure",
    "Marketing",
    "Equipment",
    "Rent",
    "Utilities",
    "Legal",
    "Taxes",
    "Travel",
    "Other",
)

UNCATEGORIZED = "Uncategorized"

# Money the founders put in or investors wired: it seeds paid-in capital in the financial model.
FUNDING_CATEGORY = "Funding"
# A bank credit that is Paystack paying out money the ledger already counted as the original
# charges (D-FIN-10). It is a transfer between the founder's own accounts, so it is kept out of
# revenue, money in and cash on hand (see cashflow._base_query).
PAYSTACK_PAYOUT_CATEGORY = "Paystack payout"


def fold(name: str) -> str:
    return name.strip().casefold()


def used_categories(db: Session, *, startup_id: uuid.UUID) -> dict[str, str]:
    """casefolded -> spelling for every category the workspace has written, oldest spelling wins."""
    found: dict[str, str] = {}
    for model, created in (
        (Transaction, Transaction.created_at),
        (Expense, Expense.created_at),
        (Budget, Budget.created_at),
    ):
        rows = (
            db.query(model.category)
            .filter(model.startup_id == startup_id, model.category.is_not(None))
            .group_by(model.category)
            .order_by(func.min(created))
            .all()
        )
        for (name,) in rows:
            found.setdefault(fold(name), name)
    return found


def normalize_category(db: Session, *, startup_id: uuid.UUID, raw: str | None) -> str | None:
    """Resolve typed text to the workspace's canonical spelling (see module docstring)."""
    if raw is None:
        return None
    key = fold(raw)
    for default in DEFAULT_CATEGORIES:
        if fold(default) == key:
            return default
    return used_categories(db, startup_id=startup_id).get(key, raw)


def list_categories(db: Session, *, startup_id: uuid.UUID) -> list[dict[str, Any]]:
    """Defaults plus the workspace's used categories, one entry per casefolded name."""
    used = used_categories(db, startup_id=startup_id)
    out: dict[str, dict[str, Any]] = {
        fold(name): {"name": name, "is_default": True, "in_use": fold(name) in used}
        for name in DEFAULT_CATEGORIES
    }
    for key, name in used.items():
        out.setdefault(key, {"name": name, "is_default": False, "in_use": True})
    return sorted(out.values(), key=lambda c: (not c["is_default"], fold(c["name"])))
