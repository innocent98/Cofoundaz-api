"""Row 4: Documents notifications go to the owner or sender, never the whole workspace."""

from datetime import UTC, datetime

import pytest

from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.membership import Membership
from app.db.models.notification import Notification
from app.platform.events import event_bus
from app.services.notifications.registry import register
from tests.factories import create_membership, create_startup, create_user


def _team(db):
    founder = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=founder)
    users = {}
    for role in MembershipRole:
        u = founder if role == MembershipRole.founder else create_user(db)
        create_membership(db, u, s, role=role)
        users[role] = u
    db.flush()
    return s, users


def _who(db, event):
    rows = db.query(Notification).filter_by(type=event).all()
    return {r.user_id for r in rows}


@pytest.mark.parametrize("event", ["document.signature.signed", "document.signature.completed"])
def test_signed_and_completed_go_only_to_the_sender(db, event):
    register()
    s, users = _team(db)
    tm = users[MembershipRole.team_member]
    event_bus.publish(db, event, {"startup_id": str(s.id), "owner_id": str(tm.id)})
    assert _who(db, event) == {tm.id}  # not the founder, not the mentor/consultant/investor


def test_requested_notifies_nobody_in_app(db):
    register()
    s, users = _team(db)
    founder = users[MembershipRole.founder]
    event_bus.publish(
        db,
        "document.signature.requested",
        {"startup_id": str(s.id), "created_by": str(founder.id), "request_id": "r"},
    )
    assert db.query(Notification).count() == 0


def test_shared_goes_to_the_owner_but_not_to_the_sharer(db):
    register()
    s, users = _team(db)
    founder, tm = users[MembershipRole.founder], users[MembershipRole.team_member]
    event_bus.publish(
        db,
        "document.shared",
        {"startup_id": str(s.id), "shared_by_id": str(tm.id), "owner_id": str(founder.id)},
    )
    assert _who(db, "document.shared") == {founder.id}
    db.query(Notification).delete()
    event_bus.publish(  # the owner sharing their own document tells nobody
        db,
        "document.shared",
        {"startup_id": str(s.id), "shared_by_id": str(founder.id), "owner_id": str(founder.id)},
    )
    assert db.query(Notification).count() == 0


def test_owner_who_left_falls_back_to_the_founders_never_other_roles(db):
    register()
    s, users = _team(db)
    tm = users[MembershipRole.team_member]
    db.query(Membership).filter_by(user_id=tm.id, startup_id=s.id).update(
        {"status": MembershipStatus.removed}
    )
    db.flush()
    event_bus.publish(
        db, "document.signature.completed", {"startup_id": str(s.id), "owner_id": str(tm.id)}
    )
    assert _who(db, "document.signature.completed") == {users[MembershipRole.founder].id}


@pytest.mark.parametrize("owner", [None, "not-a-uuid"])
def test_missing_or_garbage_owner_falls_back_to_the_founders(db, owner):
    register()
    s, users = _team(db)
    event_bus.publish(db, "document.signature.signed", {"startup_id": str(s.id), "owner_id": owner})
    assert _who(db, "document.signature.signed") == {users[MembershipRole.founder].id}


def test_mentor_cannot_read_a_stored_legacy_document_row(db):
    """Rows delivered before this change are hidden on read by the audience map."""
    from app.services.notifications.service import create_notifications, list_notifications

    s, users = _team(db)
    mentor = users[MembershipRole.mentor]
    create_notifications(
        db,
        user_ids=[mentor.id],
        startup_id=s.id,
        type="document.signature.signed",
        title="legacy",
        body="",
        data={},
    )
    rows, _ = list_notifications(
        db,
        user_id=mentor.id,
        startup_id=s.id,
        role=MembershipRole.mentor,
        unread=False,
        limit=10,
        cursor=None,
    )
    assert rows == []
