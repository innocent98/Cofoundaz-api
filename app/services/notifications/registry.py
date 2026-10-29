import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.money import format_money
from app.db.models.document import Document, DocumentGrant
from app.db.models.enums import SALES_ROLES, MembershipRole, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.notifications.audience import AUDIENCE, FOUNDER_ROLES, kpi_roles
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


def _with_roles(db: Session, startup_id: Any, roles: frozenset[MembershipRole]) -> list[uuid.UUID]:
    rows = (
        db.query(Membership.user_id)
        .filter(
            Membership.startup_id == startup_id,
            Membership.status == MembershipStatus.active,
            Membership.role.in_(list(roles)),
        )
        .all()
    )
    return [r[0] for r in rows]


def _journal_owner(db: Session, payload: dict) -> list[uuid.UUID]:
    """The one founder whose journal nudge this is (still an active founder of the workspace)."""
    if not payload.get("founder_id"):
        return []
    owner = uuid.UUID(str(payload["founder_id"]))
    return [u for u in _with_roles(db, payload["startup_id"], FOUNDER_ROLES) if u == owner]


def _audience(
    event: str, *, minus_actor: bool = True
) -> Callable[[Session, dict], list[uuid.UUID]]:
    """Recipients = active members holding one of AUDIENCE[event]'s roles (the read gate of the
    feature the notification opens), minus the actor when `minus_actor`. The read paths filter by
    the same map, so what is delivered and what is shown cannot drift."""
    roles = AUDIENCE[event]

    def _recipients(db: Session, payload: dict) -> list[uuid.UUID]:
        actor = _actor(payload) if minus_actor else None
        return [u for u in _with_roles(db, payload["startup_id"], roles) if u != actor]

    return _recipients


def _deal_won_once_a_day(db: Session, payload: dict) -> list[uuid.UUID]:
    """`sales.deal.won` recipients (founder + team members, the mover included), except none when
    this deal already produced a won notification today (UTC): a second drag into Closed Won is
    not news, a reopen and win on a later day is."""
    today = datetime.now(UTC).date()
    already = (
        db.query(Notification.id)
        .filter(
            Notification.startup_id == payload["startup_id"],
            Notification.type == "sales.deal.won",
            Notification.data["deal_id"].astext == str(payload.get("deal_id")),
            func.date(func.timezone("UTC", Notification.created_at)) == today,
        )
        .first()
    )
    return [] if already else _audience("sales.deal.won", minus_actor=False)(db, payload)


def _deal_stalled_recipients(db: Session, payload: dict) -> list[uuid.UUID]:
    """The deal owner while they are still an active founder or team member, else every active
    founder (nobody when there is none)."""
    startup_id = payload["startup_id"]
    members = _with_roles(db, startup_id, SALES_ROLES)
    owner = payload.get("owner_id")
    if owner:
        try:
            owner_id = uuid.UUID(str(owner))
        except ValueError:
            owner_id = None
        if owner_id in members:
            return [owner_id]
    return _with_roles(db, startup_id, frozenset({MembershipRole.founder}))


def _document_owner_recipients(db: Session, payload: dict, *, minus_actor: bool) -> list[uuid.UUID]:
    """The founder or team member who owns the document (or sent the signature request) while
    they still hold a Documents role, else every active founder. Never a mentor, consultant or
    any other role (D-DOC-1), and never the workspace at large."""
    startup_id = payload["startup_id"]
    members = _with_roles(db, startup_id, AUDIENCE["document.shared"])
    actor = _actor(payload) if minus_actor else None
    owner: uuid.UUID | None = None
    try:
        owner = uuid.UUID(str(payload["owner_id"])) if payload.get("owner_id") else None
    except ValueError:
        owner = None
    chosen = [owner] if owner in members else _with_roles(db, startup_id, FOUNDER_ROLES)
    return [u for u in chosen if u is not None and u != actor]


def _document_shared(db: Session, payload: dict) -> list[uuid.UUID]:
    return _document_owner_recipients(db, payload, minus_actor=True)


def _document_grantees(db: Session, payload: dict) -> list[uuid.UUID]:
    """Members with a live grant on the (non-trashed) document. A grantee outside the workspace
    has no workspace inbox to deliver to, so they are told by email instead."""
    rows = (
        db.query(DocumentGrant.grantee_user_id)
        .join(Document, Document.id == DocumentGrant.document_id)
        .join(
            Membership,
            (Membership.user_id == DocumentGrant.grantee_user_id)
            & (Membership.startup_id == Document.startup_id),
        )
        .filter(
            DocumentGrant.document_id == payload["document_id"],
            DocumentGrant.revoked_at.is_(None),
            DocumentGrant.grantee_user_id.isnot(None),
            Document.trashed_at.is_(None),
            Membership.status == MembershipStatus.active,
        )
        .all()
    )
    return [r[0] for r in rows]


def _document_version_created(db: Session, payload: dict) -> list[uuid.UUID]:
    """Everyone who can open the document except whoever saved the version: its owner (or the
    founders) and members holding a grant."""
    actor = _actor(payload)
    chosen = [
        *_document_owner_recipients(db, payload, minus_actor=True),
        *_document_grantees(db, payload),
    ]
    return [u for u in dict.fromkeys(chosen) if u != actor]


def _document_grant_created(db: Session, payload: dict) -> list[uuid.UUID]:
    """The grantee, when they are already a member of the workspace (else the email is all)."""
    return [
        u for u in _document_grantees(db, payload) if str(u) == str(payload.get("grantee_user_id"))
    ]


def _document_signed(db: Session, payload: dict) -> list[uuid.UUID]:
    return _document_owner_recipients(db, payload, minus_actor=False)


def _nobody(_db: Session, _payload: dict) -> list[uuid.UUID]:
    """`document.signature.requested`: the signers are external and get the email; no member
    has anything to sign, so there is nobody to tell in-app."""
    return []


def _kpi_audience(db: Session, payload: dict) -> list[uuid.UUID]:
    """Only members whose role can see the metric (same sets as the KPI tiles' gate)."""
    return _with_roles(db, payload["startup_id"], kpi_roles(payload))


def _learner(event: str) -> Callable[[Session, dict], list[uuid.UUID]]:
    """The learner the event is about, while they still hold an Academy role (F or TM)."""
    roles = AUDIENCE[event]

    def _recipients(db: Session, payload: dict) -> list[uuid.UUID]:
        learner = payload.get("user_id")
        return [u for u in _with_roles(db, payload["startup_id"], roles) if str(u) == str(learner)]

    return _recipients


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
    "document.shared": NotifSpec(
        _document_shared,
        lambda p: (
            f"{p.get('sharer_name') or 'A teammate'} shared a document: {p['title']}"
            if p.get("title")
            else "A document was shared"
        ),
        lambda _p: "Open document",
    ),
    "document.signature.requested": _s(_nobody, "A document needs a signature"),
    "document.signature.signed": NotifSpec(
        _document_signed,
        lambda p: (
            f"{p.get('signer_name') or 'A signer'} signed {p['title']}"
            if p.get("title")
            else "A signer signed a document"
        ),
        lambda _p: "View request",
    ),
    "document.signature.completed": NotifSpec(
        _document_signed,
        lambda p: (
            f"{p['title']} is fully signed" if p.get("title") else "A document is fully signed"
        ),
        lambda _p: "View request",
    ),
    # PRD 18 copy "New version uploaded: {doc} (v{n}) by {name}". Open type (no AUDIENCE row):
    # the recipients are computed exactly, and a grantee's role is not a Documents role.
    "document.version.created": NotifSpec(
        _document_version_created,
        lambda p: (
            f"New version uploaded: {p.get('title') or 'a document'} (v{p.get('version')}) "
            f"by {p.get('author_name') or 'a teammate'}"
        ),
        lambda _p: "Open document",
    ),
    "document.grant.created": NotifSpec(
        _document_grant_created,
        lambda p: (
            f"{p.get('sharer_name') or 'Someone'} shared {p.get('title') or 'a document'} with you"
        ),
        lambda _p: "Open document",
    ),
    # PRD 08 notifications copy: to the founder, CTA "Review suggestions".
    "business.suggestion.created": NotifSpec(
        _audience("business.suggestion.created", minus_actor=False),
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
    "mission.completed": _s(_audience("mission.completed"), "Today's mission is complete"),
    # PRD 04 notifications copy.
    "mission.streak.milestone": NotifSpec(
        _audience("mission.streak.milestone"),
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
    "assessment.completed": _s(
        _audience("assessment.completed"), "A startup assessment was completed"
    ),
    "workspace.member.joined": _s(_existing_members, "A new member joined your workspace"),
    "mission.ready": NotifSpec(
        _audience("mission.ready"),
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
        _audience("validation.smoke_test.milestone", minus_actor=False),
        lambda p: f"{p.get('name') or 'Your smoke test'} just passed {p.get('signups')} signups",
        lambda _p: "",
    ),
    # PRD 09 Insight Synthesizer notification copy, CTA "See insight".
    "validation.insight.detected": NotifSpec(
        _audience("validation.insight.detected", minus_actor=False),
        lambda p: f"New pattern detected in customer feedback: {p.get('theme') or 'a new theme'}",
        lambda _p: "See insight",
    ),
    # Journal nudges (PRD 21.4 + design): generic copy, never any entry content. In-app only.
    "journal.reminder": NotifSpec(
        _journal_owner,
        lambda _p: "A quiet moment for your journal",
        lambda _p: "You have not written today. Two minutes is enough.",
    ),
    "journal.prompt.daily": NotifSpec(
        _journal_owner,
        lambda _p: "Today's journal prompt is ready",
        lambda _p: "",
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
    # D-ASM-1: the founder owns re-assessment. CTA "Start re-assessment"; the payload carries
    # `href` (/assessment) from the scheduler handler.
    "assessment.quarterly.due": _s(
        _audience("assessment.quarterly.due", minus_actor=False),
        "Time for your quarterly startup assessment",
        "Start re-assessment",
    ),
    # PRD 08 notifications copy, CTA "Open it".
    "business.plan.generated": _s(
        _all_active_members, "Your AI-generated business plan is ready", "Open it"
    ),
    # Finance hub readers only (founder, team member, accountant).
    "finance.runway.low": _s(
        _audience("finance.runway.low"),
        "Runway below 6 months, review recommended",
        "Open Finance",
    ),
    "finance.runway.updated": NotifSpec(
        _audience("finance.runway.updated", minus_actor=False),
        lambda p: (
            f"Your runway is now {float(p['runway_months']):.1f} months"
            if p.get("runway_months") is not None
            else "Your runway is healthy"
        ),
        lambda _p: "Open Finance",
    ),
    # System event (a scheduler scan), so there is no actor to exclude.
    "finance.invoice.overdue": NotifSpec(
        _audience("finance.invoice.overdue", minus_actor=False),
        lambda p: (
            f"Invoice overdue: {p.get('number')} \u00b7 {p.get('client_name')} \u00b7 "
            f"{format_money(int(p.get('amount_minor') or 0), str(p.get('currency') or 'NGN'))}"
        ),
        lambda _p: "Send reminder",
    ),
    "finance.invoice.paid": NotifSpec(
        _audience("finance.invoice.paid", minus_actor=False),
        lambda p: f"Invoice paid: {p.get('number')} 🎉",
        lambda _p: "View invoice",
    ),
    # PRD 02.1. In-app only (no email category): it's a daily nudge, not something to mail.
    # Only BRIEFING_ROLES see the briefing prose (it is built from finance figures).
    "dashboard.briefing.ready": _s(
        _audience("dashboard.briefing.ready"), "Your daily AI briefing is ready"
    ),
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
        _audience("marketing.post.published"),
        lambda p: (
            f"Scheduled post published: {p.get('title', 'a post')}"
            if p.get("was_scheduled", True)
            else f"Post published: {p.get('title', 'a post')}"
        ),
        lambda _p: "See results",  # CTA; the deep link is the payload's `href`
    ),
    "marketing.campaign.launched": NotifSpec(
        _audience("marketing.campaign.launched"),
        lambda p: f"Campaign launched: {p.get('name', 'a campaign')}",
        lambda _p: "See results",
    ),
    # PRD 10 "Campaign performance summary ready", CTA "See results" (the payload's `href`).
    "marketing.campaign.readout.ready": NotifSpec(
        _audience("marketing.campaign.readout.ready"),
        lambda p: f"Campaign performance summary ready: {p.get('name', 'a campaign')}",
        lambda _p: "See results",
    ),
    "marketing.campaign.completed": NotifSpec(
        _audience("marketing.campaign.completed"),
        lambda p: f"Campaign completed: {p.get('name', 'a campaign')}",
        lambda _p: "See results",
    ),
    # PRD 11 / D-SAL-7 (copy avoids dashes). The stalled payload carries `href` for the follow-up.
    "sales.deal.won": NotifSpec(
        _deal_won_once_a_day,
        lambda p: f"Deal moved to Closed Won: {p.get('name', 'a deal')} 🎉",
        lambda _p: "",
    ),
    # PRD 17 notifications copy: CTA "Download it" / "Start course".
    "learning.course.completed": NotifSpec(
        _learner("learning.course.completed"),
        lambda p: f"Certificate earned: {p.get('course_title') or 'your course'} \U0001f393",
        lambda _p: "Download it",
    ),
    "learning.course.recommended": NotifSpec(
        _learner("learning.course.recommended"),
        lambda p: (
            f"New course recommended for your stage: {p['course_title']}"
            if p.get("course_title")
            else "New course recommended for your stage"
        ),
        lambda _p: "Start course",
    ),
    "sales.deal.stalled": NotifSpec(
        _deal_stalled_recipients,
        lambda p: (
            f"Deal stalled: {p.get('name', 'a deal')} has had no activity for "
            f"{p.get('days', 0)} days"
        ),
        lambda _p: "Suggest a follow-up",
    ),
    # D-SAL-8: auto-pause after a bounce spike. Founders only; the payload `href` opens Sequences.
    "sales.sending.paused": NotifSpec(
        _audience("sales.sending.paused", minus_actor=False),
        lambda p: f"Sales emails paused: too many {p.get('reason', 'bounces')}",
        lambda _p: "Review",
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
