import asyncio
import json
from contextlib import asynccontextmanager

import pytest

import app.api.v1.endpoints.notifications as notif_ep
from app.db.models.enums import FINANCE_ROLES, MARKETING_ROLES, MISSION_ROLES, MembershipRole


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
    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid, role: 3)
    monkeypatch.setattr(notif_ep, "_membership_role", lambda uid, sid: MembershipRole.founder)

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

    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid, role: 0)
    checks = []

    def still_member(uid, sid):
        checks.append(1)
        # active at connect and on the first re-check, revoked on the second
        return MembershipRole.founder if len(checks) < 3 else None

    monkeypatch.setattr(notif_ep, "_membership_role", still_member)
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


_EVENTS = [
    ("mission.task.added", "mission_task"),
    ("finance.transaction.created", "transaction"),
    ("marketing.campaign.launched", "campaign"),
    ("roadmap.milestone.completed", "roadmap_milestone"),
]


def _stream_actions(monkeypatch, role_lookup, n_loops):
    """Run the stream over one activity.created per _EVENTS entry; return delivered actions."""
    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid, role: 0)
    monkeypatch.setattr(notif_ep, "_membership_role", role_lookup)

    msgs = [
        {
            "type": "message",
            "data": json.dumps(
                {
                    "event": "activity.created",
                    "item": {"id": a, "action": a, "entity_type": e, "summary": a},
                }
            ),
        }
        for a, e in _EVENTS
    ]

    class FakePubSub:
        async def get_message(self, ignore_subscribe_messages=True, timeout=None):
            return msgs.pop(0) if msgs else None

    @asynccontextmanager
    async def fake_sub(*channels):
        yield FakePubSub()

    monkeypatch.setattr(notif_ep.realtime, "subscription", fake_sub)

    class FakeReq:
        def __init__(self):
            self.n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > n_loops

    async def collect():
        return [f async for f in notif_ep._event_stream(FakeReq(), "s1", "u1")]

    delivered = []
    for frame in asyncio.run(collect()):
        if frame.startswith("event: activity.created"):
            delivered.append(json.loads(frame.split("data: ", 1)[1])["action"])
    return delivered


@pytest.mark.parametrize("role", list(MembershipRole))
def test_event_stream_filters_activity_by_recipient_role(monkeypatch, role):
    delivered = _stream_actions(monkeypatch, lambda uid, sid: role, len(_EVENTS))
    assert ("mission.task.added" in delivered) is (role in MISSION_ROLES)
    assert ("finance.transaction.created" in delivered) is (role in FINANCE_ROLES)
    assert ("marketing.campaign.launched" in delivered) is (role in MARKETING_ROLES)
    assert "roadmap.milestone.completed" in delivered


def test_event_stream_picks_up_a_role_change_on_recheck(monkeypatch):
    """A team member demoted to mentor mid-stream stops getting mission events at the next
    membership re-check, not only after reconnecting."""
    monkeypatch.setattr(notif_ep.settings, "SSE_HEARTBEAT_INTERVAL", 0)
    roles = iter([MembershipRole.team_member, MembershipRole.mentor])
    current = {"role": MembershipRole.team_member}

    def lookup(uid, sid):
        current["role"] = next(roles, current["role"])
        return current["role"]

    delivered = _stream_actions(monkeypatch, lookup, len(_EVENTS))
    assert "mission.task.added" not in delivered
    assert "roadmap.milestone.completed" in delivered


def test_stream_endpoint_rejects_inactive_membership(client, monkeypatch):
    monkeypatch.setattr(notif_ep, "consume_stream_ticket", lambda t: ("u1", "s1"))
    monkeypatch.setattr(notif_ep, "_membership_role", lambda uid, sid: None)
    r = client.get("/api/v1/notifications/stream?ticket=t")
    assert r.status_code == 403


def test_event_stream_drops_malformed_activity_for_non_core(monkeypatch):
    """An activity.created without an item can't be classified, so it fails closed."""
    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid, role: 0)
    monkeypatch.setattr(notif_ep, "_membership_role", lambda uid, sid: MembershipRole.mentor)
    msgs = [{"type": "message", "data": '{"event":"activity.created"}'}]

    class FakePubSub:
        async def get_message(self, ignore_subscribe_messages=True, timeout=None):
            return msgs.pop(0) if msgs else None

    @asynccontextmanager
    async def fake_sub(*channels):
        yield FakePubSub()

    monkeypatch.setattr(notif_ep.realtime, "subscription", fake_sub)

    class FakeReq:
        def __init__(self):
            self.n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 2

    async def collect():
        return [f async for f in notif_ep._event_stream(FakeReq(), "s1", "u1")]

    assert not any("activity.created" in f for f in asyncio.run(collect()))


def test_stream_initial_unread_is_computed_for_the_members_role(monkeypatch):
    seen = []
    monkeypatch.setattr(notif_ep, "_unread", lambda uid, sid, role: seen.append(role) or 0)
    monkeypatch.setattr(notif_ep, "_membership_role", lambda uid, sid: MembershipRole.mentor)

    class Req:
        async def is_disconnected(self):
            return True

    @asynccontextmanager
    async def fake_sub(*channels):
        yield object()

    monkeypatch.setattr(notif_ep.realtime, "subscription", fake_sub)

    async def collect():
        return [f async for f in notif_ep._event_stream(Req(), "s1", "u1")]

    asyncio.run(collect())
    assert seen == [MembershipRole.mentor]
