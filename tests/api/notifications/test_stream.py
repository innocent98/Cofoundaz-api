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
                None,  # timeout -> heartbeat
            ]

        async def get_message(self, ignore_subscribe_messages=True, timeout=None):
            return self._msgs.pop(0) if self._msgs else None

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_sub(channel):
        yield FakePubSub()

    monkeypatch.setattr(notif_ep.realtime, "subscription", fake_sub)

    class FakeReq:
        def __init__(self):
            self.n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 2  # allow two loop iterations then disconnect

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
