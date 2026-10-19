import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import FINANCE_ROLES, MARKETING_ROLES, MembershipRole, MembershipStatus
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


def _kpi_audience(db: Session, payload: dict) -> list[uuid.UUID]:
    """Only members whose role can see the metric (same sets as the KPI tiles' gate)."""
    roles = FINANCE_ROLES if payload.get("audience") == "finance" else MARKETING_ROLES
    rows = (
        db.query(Membership.user_id)
        .filter(
            Membership.startup_id == payload["startup_id"],
            Membership.status == MembershipStatus.active,
            Membership.role.in_(list(roles)),
        )
        .all()
    )
    return [r[0] for r in rows]


def _founders(db: Session, payload: dict) -> list[uuid.UUID]:
    """Active founders only (PRD 08: the founder approves a consultant's suggestions)."""
    rows = (
        db.query(Membership.user_id)
        .filter(
            Membership.startup_id == payload["startup_id"],
            Membership.status == MembershipStatus.active,
            Membership.role == MembershipRole.founder,
        )
        .all()
    )
    return [r[0] for r in rows]


def _validation_members(db: Session, payload: dict) -> list[uuid.UUID]:
    """Founders and team members: the only roles that can open the Validation Hub."""
    rows = (
        db.query(Membership.user_id)
        .filter(
            Membership.startup_id == payload["startup_id"],
            Membership.status == MembershipStatus.active,
            Membership.role.in_([MembershipRole.founder, MembershipRole.team_member]),
        )
        .all()
    )
    return [r[0] for r in rows]


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
    # PRD 08 notifications copy: to the founder, CTA "Review suggestions".
    "business.suggestion.created": NotifSpec(
        _founders,
        lambda p: (
            f"{p.get('author_name') or 'Your consultant'} suggested changes to "
            f"{p.get('artifact') or 'your Business Builder'}"
        ),
        lambda _p: "Review suggestions",
    ),
    "business.suggestion.approved": _s(_members_minus_actor, "A suggestion was approved"),
    "business.suggestion.rejected": _s(_members_minus_actor, "A suggestion was rejected"),
    "business.artifact.completed": _s(
        _members_minus_actor, "A Business Builder artifact was completed"
    ),
    "roadmap.replanned": _s(_members_minus_actor, "Your roadmap was re-planned"),
    "roadmap.milestone.completed": _s(_members_minus_actor, "A roadmap milestone was completed"),
    "mission.completed": _s(_members_minus_actor, "Today's mission is complete"),
    # PRD 04 notifications copy.
    "mission.streak.milestone": NotifSpec(
        _members_minus_actor,
        lambda p: (
            f"🔥 {p['streak']} days straight. You're building a habit most founders never do."
            if p.get("streak")
            else "You hit a mission streak milestone"
        ),
        lambda _p: "",
    ),
    # PRD 06 notifications copy (rendered copy avoids dashes).
    "healthscore.dropped": NotifSpec(
        _all_active_members,
        lambda p: (
            f"Your Health Score dropped {abs(int(p['delta_7d']))} points, worth a look"
            if p.get("delta_7d")
            else "Your Health Score dropped, worth a look"
        ),
        lambda _p: "See why",
    ),
    "healthscore.record": NotifSpec(
        _all_active_members,
        lambda p: (
            f"New personal best: {p['score']}. Keep going."
            if p.get("score") is not None
            else "New personal best. Keep going."
        ),
        lambda _p: "",
    ),
    "assessment.completed": _s(_members_minus_actor, "A startup assessment was completed"),
    "workspace.member.joined": _s(_existing_members, "A new member joined your workspace"),
    "mission.ready": NotifSpec(
        _all_active_members,
        lambda _p: "Your mission for today is ready",
        lambda p: p.get("first_task_title") or "",
    ),
    # PRD 05 notifications copy (no dashes in rendered copy).
    "roadmap.milestone.overdue": NotifSpec(
        _all_active_members,
        lambda _p: "Milestone overdue, I've drafted a re-plan for your review",
        lambda _p: "Review re-plan",
    ),
    # PRD 09 notifications: smoke-test signup milestone (D-VAL-5 thresholds in validation/smoke.py).
    "validation.smoke_test.milestone": NotifSpec(
        _validation_members,
        lambda p: f"{p.get('name') or 'Your smoke test'} just passed {p.get('signups')} signups",
        lambda _p: "",
    ),
    # PRD 09 Insight Synthesizer notification copy, CTA "See insight".
    "validation.insight.detected": NotifSpec(
        _validation_members,
        lambda p: f"New pattern detected in customer feedback: {p.get('theme') or 'a new theme'}",
        lambda _p: "See insight",
    ),
    "roadmap.milestone.due_soon": NotifSpec(
        _all_active_members,
        lambda p: (
            f"Milestone due soon: {p['title']}, {p.get('relative', 'soon')}"
            if p.get("title")
            else "A milestone is due soon"
        ),
        lambda _p: "",
    ),
    "assessment.quarterly.due": _s(
        _all_active_members, "Time for your quarterly startup assessment"
    ),
    # PRD 08 notifications copy, CTA "Open it".
    "business.plan.generated": _s(
        _all_active_members, "Your AI-generated business plan is ready", "Open it"
    ),
    "finance.runway.low": _s(_all_active_members, "Your runway is running low"),
    # PRD 02.1. In-app only (no email category): it's a daily nudge, not something to mail.
    "dashboard.briefing.ready": _s(_all_active_members, "Your daily AI briefing is ready"),
    # PRD 02.1 "{Metric} crossed {threshold}: now {value}"; title is composed by the job.
    # PRD 23 notifications copy: only the mentioned member is notified.
    "team.comment.mentioned": NotifSpec(
        lambda _db, p: [uuid.UUID(str(p["user_id"]))] if p.get("user_id") else [],
        lambda p: f'You were mentioned by {p.get("author_name") or "a teammate"}: "{p.get("snippet", "")}"',
        lambda p: p.get("entity_title") or "",
    ),
    "kpi.threshold.crossed": NotifSpec(
        _kpi_audience,
        lambda p: p.get("title") or "A key metric crossed a threshold",
        lambda _p: "",
    ),
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
