import datetime as dt
import uuid

from pydantic import BaseModel, Field

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
