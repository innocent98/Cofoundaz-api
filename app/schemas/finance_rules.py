"""Validation rules shared by every Finance write schema (ledger, expenses, budgets)."""

import re

from pydantic_core import PydanticCustomError

# D-FIN-6: v1 is NGN only. One message for every money-bearing write.
NGN_ONLY_MESSAGE = "Only NGN is supported for now."

_SPACES = re.compile(r"\s+")


def ngn_only(value: str | None) -> str | None:
    """Accept "ngn" / "NGN" (stored as "NGN"); reject any other currency."""
    if value is None:
        return None
    if value.strip().upper() != "NGN":
        # PydanticCustomError, not ValueError: pydantic prefixes ValueError text with "Value error, ".
        raise PydanticCustomError("ngn_only", NGN_ONLY_MESSAGE)
    return "NGN"


def tidy_category(value: str | None) -> str | None:
    """Trim and collapse inner whitespace. Blank becomes None. Case is resolved server side."""
    if value is None:
        return None
    cleaned = _SPACES.sub(" ", value).strip()
    return cleaned or None
