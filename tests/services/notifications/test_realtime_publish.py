from app.services.notifications.service import create_notifications


class _FakeSession:
    def __init__(self, pending):
        self.info = {"pending_realtime": pending} if pending is not None else {}


def test_create_notifications_stashes_realtime_payloads(db):
    from tests.factories import create_membership, create_startup, create_user

    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    create_notifications(
        db, user_ids=[u.id], startup_id=s.id, type="x.test", title="T", body="", data={"k": "v"}
    )
    pending = db.info["pending_realtime"]
    assert len(pending) == 1
    sid, uid, payload = pending[0]
    assert str(sid) == str(s.id) and str(uid) == str(u.id)
    assert payload["event"] == "notification.created"
    assert payload["notification"]["type"] == "x.test"


def test_after_commit_publishes_and_clears(monkeypatch):
    import app.platform.realtime as rt

    published = []
    monkeypatch.setattr(
        rt, "publish_notification", lambda sid, uid, p: published.append((sid, uid, p))
    )
    from app.db.session import _publish_pending_realtime

    sess = _FakeSession(
        [("s1", "u1", {"event": "notification.created", "notification": {"id": "n1"}})]
    )
    _publish_pending_realtime(sess)
    assert published == [
        ("s1", "u1", {"event": "notification.created", "notification": {"id": "n1"}})
    ]
    assert not sess.info.get("pending_realtime")  # drained


def test_after_rollback_clears_without_publishing(monkeypatch):
    import app.platform.realtime as rt

    published = []
    monkeypatch.setattr(rt, "publish_notification", lambda *a, **k: published.append(a))
    from app.db.session import _drop_pending_realtime

    sess = _FakeSession([("s1", "u1", {})])
    _drop_pending_realtime(sess)
    assert published == []
    assert not sess.info.get("pending_realtime")
