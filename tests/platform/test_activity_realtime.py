"""write_activity -> live `activity.created` on the workspace channel, only after commit."""

from app.platform import realtime
from app.platform.activity import write_activity
from tests.factories import create_startup, create_user


def test_activity_is_queued_with_the_feed_shape(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = write_activity(
        db, startup_id=s.id, actor_user_id=u.id, action="x.done", summary="Did x", meta={"k": 1}
    )
    ((startup_id, payload),) = db.info["pending_activity"]
    assert startup_id == str(s.id)
    assert payload["event"] == "activity.created"
    item = payload["item"]
    assert item["id"] == str(row.id)
    assert item["actor"]["id"] == str(u.id)
    assert (item["action"], item["summary"], item["meta"]) == ("x.done", "Did x", {"k": 1})
    assert item["created_at"]


class _FakeSession:
    def __init__(self, activity):
        self.info = {"pending_activity": activity}


def test_after_commit_publishes_activity_and_clears(monkeypatch):
    sent = []
    monkeypatch.setattr(realtime, "publish_activity", lambda sid, p: sent.append((sid, p)))
    from app.db.session import _publish_pending_realtime

    sess = _FakeSession([("s1", {"event": "activity.created", "item": {"id": "a1"}})])
    _publish_pending_realtime(sess)
    assert sent == [("s1", {"event": "activity.created", "item": {"id": "a1"}})]
    assert not sess.info.get("pending_activity")


def test_after_rollback_drops_activity_without_publishing(monkeypatch):
    sent = []
    monkeypatch.setattr(realtime, "publish_activity", lambda *a: sent.append(a))
    from app.db.session import _drop_pending_realtime

    sess = _FakeSession([("s1", {})])
    _drop_pending_realtime(sess)
    assert sent == []
    assert not sess.info.get("pending_activity")


def test_publish_activity_is_best_effort(monkeypatch):
    class _Boom:
        def publish(self, *_a):
            raise ConnectionError("redis down")

    monkeypatch.setattr(realtime, "get_redis", lambda: _Boom())
    realtime.publish_activity("s1", {"event": "activity.created"})  # must not raise
