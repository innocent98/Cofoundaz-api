import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


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
