import datetime as dt
import uuid

from pydantic import BaseModel, Field, model_validator

from app.db.models.enums import TransactionDirection


# `dt.` alias: a bare `date` annotation would resolve to the field's own default
# under Python 3.14 deferred annotation evaluation.
class TransactionCreate(BaseModel):
    date: dt.date
    description: str = Field(min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    amount_minor: int = Field(ge=0)
    currency: str = Field(default="NGN", min_length=1, max_length=3)
    direction: TransactionDirection


class TransactionUpdate(BaseModel):
    date: dt.date | None = None
    description: str | None = Field(default=None, min_length=1, max_length=300)
    category: str | None = Field(default=None, max_length=120)
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=1, max_length=3)
    direction: TransactionDirection | None = None

    @model_validator(mode="after")
    def _no_explicit_null_on_required(self) -> "TransactionUpdate":
        # Only `category` is nullable on the row; an explicit null for any other field would hit a
        # NOT NULL violation at flush (500). Reject it here as a 422 instead. Omitting a field is
        # still fine (partial update) — this only fires when the field was explicitly sent as null.
        for name in ("date", "description", "amount_minor", "currency", "direction"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class TransactionResponse(BaseModel):
    id: uuid.UUID
    date: dt.date
    description: str
    category: str | None
    amount_minor: int
    currency: str
    direction: TransactionDirection
    source: str
    created_at: dt.datetime
    updated_at: dt.datetime
