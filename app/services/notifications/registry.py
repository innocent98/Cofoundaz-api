import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import MembershipStatus
from app.db.models.membership import Membership
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.notifications.categories import category_for
from app.services.notifications.preferences import email_enabled
from app.services.notifications.service import create_notifications


def _active_member_ids(
    db: Session, startup_id: Any, *, exclude: uuid.UUID | None
) -> list[uuid.UUID]:
    rows = (
        db.query(Membership.user_id)
        .filter(Membership.startup_id == startup_id, Membership.status == MembershipStatus.active)
        .all()
    )
    ids = [r[0] for r in rows]
    return [i for i in ids if i != exclude] if exclude else ids


def _actor(payload: dict) -> uuid.UUID | None:
    for key in ("actor_id", "shared_by", "created_by", "user_id", "shared_by_id", "applied_by"):
        if payload.get(key):
            try:
                return uuid.UUID(str(payload[key]))
            except ValueError:
                return None
    return None


def _members_minus_actor(db: Session, payload: dict) -> list[uuid.UUID]:
    return _active_member_ids(db, payload["startup_id"], exclude=_actor(payload))


def _existing_members(db: Session, payload: dict) -> list[uuid.UUID]:
    # workspace.member.joined: notify everyone active EXCEPT the new joiner (payload["user_id"])
    joiner = _actor({"user_id": payload.get("user_id")})
    return _active_member_ids(db, payload["startup_id"], exclude=joiner)


def _all_active_members(db: Session, payload: dict) -> list[uuid.UUID]:
    return _active_member_ids(db, payload["startup_id"], exclude=None)


@dataclass(frozen=True)
class NotifSpec:
    recipients: Callable[[Session, dict], list[uuid.UUID]]
    title: Callable[[dict], str]
    body: Callable[[dict], str]


def _s(
    recipients: Callable[[Session, dict], list[uuid.UUID]], title: str, body: str = ""
) -> NotifSpec:
    return NotifSpec(recipients, lambda _p: title, lambda _p: body)


# v1 handled events. Copy is generic per type (payloads carry ids, not titles);
# the FE renders the deep link from `type` + `data`. Add an event by adding a row.
#
# NOTE: the brief lists "member.joined", but the real emitting site
# (app/services/onboarding/invites.py::accept_invitation) publishes
# "workspace.member.joined" with {startup_id, user_id, role} — the key below
# matches the event actually published so the handler fires.
SPECS: dict[str, NotifSpec] = {
    "document.shared": _s(_members_minus_actor, "A document was shared in your workspace"),
    "document.signature.requested": _s(_members_minus_actor, "A document needs a signature"),
    "document.signature.signed": _s(_members_minus_actor, "A signer signed a document"),
    "document.signature.completed": _s(_members_minus_actor, "A document is fully signed"),
    "business.suggestion.created": _s(_members_minus_actor, "A consultant suggested a change"),
    "business.suggestion.approved": _s(_members_minus_actor, "A suggestion was approved"),
    "business.suggestion.rejected": _s(_members_minus_actor, "A suggestion was rejected"),
    "business.artifact.completed": _s(
        _members_minus_actor, "A Business Builder artifact was completed"
    ),
    "roadmap.replanned": _s(_members_minus_actor, "Your roadmap was re-planned"),
    "roadmap.milestone.completed": _s(_members_minus_actor, "A roadmap milestone was completed"),
    "mission.completed": _s(_members_minus_actor, "Today's mission is complete"),
    "mission.streak.milestone": _s(_members_minus_actor, "You hit a mission streak milestone"),
    "healthscore.dropped": _s(_members_minus_actor, "Your Startup Health Score dropped"),
    "assessment.completed": _s(_members_minus_actor, "A startup assessment was completed"),
    "workspace.member.joined": _s(_existing_members, "A new member joined your workspace"),
    "mission.ready": _s(_all_active_members, "Today's mission is ready"),
    "roadmap.milestone.overdue": _s(_all_active_members, "A roadmap milestone is overdue"),
    "assessment.quarterly.due": _s(
        _all_active_members, "Time for your quarterly startup assessment"
    ),
    "business.plan.generated": _s(_all_active_members, "Your AI business plan is ready"),
    "marketing.post.published": NotifSpec(
        _members_minus_actor,
        lambda p: f"Scheduled post published: {p.get('title', 'a post')}",
        lambda _p: "",
    ),
    "marketing.campaign.launched": NotifSpec(
        _members_minus_actor,
        lambda p: f"Campaign launched: {p.get('name', 'a campaign')}",
        lambda _p: "",
    ),
    "marketing.campaign.completed": NotifSpec(
        _members_minus_actor,
        lambda p: f"Campaign completed: {p.get('name', 'a campaign')}",
        lambda _p: "",
    ),
}


def _handle(db: Session, event: str, payload: dict) -> None:
    spec = SPECS.get(event)
    if spec is None or not payload.get("startup_id"):
        return
    user_ids = spec.recipients(db, payload)
    if not user_ids:
        return
    startup_id = uuid.UUID(str(payload["startup_id"]))
    rows = create_notifications(
        db,
        user_ids=user_ids,
        startup_id=startup_id,
        type=event,
        title=spec.title(payload),
        body=spec.body(payload),
        data=payload,
    )
    category = category_for(event)
    for n in rows:
        if email_enabled(db, user_id=n.user_id, startup_id=n.startup_id, category=category):
            job_dispatcher.enqueue(
                db, "email.notification", {"notification_id": str(n.id)}, startup_id
            )


# Ids of buses already wired up, so a re-import or a second startup call does not
# double-subscribe the default bus. A set mutated in place (never rebound) avoids a
# module-level `global` while keeping the guard per-bus — tests pass a throwaway bus.
_registered_buses: set[int] = set()


def register(bus: Any = event_bus) -> None:
    """Subscribe every handled event on the bus. Idempotent for the default bus."""
    if bus is event_bus and id(bus) in _registered_buses:
        return
    for event in SPECS:
        bus.subscribe(event, lambda db, payload, e=event: _handle(db, e, payload))
    if bus is event_bus:
        _registered_buses.add(id(bus))
