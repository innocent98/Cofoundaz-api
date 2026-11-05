"""Funding Hub data room request bodies (slice B)."""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FolderCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)


class FolderUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=0, le=10_000)


class ItemCreate(_Strict):
    """Exactly one of a rich Document or an uploaded file."""

    document_id: uuid.UUID | None = None
    file_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "ItemCreate":
        if (self.document_id is None) == (self.file_id is None):
            raise ValueError("Give exactly one of document_id or file_id.")
        return self


class ItemUpdate(_Strict):
    folder_id: uuid.UUID | None = None
    position: int | None = Field(default=None, ge=0, le=10_000)


class ShareCreate(_Strict):
    """A data room link for one investor email. `investor_id` ties it to the pipeline card."""

    email: EmailStr
    folder_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    investor_id: uuid.UUID | None = None
    allow_download: bool = False
    expires_at: dt.datetime | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    send_email: bool = True


class ShareUpdate(_Strict):
    """Fields left out are untouched; an explicit null clears the expiry (never expires)."""

    folder_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=50)
    allow_download: bool | None = None
    expires_at: dt.datetime | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class CodeRequest(_Strict):
    email: EmailStr


class CodeVerify(_Strict):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class Heartbeat(_Strict):
    view_id: uuid.UUID
