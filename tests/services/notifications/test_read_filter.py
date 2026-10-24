"""Read-path audience filter: rows stored before the fan-out audience fixes (#150/#152/#153)
must not surface to a role the type is no longer delivered to."""

from datetime import UTC, datetime

import pytest

from app.db.models.enums import (
    FINANCE_ROLES,
    MARKETING_ROLES,
    MembershipRole,
)
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.audience import AUDIENCE, audience_roles, audience_visible
from app.services.notifications.registry import SPECS, register
from app.services.notifications.service import (
    create_notifications,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)
from tests.factories import create_membership, create_startup, create_user

CORE = {MembershipRole.founder, MembershipRole.team_member}
# Recipients are a payload-driven subset of the audience (stalled: owner, else founders).
NARROWER_THAN_AUDIENCE = {
    "sales.deal.stalled",
    "learning.course.completed",  # only the learner named in the payload
    "learning.course.recommended",
    "document.shared",  # the document owner (D-DOC-1), see test_document_notifications.py
    "document.signature.requested",  # nobody in-app: the signers get the email
    "document.signature.signed",
    "document.signature.completed",
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


def _seed(db, s, user, type_, **data):
    return create_notifications(
        db, user_ids=[user.id], startup_id=s.id, type=type_, title="t", body="", data=data
    )[0]


@pytest.mark.parametrize(
    "type_", ["mission.ready", "dashboard.briefing.ready", "assessment.completed"]
)
def test_legacy_row_hidden_from_mentor_everywhere(db, type_):
    s, users = _team(db)
    mentor = users[MembershipRole.mentor]
    row = _seed(db, s, mentor, type_)
    role = MembershipRole.mentor
    kw = {"user_id": mentor.id, "startup_id": s.id, "role": role}
    items, cur = list_notifications(db, unread=False, limit=20, cursor=None, **kw)
    assert items == [] and cur is None
    assert unread_count(db, **kw) == 0
    assert mark_all_read(db, **kw) == 0
    assert row.read_at is None  # a read filter only: nothing was touched
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        mark_read(db, notification_id=row.id, **kw)


def test_founder_still_sees_legacy_mission_ready(db):
    s, users = _team(db)
    f = users[MembershipRole.founder]
    _seed(db, s, f, "mission.ready")
    kw = {"user_id": f.id, "startup_id": s.id, "role": MembershipRole.founder}
    assert unread_count(db, **kw) == 1
    assert len(list_notifications(db, unread=False, limit=20, cursor=None, **kw)[0]) == 1


def test_role_change_refilters(db):
    s, users = _team(db)
    u = users[MembershipRole.mentor]
    _seed(db, s, u, "mission.ready")
    kw = {"user_id": u.id, "startup_id": s.id}
    assert unread_count(db, role=MembershipRole.mentor, **kw) == 0
    assert unread_count(db, role=MembershipRole.team_member, **kw) == 1


def test_pagination_counts_only_visible_rows(db):
    s, users = _team(db)
    m = users[MembershipRole.mentor]
    for _ in range(3):
        _seed(db, s, m, "mission.ready")
    visible = [_seed(db, s, m, "roadmap.replanned") for _ in range(3)]
    kw = {"user_id": m.id, "startup_id": s.id, "role": MembershipRole.mentor}
    page1, cur = list_notifications(db, unread=False, limit=2, cursor=None, **kw)
    page2, cur2 = list_notifications(db, unread=False, limit=2, cursor=cur, **kw)
    assert len(page1) == 2 and len(page2) == 1 and cur2 is None
    assert {n.id for n in page1 + page2} == {n.id for n in visible}


def test_untyped_rule_stays_visible_to_every_role(db):
    s, users = _team(db)
    for role, u in users.items():
        _seed(db, s, u, "totally.unknown.type")
        _seed(db, s, u, "healthscore.dropped")
        assert unread_count(db, user_id=u.id, startup_id=s.id, role=role) == 2


def test_kpi_threshold_audience_follows_payload(db):
    s, users = _team(db)
    for role in (MembershipRole.accountant, MembershipRole.mentor, MembershipRole.team_member):
        u = users[role]
        _seed(db, s, u, "kpi.threshold.crossed", audience="finance")
        _seed(db, s, u, "kpi.threshold.crossed", audience="marketing")
        want = (role in FINANCE_ROLES) + (role in MARKETING_ROLES)
        assert unread_count(db, user_id=u.id, startup_id=s.id, role=role) == want, role


def test_kpi_python_twin_matches_sql(db):
    assert audience_visible(
        MembershipRole.accountant, "kpi.threshold.crossed", {"audience": "finance"}
    )
    assert not audience_visible(
        MembershipRole.accountant, "kpi.threshold.crossed", {"audience": "marketing"}
    )


def test_every_audience_rule_matches_actual_fan_out(db):
    """Single-source guard: for every type with an audience rule, the recipients fan-out
    picks are exactly the members the read filter lets see it (no actor in the payload)."""
    register()
    s, users = _team(db)
    for type_, spec in SPECS.items():
        if type_ not in AUDIENCE:
            continue
        payload = {"startup_id": str(s.id)}
        got = set(spec.recipients(db, payload))
        want = {users[r].id for r in audience_roles(type_, payload)}
        if type_ in NARROWER_THAN_AUDIENCE:
            assert got <= want, type_
        else:
            assert got == want, type_


def test_audience_rows_are_real_registry_types():
    assert set(AUDIENCE) <= set(SPECS)


def test_registry_publish_then_read_matches(db):
    register()
    s, users = _team(db)
    event_bus.publish(db, "mission.ready", {"startup_id": str(s.id)})
    for role, u in users.items():
        n = unread_count(db, user_id=u.id, startup_id=s.id, role=role)
        assert n == (1 if role in CORE else 0), role
        assert db.query(Notification).filter_by(user_id=u.id).count() == n
