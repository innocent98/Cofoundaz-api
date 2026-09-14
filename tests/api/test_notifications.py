from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from app.services.notifications.service import create_notifications
from tests.factories import create_membership, create_startup, create_user


def _member(db, *, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def test_feed_lists_only_own(db, client):
    u, s, h = _member(db)
    create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="t", title="Mine", body="", data={}
    )
    other = create_user(db)
    create_membership(db, other, s)
    create_notifications(
        db, user_ids=[other.id], startup_id=s.id, type="t", title="Theirs", body="", data={}
    )
    db.commit()
    r = client.get("/api/v1/notifications", headers=h)
    assert r.status_code == 200
    titles = [n["title"] for n in r.json()["data"]["notifications"]]
    assert titles == ["Mine"]


def test_unread_count_and_mark(db, client):
    u, s, h = _member(db)
    rows = create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="t", title="N", body="", data={}
    )
    db.commit()
    assert client.get("/api/v1/notifications/unread-count", headers=h).json()["data"]["unread"] == 1
    assert client.post(f"/api/v1/notifications/{rows[0].id}/read", headers=h).status_code == 200
    assert client.get("/api/v1/notifications/unread-count", headers=h).json()["data"]["unread"] == 0


def test_read_all(db, client):
    u, s, h = _member(db)
    create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="t", title="N", body="", data={}
    )
    db.commit()
    r = client.post("/api/v1/notifications/read-all", headers=h)
    assert r.status_code == 200 and r.json()["data"]["marked"] == 1


def test_mark_other_users_notification_404(db, client):
    u, s, h = _member(db)
    other = create_user(db)
    create_membership(db, other, s)
    rows = create_notifications(
        db, user_ids=[other.id], startup_id=s.id, type="t", title="N", body="", data={}
    )
    db.commit()
    assert client.post(f"/api/v1/notifications/{rows[0].id}/read", headers=h).status_code == 404


def test_end_to_end_event_creates_notification(db, client):
    """A handled event fired through the real bus (register() active in the app) creates a feed row."""
    from app.platform.events import event_bus

    u, s, h = _member(db)
    other = create_user(db)
    create_membership(db, other, s)
    db.commit()
    # `other` shares a document -> `u` (a member, not the actor) is notified
    event_bus.publish(db, "document.shared", {"startup_id": str(s.id), "shared_by": str(other.id)})
    db.commit()
    r = client.get("/api/v1/notifications", headers=h)
    assert any(n["type"] == "document.shared" for n in r.json()["data"]["notifications"])
