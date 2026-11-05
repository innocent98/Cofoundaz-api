from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import (
    READINESS_READ_ROLES,
    READINESS_WRITE_ROLES,
    MembershipRole,
)
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/readiness"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def team(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    db.flush()
    heads = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, heads


READS = [("GET", "/score"), ("GET", "/history"), ("GET", "/snapshot"), ("GET", "/inputs")]
WRITES = [("POST", "/score/recompute"), ("PUT", "/inputs")]


@pytest.mark.parametrize("role", list(MembershipRole))
@pytest.mark.parametrize(("method", "path"), READS)
def test_read_routes_by_role(client, team, role, method, path):
    _s, heads = team
    r = client.request(method, BASE + path, headers=heads[role])
    if role in READINESS_READ_ROLES:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", list(MembershipRole))
@pytest.mark.parametrize(("method", "path"), WRITES)
def test_write_routes_by_role(client, team, role, method, path):
    _s, heads = team
    r = client.request(method, BASE + path, headers=heads[role], json={})
    if role in READINESS_WRITE_ROLES:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 403, r.text


def test_requires_auth_and_workspace(client, team):
    _s, heads = team
    anon = client.get(BASE + "/score")
    assert anon.status_code in (401, 422)
    only_auth = {"Authorization": heads[MembershipRole.founder]["Authorization"]}
    no_ws = client.get(BASE + "/score", headers=only_auth)
    assert no_ws.status_code == 422


def test_cross_workspace_header_is_forbidden(client, db, team):
    s, heads = team
    other = create_startup(db, owner=create_user(db, email_verified_at=datetime.now(UTC)))
    db.commit()
    h = {**heads[MembershipRole.founder], "X-Workspace-Id": str(other.id)}
    r = client.get(BASE + "/score", headers=h)
    assert r.status_code == 403
    assert s.id != other.id


def test_score_shape(client, team):
    _s, heads = team
    d = client.get(BASE + "/score", headers=heads[MembershipRole.founder]).json()["data"]
    assert set(d) == {
        "score",
        "band",
        "verdict",
        "ready",
        "capped",
        "raw_score",
        "dimensions",
        "must_haves",
        "gaps",
        "computed_at",
        "config_version",
    }
    assert d["band"] == "not_yet" and d["ready"] is False
    assert [x["key"] for x in d["dimensions"]] == [
        "team",
        "traction",
        "market",
        "product",
        "financials",
        "story",
    ]
    assert d["verdict"].startswith("Not yet")


def test_team_member_sees_the_same_score_as_founder(client, team):
    _s, heads = team
    a = client.get(BASE + "/score", headers=heads[MembershipRole.founder]).json()["data"]
    b = client.get(BASE + "/score", headers=heads[MembershipRole.team_member]).json()["data"]
    assert a["score"] == b["score"] and a["computed_at"] == b["computed_at"]


def test_recompute_then_rate_limit(client, team):
    _s, heads = team
    h = heads[MembershipRole.founder]
    first = client.post(BASE + "/score/recompute", headers=h)
    assert first.status_code == 200
    r = client.post(BASE + "/score/recompute", headers=h)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"


def test_history_range_and_bad_range(client, team):
    _s, heads = team
    h = heads[MembershipRole.founder]
    client.get(BASE + "/score", headers=h)
    d = client.get(BASE + "/history?range=30", headers=h).json()["data"]
    assert d["range"] == 30 and len(d["points"]) == 1 and d["markers"] == []
    default = client.get(BASE + "/history", headers=h)
    assert default.json()["data"]["range"] == 90
    bad = client.get(BASE + "/history?range=7", headers=h)
    assert bad.status_code == 422


def test_inputs_roundtrip_and_validation(client, team):
    _s, heads = team
    h = heads[MembershipRole.founder]
    body = {
        "tam_minor": 1000000,
        "sam_minor": 500000,
        "som_minor": 100000,
        "source_url": "https://example.com/report",
    }
    r = client.put(BASE + "/inputs", headers=h, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["source_url"] == "https://example.com/report"
    got = client.get(BASE + "/inputs", headers=heads[MembershipRole.team_member]).json()["data"]
    assert got["som_minor"] == 100000
    nested = client.put(BASE + "/inputs", headers=h, json={"sam_minor": 5, "tam_minor": 1})
    assert nested.status_code == 422
    bad_url = client.put(BASE + "/inputs", headers=h, json={"source_url": "nope"})
    assert bad_url.status_code == 422
    unknown = client.put(BASE + "/inputs", headers=h, json={"bogus": 1})
    assert unknown.status_code == 422
    # The sourced market size now moves the Market dimension's signal.
    score = client.get(BASE + "/score?x=1", headers=h).json()["data"]
    market = next(d for d in score["dimensions"] if d["key"] == "market")
    assert next(s for s in market["signals"] if s["key"] == "market.size")["score"] == 100


def test_snapshot_has_no_gap_notes(client, team):
    _s, heads = team
    d = client.get(BASE + "/snapshot", headers=heads[MembershipRole.founder]).json()["data"]
    assert "gaps" not in d and "must_haves" not in d
    assert len(d["dimensions"]) == 6


def test_inputs_reject_nesting_against_stored_values_and_other_currencies(client, team):
    _s, heads = team
    h = heads[MembershipRole.founder]
    ok = client.put(
        BASE + "/inputs", headers=h, json={"tam_minor": 100, "sam_minor": 50, "som_minor": 10}
    )
    assert ok.status_code == 200, ok.text
    vs_stored = client.put(BASE + "/inputs", headers=h, json={"sam_minor": 500})
    assert vs_stored.status_code == 422
    assert vs_stored.json()["error"]["field_errors"][0]["field"] == "sam_minor"
    eur = client.put(BASE + "/inputs", headers=h, json={"currency": "EUR"})
    assert eur.status_code == 422
    usd = client.put(BASE + "/inputs", headers=h, json={"currency": "USD"})
    assert usd.status_code == 200 and usd.json()["data"]["currency"] == "USD"
