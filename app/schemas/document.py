import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.db.models.enums import DocumentKind, DocumentStatus, ShareAccess


class DocumentCreate(BaseModel):
    kind: DocumentKind | None = None
    title: str | None = None
    folder: str | None = None
    template_key: str | None = None
    sections: list[dict] | None = None
    tags: list[str] | None = None


class DocumentSave(BaseModel):
    title: str = ""
    sections: list[dict] = Field(default_factory=list)
    status: DocumentStatus = DocumentStatus.draft
    folder: str | None = None
    version: int


# One address, no whitespace, angle brackets, commas or semicolons: nothing that could add a
# recipient or a header when the value reaches an email's To.
_EMAIL = re.compile(r"^[^@\s<>,;\"']{1,64}@[^@\s<>,;\"']{1,190}\.[^@\s<>,;\"']{2,}$")


def _check_email(value: str) -> str:
    value = value.strip()
    if len(value) > 255 or not _EMAIL.match(value):
        raise ValueError("Enter a valid email address.")
    return value


class ShareCreate(BaseModel):
    """`email` omitted = "anyone with the link" (nothing is emailed). `expires_at` (a date in the
    future, at most a year out) wins over `expires_in_days`; send `expires_in_days: null` and no
    `expires_at` for a link that never expires."""

    email: str | None = None
    access_level: ShareAccess = ShareAccess.view
    expires_in_days: int | None = 30
    expires_at: datetime | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return None if v is None else _check_email(v)


class ShareExpiry(BaseModel):
    """`expires_at: null` makes the link permanent."""

    expires_at: datetime | None


class SignerInput(BaseModel):
    email: str
    name: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _check_email(v)


class SignatureRequestCreate(BaseModel):
    signers: list[SignerInput] = Field(default_factory=list, max_length=20)
    title: str | None = Field(default=None, max_length=255)
    expires_in_days: int | None = 14
    # Sign one after another in the order given; each next signer gets their link only when the
    # one before has signed.
    sequential: bool = False
    # Shown to every signer; the default sentence is used when blank.
    message: str | None = Field(default=None, max_length=2000)


class SignAction(BaseModel):
    typed_name: str = Field(max_length=200)


class DocumentPatch(BaseModel):
    """Rename / move / tag / set status. Only the fields sent are changed; `folder: null` moves
    the document out of its folder."""

    title: str | None = None
    folder: str | None = None
    tags: list[str] | None = None
    status: DocumentStatus | None = None


class FilePatch(BaseModel):
    filename: str | None = None
    folder: str | None = None
    tags: list[str] | None = None


class FolderCreate(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None


class FolderRename(BaseModel):
    name: str


class GrantCreate(BaseModel):
    email: str | None = None
    user_id: uuid.UUID | None = None
    permission: ShareAccess = ShareAccess.view


class GrantUpdate(BaseModel):
    permission: ShareAccess


class DocumentCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000, pattern=r"\S")


class ExportRequest(BaseModel):
    format: Literal["pdf", "docx"]


class TemplateInstantiate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    folder: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    ai_draft: bool = False
    ai_sections: list[str] | None = None


class AiDraftRequest(BaseModel):
    sections: list[str] | None = None
