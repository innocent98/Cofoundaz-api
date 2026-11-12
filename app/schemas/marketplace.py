import uuid
from datetime import date, time
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

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


# ---------------------------------------------------------------- slice B: listings, availability

ListingTypeLit = Literal["consultation", "package"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=160)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Deliverable = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Kobo = Annotated[int, Field(ge=1, le=10_000_000_000, strict=True)]  # a string "5000" is refused


def _https(v: str | None) -> str | None:
    if v and not v.lower().startswith(("http://", "https://")):
        raise ValueError("Links must start with http:// or https://")
    return v


class ListingFields(_Strict):
    title: Title | None = None
    summary: Summary | None = None
    description: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)] | None
    ) = None
    category: Category | None = None
    tags: Annotated[list[Tag], Field(max_length=8)] | None = None
    price_minor: Kobo | None = None
    duration_min: Annotated[int, Field(ge=15, le=240, multiple_of=15)] | None = None
    delivery_days: Annotated[int, Field(ge=1, le=90)] | None = None
    deliverables: Annotated[list[Deliverable], Field(max_length=10)] | None = None
    revisions_included: Annotated[int, Field(ge=0, le=5)] | None = None
    meeting_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = (
        None
    )

    @field_validator("meeting_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _https(v)


class ListingCreate(ListingFields):
    type: ListingTypeLit
    title: Title
    category: Category
    price_minor: Kobo


class ListingPatch(ListingFields):
    pass


class RuleIn(_Strict):
    weekday: Annotated[int, Field(ge=0, le=6)]
    start: time
    end: time
    slot_min: Literal[15, 30, 60] = 30

    @field_validator("start", "end")
    @classmethod
    def _whole_minutes(cls, v: time) -> time:
        if v.second or v.microsecond or v.tzinfo:
            raise ValueError("Use plain HH:MM times.")
        return v


class ExceptionIn(_Strict):
    on_date: date
    kind: Literal["blocked", "extra"]
    start: time | None = None
    end: time | None = None

    @field_validator("start", "end")
    @classmethod
    def _whole_minutes(cls, v: time | None) -> time | None:
        if v is not None and (v.second or v.microsecond or v.tzinfo):
            raise ValueError("Use plain HH:MM times.")
        return v


class AvailabilityPut(_Strict):
    min_notice_h: Annotated[int, Field(ge=0, le=336)]
    buffer_min: Annotated[int, Field(ge=0, le=120)]
    rules: Annotated[list[RuleIn], Field(max_length=28)]
    exceptions: Annotated[list[ExceptionIn], Field(max_length=90)] = []


class ListingRejectRequest(_Strict):
    template: Literal["unclear_scope", "pricing", "prohibited_content", "contact_details", "other"]
    message: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
        | None
    ) = None
    reason: Reason


class ListingChangesRequest(_Strict):
    message: Message


# ---------------------------------------------------------------- slice C: booking and checkout


class BookingCreate(_Strict):
    listing_id: uuid.UUID
    starts_at: AwareDatetime | None = None  # consultations only; must carry a UTC offset
    brief: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)]
    accept_terms: bool = False
    pay_with: Literal["card", "saved_card"] = "card"
    # Uploaded with purpose `brief` through POST /marketplace/files, unattached and yours.
    file_ids: Annotated[list[uuid.UUID], Field(max_length=5)] = Field(default_factory=list)


# ---------------------------------------------------------------- slice D: fulfilment, messaging

Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DeliverRequest(_Strict):
    message: Body
    file_ids: Annotated[list[uuid.UUID], Field(max_length=10)] = Field(default_factory=list)


class RequestChangesBody(_Strict):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class MessageCreate(_Strict):
    body: Body
    file_ids: Annotated[list[uuid.UUID], Field(max_length=5)] = Field(default_factory=list)


class ThreadOpen(_Strict):
    provider_id: uuid.UUID


class AttachDocument(_Strict):
    document_id: uuid.UUID
