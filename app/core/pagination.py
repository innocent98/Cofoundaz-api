import base64
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.errors import AppError


@dataclass
class CursorPage:
    items: list[Any]
    next_cursor: str | None
    total_estimate: int | None


def encode_cursor(created_at: datetime, id: uuid.UUID) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "i": str(id)}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        data = json.loads(raw)
        return datetime.fromisoformat(data["t"]), uuid.UUID(data["i"])
    except Exception as exc:
        raise ValueError("Invalid cursor") from exc


# ---- keyset pagination over a fixed descending sort (used by the Finance lists) --------------- #

DEFAULT_LIMIT = 100
MAX_LIMIT = 200


def encode_keyset(*parts: str) -> str:
    """Opaque cursor carrying the sort-key values of the last row of a page."""
    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode()).decode()


def decode_keyset(cursor: str, *parsers: Callable[[str], Any]) -> tuple[Any, ...]:
    """Inverse of `encode_keyset`: one parser per sort-key part. A malformed or foreign cursor is
    a 422, never a 500."""
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(parts, list) or len(parts) != len(parsers):
            raise ValueError("wrong shape")
        return tuple(parse(str(p)) for parse, p in zip(parsers, parts, strict=True))
    except Exception as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "Invalid pagination cursor.",
            422,
            field_errors=[{"field": "cursor", "message": "This page link is no longer valid."}],
        ) from exc
