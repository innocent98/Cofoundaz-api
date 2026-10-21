from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.db.models.membership import Membership
from app.db.models.notification import Notification
from app.services.notifications.service import create_notifications
from tests.factories import create_membership, create_startup, create_user


def _ws(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    mentor = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    db.flush()

    def headers(u):
        return {
            "Authorization": f"Bearer {create_access_token(str(u.id))}",
            "X-Workspace-Id": str(s.id),
        }

    return s, owner, mentor, headers


def _legacy(db, s, user, type_="mission.ready"):
    return create_notifications(
        db, user_ids=[user.id], startup_id=s.id, type=type_, title="legacy", body="", data={}
    )[0]


def test_mentor_does_not_see_legacy_mission_row(db, client):
    s, owner, mentor, hdr = _ws(db)
    row = _legacy(db, s, mentor)
    _legacy(db, s, mentor, "document.shared")
    db.commit()
    h = hdr(mentor)
    r = client.get("/api/v1/notifications", headers=h)
    assert [n["type"] for n in r.json()["data"]["notifications"]] == ["document.shared"]
    assert client.get("/api/v1/notifications/unread-count", headers=h).json()["data"]["unread"] == 1
    assert client.post(f"/api/v1/notifications/{row.id}/read", headers=h).status_code == 404
    assert client.post("/api/v1/notifications/read-all", headers=h).json()["data"]["marked"] == 1
    db.expire_all()
    assert db.get(Notification, row.id).read_at is None


def test_founder_still_sees_legacy_mission_row(db, client):
    s, owner, mentor, hdr = _ws(db)
    _legacy(db, s, owner)
    db.commit()
    h = hdr(owner)
    r = client.get("/api/v1/notifications", headers=h)
    assert [n["type"] for n in r.json()["data"]["notifications"]] == ["mission.ready"]
    assert client.get("/api/v1/notifications/unread-count", headers=h).json()["data"]["unread"] == 1


def test_role_change_refilters(db, client):
    s, owner, mentor, hdr = _ws(db)
    _legacy(db, s, mentor)
    db.commit()
    h = hdr(mentor)
    assert client.get("/api/v1/notifications/unread-count", headers=h).json()["data"]["unread"] == 0
    m = db.query(Membership).filter_by(user_id=mentor.id, startup_id=s.id).one()
    m.role = MembershipRole.team_member
    db.commit()
    assert client.get("/api/v1/notifications/unread-count", headers=h).json()["data"]["unread"] == 1
