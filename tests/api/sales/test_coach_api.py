import uuid
from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/sales"
OTHER_ROLES = [MembershipRole.mentor, MembershipRole.business_consultant]
_ID = str(uuid.uuid4())
ROUTES = [
    ("POST", "/coach/sessions", {"scenario": "x", "persona": "CFO"}),
    ("GET", "/coach/sessions", None),
    ("GET", f"/coach/sessions/{_ID}", None),
    ("POST", f"/coach/sessions/{_ID}/messages", {"content": "hi"}),
    ("POST", f"/coach/sessions/{_ID}/end", None),
    ("POST", f"/deals/{_ID}/call-prep", None),
    ("GET", f"/deals/{_ID}/call-prep", None),
    ("GET", "/analytics", None),
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def _deal(client, h, name="Acme"):
    r = client.post(f"{BASE}/deals", json={"name": name, "value_minor": 100_000}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


@pytest.mark.parametrize("role", OTHER_ROLES)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_mentor_and_consultant_are_forbidden(client, db, role, method, path, body):
    _f, startup, _h = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    r = client.request(method, BASE + path, json=body, headers=h)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_unauthenticated_is_401(client, method, path, body):
    assert client.request(method, BASE + path, json=body).status_code == 401


def test_roleplay_protocol_202_poll_409_and_end(client, db):
    _u, _s, h = _member(db)
    deal_id = _deal(client, h)
    r = client.post(
        f"{BASE}/coach/sessions",
        json={"deal_id": deal_id, "persona": "Skeptical CFO", "difficulty": "tough"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    s = r.json()["data"]
    assert s["status"] == "active" and s["deal_name"] == "Acme" and s["messages"] == []
    sid = s["id"]

    m = client.post(f"{BASE}/coach/sessions/{sid}/messages", json={"content": "Hello"}, headers=h)
    assert m.status_code == 202, m.text
    assert m.json()["data"]["status"] == "generating" and m.json()["data"]["role"] == "assistant"

    again = client.post(f"{BASE}/coach/sessions/{sid}/messages", json={"content": "?"}, headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "REPLY_IN_PROGRESS"
    early_end = client.post(f"{BASE}/coach/sessions/{sid}/end", headers=h)
    assert early_end.status_code == 409

    got = client.get(f"{BASE}/coach/sessions/{sid}", headers=h).json()["data"]
    assert [x["role"] for x in got["messages"]] == ["user", "assistant"]
    listed = client.get(f"{BASE}/coach/sessions", headers=h).json()["data"]
    assert [x["id"] for x in listed] == [sid] and "messages" not in listed[0]


def test_end_with_nothing_said_is_422(client, db):
    _u, _s, h = _member(db)
    sid = client.post(
        f"{BASE}/coach/sessions", json={"scenario": "Cold call", "persona": "CFO"}, headers=h
    ).json()["data"]["id"]
    r = client.post(f"{BASE}/coach/sessions/{sid}/end", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOTHING_TO_SCORE"


@pytest.mark.parametrize(
    "body",
    [
        {"persona": "CFO"},
        {"scenario": "x"},
        {"scenario": "x", "persona": "CFO", "difficulty": "brutal"},
        {"scenario": "x", "persona": "CFO", "message": "m" * 4001},
    ],
)
def test_setup_validation(client, db, body):
    _u, _s, h = _member(db)
    assert client.post(f"{BASE}/coach/sessions", json=body, headers=h).status_code == 422


def test_cross_workspace_deal_is_rejected_everywhere(client, db):
    _u1, _s1, h1 = _member(db)
    _u2, _s2, h2 = _member(db)
    foreign = _deal(client, h2)
    r = client.post(
        f"{BASE}/coach/sessions", json={"deal_id": foreign, "persona": "CFO"}, headers=h1
    )
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "deal_id"
    assert client.post(f"{BASE}/deals/{foreign}/call-prep", headers=h1).status_code == 404
    assert client.get(f"{BASE}/deals/{foreign}/call-prep", headers=h1).status_code == 404
    # nothing was queued for the foreign deal
    assert client.get(f"{BASE}/deals/{foreign}/call-prep", headers=h2).json()["data"] is None


def test_sessions_are_private_between_teammates(client, db):
    _u, startup, h = _member(db)
    _t, _s, th = _member(db, role=MembershipRole.team_member, startup=startup)
    sid = client.post(
        f"{BASE}/coach/sessions", json={"scenario": "x", "persona": "CFO"}, headers=h
    ).json()["data"]["id"]
    for method, path, body in (
        ("GET", f"/coach/sessions/{sid}", None),
        ("POST", f"/coach/sessions/{sid}/messages", {"content": "hi"}),
        ("POST", f"/coach/sessions/{sid}/end", None),
    ):
        assert client.request(method, BASE + path, json=body, headers=th).status_code == 404
    assert client.get(f"{BASE}/coach/sessions", headers=th).json()["data"] == []


def test_call_prep_api_flow(client, db):
    _u, _s, h = _member(db)
    deal = _deal(client, h)
    assert client.get(f"{BASE}/deals/{deal}/call-prep", headers=h).json()["data"] is None
    r = client.post(f"{BASE}/deals/{deal}/call-prep", headers=h)
    assert r.status_code == 202, r.text
    assert r.json()["data"]["status"] == "generating" and r.json()["data"]["output"] is None
    dup = client.post(f"{BASE}/deals/{deal}/call-prep", headers=h)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "CALL_PREP_IN_PROGRESS"
    latest = client.get(f"{BASE}/deals/{deal}/call-prep", headers=h).json()["data"]
    assert latest["id"] == r.json()["data"]["id"] and latest["deal_id"] == deal


def test_analytics_api(client, db):
    _u, _s, h = _member(db)
    deal = _deal(client, h)
    stages = {s["name"]: s["id"] for s in client.get(f"{BASE}/stages", headers=h).json()["data"]}
    client.post(f"{BASE}/deals/{deal}/stage", json={"stage_id": stages["Closed Won"]}, headers=h)
    r = client.get(f"{BASE}/analytics", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert set(d) == {
        "from",
        "to",
        "currency",
        "funnel",
        "avg_cycle_days",
        "won",
        "win_loss",
        "leaderboard",
        "monthly_revenue",
    }
    assert d["won"] == {"count": 1, "value_minor": 100_000}
    assert d["funnel"]["deals_created"] == 1
    assert d["monthly_revenue"][-1]["value_minor"] == 100_000
    ranged = client.get(
        f"{BASE}/analytics", params={"from": "2020-01-01", "to": "2020-01-31"}, headers=h
    ).json()["data"]
    assert ranged["from"] == "2020-01-01" and ranged["won"]["count"] == 0
    for params in ({"from": "2026-02-01", "to": "2026-01-01"}, {"from": "nope"}):
        assert client.get(f"{BASE}/analytics", params=params, headers=h).status_code == 422
