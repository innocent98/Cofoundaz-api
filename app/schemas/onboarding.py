from pydantic import BaseModel, Field

from app.db.models.enums import BusinessModel, MembershipRole, StartupStage


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


class InviteItem(BaseModel):
    email: str
    role: MembershipRole


class InvitesRequest(BaseModel):
    # Bounded like POST /team/invitations: one request can't fan out unbounded email.
    invites: list[InviteItem] = Field(max_length=20)


class AcceptRequest(BaseModel):
    token: str
