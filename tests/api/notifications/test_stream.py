import asyncio

import app.api.v1.endpoints.notifications as notif_ep


def test_stream_ticket_requires_auth(client):
    # no auth -> 401/403 (same guard as the other notification endpoints)
    r = client.post("/api/v1/notifications/stream-ticket")
    assert r.status_code in (401, 403)


def test_stream_rejects_bad_ticket(client, monkeypatch):
    monkeypatch.setattr(notif_ep, "consume_stream_ticket", lambda t: None)
    r = client.get("/api/v1/notifications/stream?ticket=nope")
    assert r.status_code == 401


def test_event_stream_emits_unread_then_notification(monkeypatch):
    # Drive the async generator directly with fakes: no real Redis/DB.
    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid: 3)

    class FakePubSub:
        def __init__(self):
            self._msgs = [
                {
                    "type": "message",
                    "data": '{"event":"notification.created","notification":{"id":"n1","type":"x.test"}}',
                },
                {
                    "type": "message",
                    "data": '{"event":"activity.created","item":{"id":"a1","summary":"Ada did X"}}',
                },
                None,  # timeout -> heartbeat
            ]

        async def get_message(self, ignore_subscribe_messages=True, timeout=None):
            return self._msgs.pop(0) if self._msgs else None

    from contextlib import asynccontextmanager

    subscribed = []

    @asynccontextmanager
    async def fake_sub(*channels):
        subscribed.extend(channels)
        yield FakePubSub()

    monkeypatch.setattr(notif_ep.realtime, "subscription", fake_sub)

    class FakeReq:
        def __init__(self):
            self.n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 3  # allow three loop iterations then disconnect

    async def collect():
        out = []
        async for frame in notif_ep._event_stream(FakeReq(), "s1", "u1"):
            out.append(frame)
        return out

    frames = asyncio.run(collect())
    joined = "".join(frames)
    assert "event: unread" in joined and '"unread": 3' in joined
    assert "event: notification.created" in joined and '"id": "n1"' in joined
    assert ": heartbeat" in joined
    # The same stream also carries the workspace's live activity (PRD workspace.{id}.activity).
    assert subscribed == ["notif:s1:u1", "activity:s1"]
    assert "event: activity.created" in joined and '"summary": "Ada did X"' in joined


def test_event_stream_closes_when_membership_is_revoked(monkeypatch):
    """A member removed mid-stream stops receiving workspace activity (security review)."""
    from contextlib import asynccontextmanager

    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid: 0)
    checks = []

    def still_member(uid, sid):
        checks.append(1)
        return len(checks) < 2  # active on the first re-check, revoked on the second

    monkeypatch.setattr(notif_ep, "_active_membership", still_member)
    monkeypatch.setattr(notif_ep.settings, "SSE_HEARTBEAT_INTERVAL", 0)

    class FakePubSub:
        async def get_message(self, ignore_subscribe_messages=True, timeout=None):
            return {"type": "message", "data": '{"event":"activity.created","item":{"id":"a"}}'}

    @asynccontextmanager
    async def fake_sub(*channels):
        yield FakePubSub()

    monkeypatch.setattr(notif_ep.realtime, "subscription", fake_sub)

    class NeverDisconnects:
        async def is_disconnected(self):
            return False

    async def collect():
        return [f async for f in notif_ep._event_stream(NeverDisconnects(), "s1", "u1")]

    frames = asyncio.run(collect())
    assert frames[-1].startswith("event: revoked")
    assert sum("activity.created" in f for f in frames) == 1  # nothing after revocation
