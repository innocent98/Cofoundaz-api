"""D-MIS-8 follow-through: each notification reaches only roles that can open what it links to.

`READ_ROLES` is the read gate of the feature the notification opens. Every spec in
`registry.SPECS` is listed, so adding one without deciding its audience fails the coverage test.
"""

from datetime import UTC, datetime

import pytest

from app.db.models.enums import MembershipRole
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.registry import SPECS, register
from tests.factories import create_membership, create_startup, create_user

ALL = frozenset(MembershipRole)
CORE = frozenset({MembershipRole.founder, MembershipRole.team_member})
FOUNDER = frozenset({MembershipRole.founder})
FINANCE = frozenset({*CORE, MembershipRole.accountant})

READ_ROLES: dict[str, frozenset[MembershipRole]] = {
    # Dashboard briefing + Mission (D-MIS-8): founder + team member only
    "dashboard.briefing.ready": CORE,
    "mission.ready": CORE,
    "mission.completed": CORE,
    "mission.streak.milestone": CORE,
    # Finance hub: founder, team member, accountant
    "finance.runway.low": FINANCE,
    "finance.runway.updated": FINANCE,
    "finance.invoice.overdue": FINANCE,
    "finance.invoice.paid": FINANCE,
    # Marketing hub: founder + team member
    "marketing.post.published": CORE,
    "marketing.campaign.launched": CORE,
    "marketing.campaign.completed": CORE,
    "marketing.campaign.readout.ready": CORE,
    # Sales hub: founder + team member
    "sales.deal.won": CORE,
    "sales.sending.paused": FOUNDER,  # bounce auto-pause: founders resume it
    # Assessment / Validation / KPI strip (earlier PRs, kept honest here)
    "assessment.completed": CORE,
    "assessment.quarterly.due": FOUNDER,
    "validation.smoke_test.milestone": CORE,
    "validation.insight.detected": CORE,
    "kpi.threshold.crossed": CORE,  # the test payload's audience is marketing
    "business.suggestion.created": FOUNDER,
    # Pages every member can open (Health, Roadmap, Business Builder, Team)
    "healthscore.dropped": ALL,
    "healthscore.record": ALL,
    "roadmap.replanned": ALL,
    "roadmap.milestone.completed": ALL,
    "roadmap.milestone.overdue": ALL,
    "roadmap.milestone.due_soon": ALL,
    "business.suggestion.approved": ALL,
    "business.suggestion.rejected": ALL,
    "business.artifact.completed": ALL,
    "business.plan.generated": ALL,
    "workspace.member.joined": ALL,
}
# Addressed to named users, not to a whole role audience (stalled: the deal owner, else the
# founders; always inside the Sales hub audience, covered in test_sales_notifications.py).
DIRECT = {
    "team.comment.mentioned",
    "sales.deal.stalled",
    "learning.course.completed",  # the learner, covered in test_learning_notifications.py
    "learning.course.recommended",
    # Documents (D-DOC-1): the owner or sender, never the workspace; see test_document_notifications.py
    "document.shared",
    "document.signature.requested",
    "document.signature.signed",
    "document.signature.completed",
    # Exact recipients (owner, members holding a grant); see test_document_notifications.py
    "document.version.created",
    "document.grant.created",
}


def _team(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    users = {}
    for role in MembershipRole:
        u = owner if role == MembershipRole.founder else create_user(db)
        create_membership(db, u, s, role=role)
        users[role] = u
    db.flush()
    return s, users


def _count(db, user, event):
    return db.query(Notification).filter_by(user_id=user.id, type=event).count()


def test_every_spec_has_a_declared_audience():
    assert set(SPECS) == set(READ_ROLES) | DIRECT


@pytest.mark.parametrize("event", sorted(READ_ROLES))
def test_recipients_are_exactly_the_roles_that_can_open_the_feature(db, event):
    register()
    s, users = _team(db)
    # The actor is someone outside the team, so "minus actor" never hides a role from this check.
    payload = {
        "startup_id": str(s.id),
        "user_id": str(create_user(db).id),
        "audience": "marketing",
    }
    event_bus.publish(db, event, payload)
    got = {role for role, u in users.items() if _count(db, u, event) == 1}
    assert got == set(READ_ROLES[event]), event


def test_actor_is_still_excluded_from_mission_completed(db):
    register()
    s, users = _team(db)
    actor = users[MembershipRole.founder]
    event_bus.publish(db, "mission.completed", {"startup_id": str(s.id), "actor_id": str(actor.id)})
    assert _count(db, actor, "mission.completed") == 0
    assert _count(db, users[MembershipRole.team_member], "mission.completed") == 1
