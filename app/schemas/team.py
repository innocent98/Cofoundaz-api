import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.db.models.enums import MembershipRole


class InvitableRole(StrEnum):
    """The roles the invite modal offers (PRD 01.6 step 5 / 23.4). "Co-Founder" joins as
    `founder` (full access); investors aren't invited from here (they arrive via Funding)."""

    founder = MembershipRole.founder.value
    team_member = MembershipRole.team_member.value
    mentor = MembershipRole.mentor.value
    accountant = MembershipRole.accountant.value
    legal_advisor = MembershipRole.legal_advisor.value
    business_consultant = MembershipRole.business_consultant.value


class TeamInvite(BaseModel):
    email: EmailStr
    role: InvitableRole


class TeamInvitesRequest(BaseModel):
    # Bounded so one request can't fan out an unbounded number of emails.
    invites: list[TeamInvite] = Field(min_length=1, max_length=20)


CommentEntity = Literal["roadmap_milestone", "roadmap_task"]


class CommentCreate(BaseModel):
    entity_type: CommentEntity
    entity_id: uuid.UUID
    body: str = Field(min_length=1, max_length=4000)
    mention_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class CommentEdit(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class CommentResolve(BaseModel):
    resolved: bool = True
