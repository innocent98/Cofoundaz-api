import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.models.enums import TicketCategory, TicketPriority
from app.schemas.staff import Reason

Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TicketCreateRequest(_Strict):
    category: TicketCategory
    subject: Subject
    body: Body


class MessageRequest(_Strict):
    body: Body


class AssignRequest(_Strict):
    # None clears the assignment.
    assignee_user_id: uuid.UUID | None


class PriorityRequest(_Strict):
    priority: TicketPriority


class EscalateRequest(_Strict):
    reason: Reason


class ResolveRequest(_Strict):
    # An optional closing message, posted to the thread and quoted in the closing email.
    message: Body | None = None


class ReopenRequest(_Strict):
    reason: Reason | None = None


class CannedRequest(_Strict):
    title: Title
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class CannedUpdateRequest(_Strict):
    title: Title | None = None
    body: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
        | None
    ) = None
