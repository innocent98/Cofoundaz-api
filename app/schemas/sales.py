import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models.enums import StageKind


class StageIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=60)
    kind: StageKind
    win_probability: int = Field(ge=0, le=100)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required.")
        return v


class StagesReplace(BaseModel):
    stages: list[StageIn]


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    position: int
    kind: StageKind
    win_probability: int


class SettingsUpdate(BaseModel):
    stall_days: int | None = Field(default=None, ge=3, le=90)
    outreach_postal_address: str | None = Field(default=None, max_length=500)


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stall_days: int
    outreach_postal_address: str | None


def _blank_to_none(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _clean_website(v: str | None) -> str | None:
    """http(s) only: the FE renders this as a link, so a `javascript:` value must never be stored."""
    v = _blank_to_none(v)
    if v is None:
        return None
    if "://" not in v and ":" not in v.split("/", 1)[0]:
        v = f"https://{v}"
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Website must be an http or https address.")
    return v


# ---- Accounts ----
class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=300)
    industry: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    owner_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required.")
        return v

    @field_validator("industry", "description")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return _blank_to_none(v)

    @field_validator("website")
    @classmethod
    def _website(cls, v: str | None) -> str | None:
        return _clean_website(v)


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=300)
    industry: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    owner_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("Name is required.")
        return v

    @field_validator("industry", "description")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return _blank_to_none(v)

    @field_validator("website")
    @classmethod
    def _website(cls, v: str | None) -> str | None:
        return _clean_website(v)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    website: str | None
    industry: str | None
    description: str | None
    owner_id: uuid.UUID | None
    created_at: datetime


# ---- Contacts ----
class ContactCreate(BaseModel):
    account_id: uuid.UUID | None = None
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=100)

    @field_validator("first_name")
    @classmethod
    def _strip_first(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("First name is required.")
        return v

    @field_validator("last_name", "phone", "title")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return _blank_to_none(v)


class ContactUpdate(BaseModel):
    account_id: uuid.UUID | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=100)

    @field_validator("first_name")
    @classmethod
    def _strip_first(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("First name is required.")
        return v

    @field_validator("last_name", "phone", "title")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return _blank_to_none(v)


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID | None
    first_name: str
    last_name: str | None
    email: str | None
    phone: str | None
    title: str | None
    created_at: datetime


class AccountDetailOut(BaseModel):
    account: AccountOut
    contacts: list[ContactOut]
    open_deals: list[dict[str, Any]]
    lifetime_won_minor: int
