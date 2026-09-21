import pytest

import app.platform.realtime as rt
from app.core.config import settings


def test_channel_for_format():
    assert rt.channel_for("s1", "u1") == "notif:s1:u1"


def test_publish_uses_channel_and_json(monkeypatch):
    calls = []

    class FakeRedis:
        def publish(self, ch, msg):
            calls.append((ch, msg))

    monkeypatch.setattr(rt, "get_redis", lambda: FakeRedis())
    rt.publish_notification(
        "s1", "u1", {"event": "notification.created", "notification": {"id": "n1"}}
    )
    assert calls[0][0] == "notif:s1:u1"
    assert '"id": "n1"' in calls[0][1]


def test_publish_is_fail_soft(monkeypatch):
    class BoomRedis:
        def publish(self, ch, msg):
            raise RuntimeError("redis down")

    monkeypatch.setattr(rt, "get_redis", lambda: BoomRedis())
    # must NOT raise
    rt.publish_notification("s1", "u1", {"x": 1})


def test_ticket_mint_and_consume_is_one_time(monkeypatch):
    store: dict[str, str] = {}

    class FakeRedis:
        def set(self, k, v, ex=None, nx=None):
            store[k] = v

        def getdel(self, k):
            return store.pop(k, None)

    monkeypatch.setattr(rt, "get_redis", lambda: FakeRedis())
    tok = rt.mint_stream_ticket("u1", "s1")
    assert rt.consume_stream_ticket(tok) == ("u1", "s1")
    assert rt.consume_stream_ticket(tok) is None  # one-time


def test_consume_unknown_ticket_returns_none(monkeypatch):
    monkeypatch.setattr(rt, "get_redis", lambda: type("R", (), {"getdel": lambda self, k: None})())
    assert rt.consume_stream_ticket("nope") is None


def test_settings_defaults():
    assert settings.SSE_TICKET_TTL == 30
    assert settings.SSE_HEARTBEAT_INTERVAL == 20


async def test_subscription_closes_client_on_subscribe_failure(monkeypatch):
    """If Redis is down at connect time, `subscribe()` raises before yielding — the
    client/pubsub must still be closed rather than leaked (one per failed EventSource
    reconnect during an outage)."""
    import redis.asyncio as aioredis

    closed = {"pubsub": False, "client": False}

    class FakePubSub:
        async def subscribe(self, channel):
            raise RuntimeError("redis down")

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            closed["pubsub"] = True

    class FakeClient:
        def pubsub(self):
            return FakePubSub()

        async def aclose(self):
            closed["client"] = True

    monkeypatch.setattr(aioredis, "from_url", lambda *args, **kwargs: FakeClient())

    with pytest.raises(RuntimeError, match="redis down"):
        async with rt.subscription("notif:s1:u1"):
            pass  # pragma: no cover - subscribe() raises before yield

    assert closed["pubsub"] is True
    assert closed["client"] is True
