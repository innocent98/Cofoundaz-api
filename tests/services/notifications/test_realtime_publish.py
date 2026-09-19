import app.platform.realtime as rt
from app.db.session import SessionLocal
from app.services.notifications.service import create_notifications


def _make_pair(db):
    from tests.factories import create_membership, create_startup, create_user

    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    return u, s


def test_publish_on_commit_one_per_recipient(monkeypatch):
    published = []
    monkeypatch.setattr(
        rt, "publish_notification", lambda sid, uid, payload: published.append((sid, uid, payload))
    )
    db = SessionLocal()
    try:
        u, s = _make_pair(db)
        db.commit()  # commit the fixtures first so the next commit only carries the notification
        published.clear()
        create_notifications(
            db, user_ids=[u.id], startup_id=s.id, type="x.test", title="T", body="", data={"k": "v"}
        )
        assert published == []  # nothing published before commit
        db.commit()
        assert len(published) == 1
        sid, uid, payload = published[0]
        assert str(sid) == str(s.id) and str(uid) == str(u.id)
        assert payload["event"] == "notification.created"
        assert payload["notification"]["type"] == "x.test"
    finally:
        db.rollback()
        db.close()


def test_no_publish_on_rollback(monkeypatch):
    published = []
    monkeypatch.setattr(rt, "publish_notification", lambda *a, **k: published.append(a))
    db = SessionLocal()
    try:
        u, s = _make_pair(db)
        db.commit()
        published.clear()
        create_notifications(
            db, user_ids=[u.id], startup_id=s.id, type="x.test", title="T", body="", data={}
        )
        db.rollback()
        assert published == []
    finally:
        db.close()
