from typing import Any

from pydantic import BaseModel


class Meta(BaseModel):
    next_cursor: str | None = None
    total_estimate: int | None = None


def success_response(data: Any, meta: Meta | None = None) -> dict:
    return {"data": data, "meta": meta.model_dump() if meta else None}


def error_response(code: str, message: str, field_errors: list[dict] | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
        }
    }
