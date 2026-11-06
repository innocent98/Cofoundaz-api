import re
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from app.db.models.enums import BusinessModel, MembershipRole, StartupStage
from app.schemas.team import InvitableRole

# The eight goal chips of the onboarding wizard (PRD §01.6 step 4, D-AUTH-7). Stored as these
# exact labels; anything else is rejected, so a goal can never carry markup or free text.
ONBOARDING_GOALS = (
    "Validate my idea",
    "Build my MVP",
    "Get first customers",
    "Grow revenue",
    "Raise funding",
    "Get legally set up",
    "Get financially organized",
    "Build my team",
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_HOST_PORT = re.compile(r"^[^/\s:@]+:\d{1,5}(?:[/?#]|$)")
_PHONE = re.compile(r"\+?[0-9(][0-9 ()\-.]{5,30}")


def _text(v: str, *, field: str, max_len: int, required: bool = True) -> str | None:
    """Collapse whitespace; refuse control characters and over-long values. Blank is an error
    when the field is required (a name that is just spaces must not be stored), else None."""
    if _CONTROL.search(v):
        raise PydanticCustomError(f"{field}_chars", "Use letters, spaces and punctuation only.")
    v = " ".join(v.split())
    if not v:
        if required:
            raise PydanticCustomError(f"{field}_required", "This field can't be blank.")
        return None
    if len(v) > max_len:
        raise PydanticCustomError(f"{field}_too_long", f"Keep it to {max_len} characters.")
    return v


class OnboardingStatePatch(BaseModel):
    step: int = Field(ge=1, le=6)
    # step 1
    full_name: str | None = None
    role_title: str | None = None
    country: str | None = None
    phone: str | None = None
    how_heard: str | None = None
    # step 2
    name: str | None = None
    description: str | None = None
    website: str | None = None
    # step 3
    industry: str | None = None
    business_model: BusinessModel | None = None
    stage: StartupStage | None = None
    # step 4
    goals: list[str] | None = Field(default=None, max_length=3)
    notes: str | None = None

    @field_validator("full_name")
    @classmethod
    def _full_name(cls, v: str | None) -> str | None:
        return None if v is None else _text(v, field="name", max_len=120)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _text(v, field="name", max_len=120)

    @field_validator("role_title")
    @classmethod
    def _role_title(cls, v: str | None) -> str | None:
        return None if v is None else _text(v, field="role", max_len=80)

    @field_validator("industry")
    @classmethod
    def _industry(cls, v: str | None) -> str | None:
        return None if v is None else _text(v, field="industry", max_len=80)

    @field_validator("how_heard")
    @classmethod
    def _how_heard(cls, v: str | None) -> str | None:
        return None if v is None else _text(v, field="how_heard", max_len=100, required=False)

    @field_validator("description")
    @classmethod
    def _description(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if _CONTROL.search(v.replace("\n", " ").replace("\t", " ")):
            raise PydanticCustomError(
                "description_chars", "Use letters, spaces and punctuation only."
            )
        v = v.strip()
        if len(v) > 1000:
            raise PydanticCustomError("description_too_long", "Keep it to 1000 characters.")
        return v or None

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if _CONTROL.search(v.replace("\n", " ").replace("\t", " ")):
            raise PydanticCustomError("notes_chars", "Use letters, spaces and punctuation only.")
        v = v.strip()
        if len(v) > 2000:
            raise PydanticCustomError("notes_too_long", "Keep it to 2000 characters.")
        return v or None

    @field_validator("country")
    @classmethod
    def _country(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise PydanticCustomError("country_code", "Use a two-letter country code, like NG.")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None  # blank clears the optional phone
        digits = sum(c.isdigit() for c in v)
        if not _PHONE.fullmatch(v) or digits < 7 or digits > 15:
            raise PydanticCustomError("phone_format", "Enter a valid phone number.")
        return v

    @field_validator("website")
    @classmethod
    def _website(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        bad = PydanticCustomError(
            "website_format", "Enter a web address starting with http or https."
        )
        if _CONTROL.search(v) or " " in v or len(v) > 255:
            raise bad
        if "://" in v:
            if urlsplit(v).scheme.lower() not in ("http", "https"):
                raise bad
        elif _SCHEME.match(v) and not _HOST_PORT.match(v):
            raise bad  # javascript:, data:, mailto:, ...
        else:
            v = f"https://{v}"  # "acme.com" typed without a scheme
        parts = urlsplit(v)
        host = parts.hostname or ""
        if not host or "." not in host or parts.username is not None:
            raise bad
        return v

    @field_validator("goals")
    @classmethod
    def _goals(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if any(g not in ONBOARDING_GOALS for g in v):
            raise PydanticCustomError("goal_unknown", "Pick goals from the list.")
        if len(set(v)) != len(v):
            raise PydanticCustomError("goal_duplicate", "Pick each goal once.")
        return v


class OnboardingInviteRole(StrEnum):
    """An explicit allowlist, not "every MembershipRole": a role added to the enum later is not
    invitable from here until it is listed. The six invite-modal roles, plus `investor`, which
    the wizard hides (D-AUTH-4) but this endpoint has always accepted."""

    founder = InvitableRole.founder.value
    team_member = InvitableRole.team_member.value
    mentor = InvitableRole.mentor.value
    accountant = InvitableRole.accountant.value
    legal_advisor = InvitableRole.legal_advisor.value
    business_consultant = InvitableRole.business_consultant.value
    investor = MembershipRole.investor.value


class InviteItem(BaseModel):
    # A plain string on purpose: a bad address is reported on its own row of the result
    # (`status: "invalid"`) instead of failing the whole request, so the FE can show which
    # rows to fix. Validated per row in `create_invitations`.
    email: str = Field(max_length=320)
    role: OnboardingInviteRole


class InvitesRequest(BaseModel):
    # Bounded like POST /team/invitations: one request can't fan out unbounded email.
    invites: list[InviteItem] = Field(max_length=20)


class AcceptRequest(BaseModel):
    token: str
