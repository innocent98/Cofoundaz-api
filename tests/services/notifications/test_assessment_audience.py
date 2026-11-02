"""D-ASM-1 follow-through: assessment notifications only reach roles that can open /assessment."""

from datetime import UTC, datetime

from app.db.models.enums import MembershipRole
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.registry import register
from tests.factories import create_membership, create_startup, create_user


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


def _got(db, user, type_):
    return db.query(Notification).filter_by(user_id=user.id, type=type_).count()


def test_completed_goes_to_founders_and_team_members_minus_actor(db):
    register()
    s, users = _team(db)
    actor = users[MembershipRole.founder]
    event_bus.publish(
        db, "assessment.completed", {"startup_id": str(s.id), "actor_id": str(actor.id)}
    )
    assert _got(db, users[MembershipRole.team_member], "assessment.completed") == 1
    assert _got(db, actor, "assessment.completed") == 0
    for role in (
        MembershipRole.mentor,
        MembershipRole.accountant,
        MembershipRole.legal_advisor,
        MembershipRole.business_consultant,
        MembershipRole.investor,
    ):
        assert _got(db, users[role], "assessment.completed") == 0, role


def test_completed_reaches_a_second_founder(db):
    register()
    s, users = _team(db)
    co = create_user(db)
    create_membership(db, co, s, role=MembershipRole.founder)
    db.flush()
    event_bus.publish(
        db,
        "assessment.completed",
        {"startup_id": str(s.id), "actor_id": str(users[MembershipRole.founder].id)},
    )
    assert _got(db, co, "assessment.completed") == 1


def test_quarterly_due_goes_to_founders_only_with_cta_copy(db):
    register()
    s, users = _team(db)
    event_bus.publish(db, "assessment.quarterly.due", {"startup_id": str(s.id)})
    for role, u in users.items():
        expected = 1 if role == MembershipRole.founder else 0
        assert _got(db, u, "assessment.quarterly.due") == expected, role
    n = db.query(Notification).filter_by(type="assessment.quarterly.due").one()
    assert n.title == "Time for your quarterly startup assessment"
    assert n.body == "Start re-assessment"
