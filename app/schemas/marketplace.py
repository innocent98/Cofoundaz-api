import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.schemas.staff import Reason, TotpCode

Category = Literal["Legal", "Accounting", "Design", "Development", "Marketing"]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Lang = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=30)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Message = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcceptInviteRequest(_Strict):
    token: Annotated[str, StringConstraints(min_length=20, max_length=100)]
    accept_terms: bool

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You need to accept the expert terms to continue.")
        return v


class ApplyRequest(_Strict):
    category: Category | None = None
    accept_terms: bool

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You need to accept the expert terms to continue.")
        return v


class ProfilePatch(_Strict):
    display_name: Name | None = None
    headline: Short | None = None
    bio: Annotated[str, StringConstraints(strip_whitespace=True, max_length=3000)] | None = None
    category: Category | None = None
    categories: Annotated[list[Category], Field(max_length=3)] | None = None
    languages: Annotated[list[Lang], Field(max_length=8)] | None = None
    timezone: Annotated[str, StringConstraints(max_length=64)] | None = None
    location: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    avatar_file_id: uuid.UUID | None = None


class PortfolioCreate(_Strict):
    title: Short
    description: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None
    ) = None
    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    file_id: uuid.UUID | None = None

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str | None) -> str | None:
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("Links must start with http:// or https://")
        return v


class PortfolioPatch(PortfolioCreate):
    title: Short | None = None  # type: ignore[assignment]
    position: Annotated[int, Field(ge=0, le=1000)] | None = None


class InviteRequest(_Strict):
    email: EmailStr
    category: Category | None = None
    display_name: Name | None = None


class CheckIn(_Strict):
    done: bool
    note: Note | None = None
    expires_on: date | None = None  # licence only
    count: Annotated[int, Field(ge=0, le=20)] | None = None  # references only


class VettingRequest(_Strict):
    checks: Annotated[
        dict[Literal["id", "licence", "references", "portfolio"], CheckIn], Field(min_length=1)
    ]


class ReasonRequest(_Strict):
    reason: Reason


class StepUpReasonRequest(_Strict):
    reason: Reason
    totp_code: TotpCode


class RequestChangesRequest(_Strict):
    message: Message


class RejectRequest(_Strict):
    template: Literal[
        "incomplete_documents",
        "licence_not_verified",
        "references_not_verified",
        "outside_scope",
        "other",
    ]
    message: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
        | None
    ) = None
    reason: Reason
    totp_code: TotpCode


class ClearFlagRequest(_Strict):
    reason: Reason
    # `profile_flag.updated_at` as the reviewer saw it; a newer edit makes the clear a 409.
    updated_at: Annotated[str, StringConstraints(min_length=10, max_length=40)]
