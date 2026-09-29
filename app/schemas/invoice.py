import datetime as dt
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.db.models.enums import InvoiceTerms

INT32 = 2_147_483_647


# `dt.` alias: see app/schemas/finance.py (Py3.14 deferred-annotation shadowing).
class LineItem(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(ge=1, le=100000)
    unit_price_minor: int = Field(ge=0, le=INT32)


class InvoiceCreate(BaseModel):
    # Deliberately no subtotal/tax/total fields: the server computes every amount, so any
    # client-supplied total is dropped by Pydantic and can never influence what is stored.
    client_name: str = Field(min_length=1, max_length=200)
    client_email: EmailStr
    line_items: list[LineItem] = Field(min_length=1, max_length=50)
    tax_percent: float = Field(default=0, ge=0, le=100)
    currency: str = Field(default="NGN", min_length=1, max_length=3)
    terms: str = Field(default=InvoiceTerms.net_30.value)

    @field_validator("terms")
    @classmethod
    def _terms(cls, v: str) -> str:
        if v not in {t.value for t in InvoiceTerms}:
            raise ValueError("invalid terms")
        return v


class InvoiceUpdate(BaseModel):
    client_name: str | None = Field(default=None, min_length=1, max_length=200)
    client_email: EmailStr | None = None
    line_items: list[LineItem] | None = Field(default=None, min_length=1, max_length=50)
    tax_percent: float | None = Field(default=None, ge=0, le=100)
    currency: str | None = Field(default=None, min_length=1, max_length=3)
    terms: str | None = None
    auto_remind: bool | None = None

    @field_validator("terms")
    @classmethod
    def _terms(cls, v: str | None) -> str | None:
        if v is not None and v not in {t.value for t in InvoiceTerms}:
            raise ValueError("invalid terms")
        return v

    @model_validator(mode="after")
    def _no_explicit_null(self) -> "InvoiceUpdate":
        # Every column is NOT NULL: an explicit null (as opposed to an omitted field) would hit a
        # NOT NULL violation at flush (500), so reject it here as a 422.
        for name in (
            "client_name",
            "client_email",
            "line_items",
            "tax_percent",
            "currency",
            "terms",
            "auto_remind",
        ):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class MoneyLine(BaseModel):
    description: str
    quantity: int
    unit_price_minor: int
    amount_minor: int


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    number: str
    client_name: str
    client_email: str
    line_items: list[MoneyLine]
    subtotal_minor: int
    tax_percent: float
    tax_minor: int
    total_minor: int
    currency: str
    terms: str
    status: str  # derived: draft | sent | paid | overdue
    issued_on: dt.date | None
    due_on: dt.date | None
    paid_at: dt.datetime | None
    auto_remind: bool
    transaction_id: uuid.UUID | None
    created_at: dt.datetime
    updated_at: dt.datetime
