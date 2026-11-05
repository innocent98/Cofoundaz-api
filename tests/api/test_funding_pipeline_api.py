"""Funding Hub slice A: round, pipeline, stage rules, interaction log, Finance hand-off, roles."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import FUNDING_ROLES, MembershipRole
from app.db.models.finance import Transaction
from app.db.models.funding import Investor
from app.db.models.notification import Notification
from app.services.billing import subscription as subs
from tests.billing_helpers import seed_plans
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/funding"
NOTIF = "funding.round.committed_changed"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def team(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    db.flush()
    members = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, {r: m[1] for r, m in members.items()}, {r: m[0] for r, m in members.items()}


@pytest.fixture()
def founder(team):
    return team[1][MembershipRole.founder]


def _add(client, h, **kw):
    body = {"name": "Ada Obi", "firm": "Lagos Angels", **kw}
    r = client.post(f"{BASE}/investors", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _move(client, h, inv, stage, **kw):
    return client.post(
        f"{BASE}/investors/{inv['id']}/stage", json={"stage": stage, **kw}, headers=h
    )


# ---- roles ----
READS = [("GET", "/round"), ("GET", "/overview"), ("GET", "/investors")]
WRITES = [("PUT", "/round"), ("POST", "/investors")]


@pytest.mark.parametrize("role", list(MembershipRole))
@pytest.mark.parametrize(("method", "path"), READS + WRITES)
def test_every_route_by_role(client, team, role, method, path):
    _s, heads, _u = team
    body = {"name": "X"} if path == "/investors" else {}
    r = client.request(
        method, BASE + path, headers=heads[role], json=body if method != "GET" else None
    )
    if role in FUNDING_ROLES:
        assert r.status_code in (200, 201), r.text
    else:
        assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", [r for r in MembershipRole if r not in FUNDING_ROLES])
def test_refused_roles_cannot_touch_an_existing_investor(client, team, role):
    _s, heads, _u = team
    inv = _add(client, heads[MembershipRole.founder])
    h = heads[role]
    calls = [
        client.get(f"{BASE}/investors/{inv['id']}", headers=h),
        client.patch(f"{BASE}/investors/{inv['id']}", json={"warm": True}, headers=h),
        client.delete(f"{BASE}/investors/{inv['id']}", headers=h),
        _move(client, h, inv, "outreach"),
        client.get(f"{BASE}/investors/{inv['id']}/events", headers=h),
        client.post(
            f"{BASE}/investors/{inv['id']}/events", json={"kind": "note", "body": "x"}, headers=h
        ),
        client.post(f"{BASE}/investors/{inv['id']}/record-funding", json={}, headers=h),
    ]
    assert [c.status_code for c in calls] == [403] * 7


def test_team_member_has_full_pipeline_access(client, team):
    _s, heads, _u = team
    h = heads[MembershipRole.team_member]
    inv = _add(client, h)
    resp = _move(client, h, inv, "outreach")
    assert resp.status_code == 200
    resp = client.delete(f"{BASE}/investors/{inv['id']}", headers=h)
    assert resp.status_code == 200


def test_requires_auth_and_workspace(client, team):
    _s, heads, _u = team
    resp = client.get(BASE + "/round")
    assert resp.status_code in (401, 422)
    only_auth = {"Authorization": heads[MembershipRole.founder]["Authorization"]}
    resp = client.get(BASE + "/round", headers=only_auth)
    assert resp.status_code == 422


def test_workspace_header_for_another_workspace_is_forbidden(client, db, team):
    _s, heads, _u = team
    other = create_startup(db, owner=create_user(db, email_verified_at=datetime.now(UTC)))
    db.commit()
    h = {**heads[MembershipRole.founder], "X-Workspace-Id": str(other.id)}
    resp = client.get(BASE + "/investors", headers=h)
    assert resp.status_code == 403


def test_another_workspaces_investor_is_404(client, db, team):
    _s, heads, _u = team
    inv = _add(client, heads[MembershipRole.founder])
    s2 = create_startup(db, owner=create_user(db, email_verified_at=datetime.now(UTC)))
    _u2, h2 = _member(db, s2, MembershipRole.founder)
    db.commit()
    resp = client.get(f"{BASE}/investors/{inv['id']}", headers=h2)
    assert resp.status_code == 404
    resp = client.patch(f"{BASE}/investors/{inv['id']}", json={"warm": True}, headers=h2)
    assert resp.status_code == 404
    resp = client.delete(f"{BASE}/investors/{inv['id']}", headers=h2)
    assert resp.status_code == 404
    resp = _move(client, h2, inv, "outreach")
    assert resp.status_code == 404
    resp = client.get(f"{BASE}/investors/{inv['id']}/events", headers=h2)
    assert resp.status_code == 404
    resp = client.get(f"{BASE}/investors", headers=h2)
    assert resp.json()["data"]["total"] == 0


# ---- plan gate ----
def test_plan_gate_is_dark_then_402_on_starter_and_open_on_scale(client, db, team, monkeypatch):
    s, heads, _u = team
    h = heads[MembershipRole.founder]
    seed_plans(db)
    resp = client.get(f"{BASE}/round", headers=h)
    assert resp.status_code == 200  # enforcement off
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, s.id)
    sub.trial_used_at = subs.now()
    db.flush()
    r = client.get(f"{BASE}/round", headers=h)
    assert r.status_code == 402 and r.json()["error"]["code"] == "PLAN_REQUIRED"
    assert r.json()["error"]["message"] == "Upgrade to Scale to use this."
    # A refused role is 403 before the plan check.
    resp = client.get(f"{BASE}/round", headers=heads[MembershipRole.mentor])
    assert resp.status_code == 403
    sub.status, sub.plan_key = "active", "scale"
    sub.current_period_end = subs.now() + timedelta(days=5)
    db.flush()
    resp = client.get(f"{BASE}/round", headers=h)
    assert resp.status_code == 200


# ---- round ----
def test_no_round_until_one_is_made(client, founder):
    resp = client.get(f"{BASE}/round", headers=founder)
    assert resp.json()["data"] is None
    ov = client.get(f"{BASE}/overview", headers=founder).json()["data"]
    assert ov["empty"] is True and ov["round"] is None and ov["totals"] is None
    assert [s["stage"] for s in ov["stages"]] == [
        "research",
        "outreach",
        "meeting",
        "diligence",
        "term_sheet",
        "committed",
        "passed",
    ]


def test_put_round_creates_then_updates_and_tracks_open_close(client, founder):
    r = client.put(
        f"{BASE}/round", json={"name": "Seed", "target_minor": 9_000_000_000}, headers=founder
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "planning" and d["currency"] == "NGN" and d["opened_at"] is None
    d2 = client.put(f"{BASE}/round", json={"status": "open"}, headers=founder).json()["data"]
    assert d2["id"] == d["id"] and d2["opened_at"] and d2["target_minor"] == 9_000_000_000
    d3 = client.put(f"{BASE}/round", json={"target_minor": None}, headers=founder).json()["data"]
    assert d3["target_minor"] is None
    d4 = client.put(f"{BASE}/round", json={"status": "closed"}, headers=founder).json()["data"]
    assert d4["closed_at"]
    # The closed round is history; PUT now starts a new one.
    d5 = client.put(
        f"{BASE}/round", json={"name": "Series A", "currency": "USD"}, headers=founder
    ).json()["data"]
    assert d5["id"] != d["id"] and d5["currency"] == "USD" and d5["status"] == "planning"


def test_round_validation(client, founder):
    for body in (
        {"currency": "EUR"},
        {"status": "bogus"},
        {"target_minor": -1},
        {"name": ""},
        {"unknown": 1},
    ):
        r = client.put(f"{BASE}/round", json=body, headers=founder)
        assert r.status_code == 422, (body, r.text)
    resp = client.put(f"{BASE}/round", json={"status": "closed"}, headers=founder)
    assert resp.status_code == 422


def test_currency_cannot_change_once_amounts_exist(client, founder):
    client.put(f"{BASE}/round", json={"target_minor": 100}, headers=founder)
    _add(client, founder, check_min_minor=100)
    r = client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    assert r.status_code == 422 and "currency" in r.json()["error"]["field_errors"][0]["field"]


def test_first_investor_creates_a_planning_round_and_closed_round_needs_a_new_one(client, founder):
    _add(client, founder)
    rnd = client.get(f"{BASE}/round", headers=founder).json()["data"]
    assert rnd["status"] == "planning" and rnd["target_minor"] is None
    client.put(f"{BASE}/round", json={"status": "closed"}, headers=founder)
    r = client.post(f"{BASE}/investors", json={"name": "Late"}, headers=founder)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ROUND_CLOSED"
    # Closed round's investors stay readable.
    resp = client.get(f"{BASE}/investors", headers=founder)
    assert resp.json()["data"]["total"] == 1


# ---- investors ----
def test_add_edit_delete_investor(client, founder):
    inv = _add(
        client,
        founder,
        type="vc",
        warm=True,
        check_min_minor=1_000_000,
        check_max_minor=5_000_000,
        next_step="Send deck",
        next_step_on=str(date.today() + timedelta(days=3)),
    )
    assert inv["stage"] == "research" and inv["warm"] is True and inv["position"] == 0
    assert (
        inv["days_in_stage"] == 0
        and inv["next_step_overdue"] is False
        and inv["source"] == "manual"
    )
    p = client.patch(
        f"{BASE}/investors/{inv['id']}",
        json={"thesis_notes": "Fintech only", "firm": None, "check_max_minor": 9_000_000},
        headers=founder,
    ).json()["data"]
    assert (
        p["thesis_notes"] == "Fintech only"
        and p["firm"] is None
        and p["check_max_minor"] == 9_000_000
    )
    assert p["name"] == "Ada Obi"  # untouched
    got = client.get(f"{BASE}/investors/{inv['id']}", headers=founder).json()["data"]
    assert got["thesis_notes"] == "Fintech only"
    resp = client.delete(f"{BASE}/investors/{inv['id']}", headers=founder)
    assert resp.json()["data"] == {"deleted": True}
    resp = client.get(f"{BASE}/investors/{inv['id']}", headers=founder)
    assert resp.status_code == 404


def test_investor_validation(client, founder):
    bad = [
        {"name": ""},
        {"name": "A", "type": "bank"},
        {"name": "A", "check_min_minor": 10, "check_max_minor": 5},
        {"name": "A", "check_min_minor": -1},
        {"name": "A", "stage": "passed"},
        {"name": "A", "stage": "nope"},
        {"name": "A", "firm": "x" * 161},
        {"name": "A", "committed_minor": 10**16},
    ]
    for body in bad:
        resp = client.post(f"{BASE}/investors", json=body, headers=founder)
        assert resp.status_code == 422, body
    inv = _add(client, founder, check_min_minor=10)
    r = client.patch(f"{BASE}/investors/{inv['id']}", json={"check_max_minor": 5}, headers=founder)
    assert r.status_code == 422  # checked against the stored minimum too
    resp = client.patch(f"{BASE}/investors/{inv['id']}", json={"stage": "meeting"}, headers=founder)
    assert resp.status_code == 422


def test_list_filters_search_and_board_order(client, founder):
    a = _add(client, founder, name="Ada", firm="Alpha_Fund", warm=True, type="vc")
    b = _add(client, founder, name="Bola", firm="Beta", type="angel")
    _move(client, founder, b, "meeting")
    items = client.get(f"{BASE}/investors", headers=founder).json()["data"]["items"]
    assert [i["name"] for i in items] == ["Ada", "Bola"]  # research before meeting

    def q(**p):
        return client.get(f"{BASE}/investors", params=p, headers=founder).json()["data"]

    assert q(stage="meeting")["total"] == 1
    assert q(warm="true")["items"][0]["id"] == a["id"]
    assert q(type="vc")["total"] == 1
    assert q(q="alpha_")["total"] == 1  # `_` is literal, not a wildcard
    assert q(q="%")["total"] == 0
    assert q(limit=1, offset=1)["items"][0]["name"] == "Bola"
    resp = client.get(f"{BASE}/investors?stage=bogus", headers=founder)
    assert resp.status_code == 422


# ---- stages ----
def test_move_stage_writes_history_and_resets_the_clock(client, db, founder):
    inv = _add(client, founder)
    row = db.get(Investor, inv["id"])
    row.stage_entered_at = datetime.now(UTC) - timedelta(days=9)
    db.commit()
    resp = client.get(f"{BASE}/investors/{inv['id']}", headers=founder)
    assert resp.json()["data"]["days_in_stage"] == 9
    r = _move(client, founder, inv, "meeting", note="Intro call booked")
    assert r.status_code == 200
    d = r.json()["data"]["investor"]
    assert (
        d["stage"] == "meeting"
        and d["days_in_stage"] == 0
        and r.json()["data"]["record_in_finance"] is None
    )
    ev = client.get(f"{BASE}/investors/{inv['id']}/events", headers=founder).json()["data"]["items"]
    assert [(e["kind"], e["from_stage"], e["to_stage"]) for e in ev] == [
        ("stage_change", "research", "meeting"),
        ("stage_change", None, "research"),
    ]
    assert ev[0]["body"] == "Intro call booked"


def test_passed_needs_a_reason_and_leaving_clears_it(client, founder):
    inv = _add(client, founder)
    r = _move(client, founder, inv, "passed")
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "passed_reason"
    resp = _move(client, founder, inv, "passed", passed_reason="vibes")
    assert resp.status_code == 422
    ok = _move(client, founder, inv, "passed", passed_reason="valuation").json()["data"]["investor"]
    assert ok["stage"] == "passed" and ok["passed_reason"] == "valuation"
    back = _move(client, founder, inv, "outreach").json()["data"]["investor"]
    assert back["passed_reason"] is None
    resp = _move(client, founder, inv, "meeting", passed_reason="ghosted")
    assert resp.status_code == 422


def test_reorder_within_a_column_and_across(client, founder):
    a, b, c = (_add(client, founder, name=n) for n in "ABC")
    assert [a["position"], b["position"], c["position"]] == [0, 1, 2]
    _move(client, founder, c, "research", position=0)  # same stage = reorder, no event
    names = [
        i["name"] for i in client.get(f"{BASE}/investors", headers=founder).json()["data"]["items"]
    ]
    assert names == ["C", "A", "B"]
    resp = client.get(f"{BASE}/investors/{c['id']}/events", headers=founder)
    assert len(resp.json()["data"]["items"]) == 1
    _move(client, founder, a, "outreach")
    items = client.get(f"{BASE}/investors", headers=founder).json()["data"]["items"]
    assert [(i["name"], i["stage"], i["position"]) for i in items] == [
        ("C", "research", 0),
        ("B", "research", 1),
        ("A", "outreach", 0),
    ]


# ---- interaction log ----
def test_interaction_log_and_last_touch(client, founder):
    inv = _add(client, founder)
    assert inv["last_touch_at"] is None
    when = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    r = client.post(
        f"{BASE}/investors/{inv['id']}/events",
        json={"kind": "call", "body": "30 min intro", "occurred_at": when},
        headers=founder,
    )
    assert r.status_code == 201 and r.json()["data"]["kind"] == "call"
    client.post(
        f"{BASE}/investors/{inv['id']}/events",
        json={"kind": "note", "body": "Warm"},
        headers=founder,
    )
    got = client.get(f"{BASE}/investors/{inv['id']}", headers=founder).json()["data"]
    assert got["last_touch_at"] is not None
    page = client.get(f"{BASE}/investors/{inv['id']}/events?limit=2", headers=founder).json()[
        "data"
    ]
    assert page["total"] == 3 and len(page["items"]) == 2 and page["items"][0]["body"] == "Warm"


def test_event_validation(client, founder):
    inv = _add(client, founder)
    url = f"{BASE}/investors/{inv['id']}/events"
    resp = client.post(url, json={"kind": "stage_change", "body": "x"}, headers=founder)
    assert resp.status_code == 422
    resp = client.post(url, json={"kind": "note", "body": ""}, headers=founder)
    assert resp.status_code == 422
    resp = client.post(url, json={"kind": "tweet", "body": "x"}, headers=founder)
    assert resp.status_code == 422
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    resp = client.post(
        url, json={"kind": "note", "body": "x", "occurred_at": future}, headers=founder
    )
    assert resp.status_code == 422


# ---- overview + totals ----
def test_overview_totals_split_soft_and_committed(client, founder):
    client.put(f"{BASE}/round", json={"target_minor": 1000}, headers=founder)
    a = _add(client, founder, name="A", committed_minor=300)
    b = _add(client, founder, name="B", committed_minor=200)
    c = _add(client, founder, name="C")  # no amount: never counted
    d = _add(client, founder, name="D", committed_minor=999, warm=True)
    _move(client, founder, a, "term_sheet")
    _move(client, founder, b, "committed")
    _move(client, founder, c, "committed")
    _move(client, founder, d, "passed", passed_reason="other")  # passed never counted
    ov = client.get(f"{BASE}/overview", headers=founder).json()["data"]
    assert ov["totals"] == {
        "currency": "NGN",
        "target_minor": 1000,
        "soft_minor": 300,
        "committed_minor": 200,
        "soft_pct": 30,
        "committed_pct": 20,
    }
    assert {s["stage"]: s["count"] for s in ov["stages"]} == {
        "research": 0,
        "outreach": 0,
        "meeting": 0,
        "diligence": 0,
        "term_sheet": 1,
        "committed": 2,
        "passed": 1,
    }
    assert (
        ov["counts"] == {"investors": 4, "warm": 1, "overdue_next_steps": 0}
        and ov["empty"] is False
    )


def test_overview_without_a_target_has_null_percentages(client, founder):
    inv = _add(client, founder, committed_minor=50)
    _move(client, founder, inv, "committed")
    t = client.get(f"{BASE}/overview", headers=founder).json()["data"]["totals"]
    assert t["committed_minor"] == 50 and t["committed_pct"] is None and t["target_minor"] is None


def test_overdue_next_steps_are_counted_but_not_for_closed_out_stages(client, db, founder):
    a = _add(client, founder, name="A", next_step_on=str(date.today() - timedelta(days=2)))
    b = _add(client, founder, name="B", next_step_on=str(date.today() - timedelta(days=2)))
    _move(client, founder, b, "committed")
    assert a["next_step_overdue"] is True and b["next_step_overdue"] is True
    ov = client.get(f"{BASE}/overview", headers=founder).json()["data"]
    assert ov["counts"]["overdue_next_steps"] == 1
    items = {
        i["name"]: i
        for i in client.get(f"{BASE}/investors", headers=founder).json()["data"]["items"]
    }
    assert items["A"]["next_step_overdue"] is True and items["B"]["next_step_overdue"] is False


def test_suggested_target_is_18_months_of_net_burn_for_ngn_only(client, db, team, founder):
    s = team[0]
    from app.db.models.enums import TransactionDirection, TransactionSource

    today = date.today()
    for amt, direction in (
        (50_000_00, TransactionDirection.inflow),
        (900_000_00, TransactionDirection.outflow),
    ):
        db.add(
            Transaction(
                startup_id=s.id,
                date=today - timedelta(days=5),
                description="t",
                category="Software",
                amount_minor=amt,
                currency="NGN",
                direction=direction,
                source=TransactionSource.manual,
            )
        )
    db.commit()
    ov = client.get(f"{BASE}/overview", headers=founder).json()["data"]
    st = ov["suggested_target"]
    assert (
        st is not None
        and st["months"] == 18
        and st["amount_minor"] == st["monthly_burn_minor"] * 18
    )
    client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    resp = client.get(f"{BASE}/overview", headers=founder)
    assert resp.json()["data"]["suggested_target"] is None


# ---- committed-changed notification ----
def _notes(db, s):
    return db.query(Notification).filter_by(startup_id=s.id, type=NOTIF).all()


def test_committed_change_notifies_founder_and_team_minus_actor_only(client, db, team):
    s, heads, users = team
    client.put(
        f"{BASE}/round", json={"target_minor": 500_000_00}, headers=heads[MembershipRole.founder]
    )
    inv = _add(client, heads[MembershipRole.founder], committed_minor=100_000_00)
    assert _notes(db, s) == []  # not committed yet
    _move(client, heads[MembershipRole.founder], inv, "term_sheet")
    assert _notes(db, s) == []  # soft money is not "committed"
    _move(client, heads[MembershipRole.founder], inv, "committed")
    notes = _notes(db, s)
    assert [n.user_id for n in notes] == [users[MembershipRole.team_member].id]  # actor excluded
    assert notes[0].title == "Funding committed is now ₦100,000 of ₦500,000"
    # amount edit while committed re-fires; an edit that changes nothing does not.
    client.patch(
        f"{BASE}/investors/{inv['id']}", json={"warm": True}, headers=heads[MembershipRole.founder]
    )
    assert len(_notes(db, s)) == 1
    client.patch(
        f"{BASE}/investors/{inv['id']}",
        json={"committed_minor": 150_000_00},
        headers=heads[MembershipRole.team_member],
    )
    after = _notes(db, s)
    assert len(after) == 2
    assert sorted(n.user_id for n in after) == sorted(
        [users[MembershipRole.team_member].id, users[MembershipRole.founder].id]
    )  # the edit went to the founder (the team member was the actor)
    client.delete(f"{BASE}/investors/{inv['id']}", headers=heads[MembershipRole.founder])
    assert len(_notes(db, s)) == 3
    for n in _notes(db, s):  # never to mentors, investors, accountants...
        assert n.user_id in {users[MembershipRole.founder].id, users[MembershipRole.team_member].id}


# ---- Finance hand-off (D-FUND-L3) ----
def test_moving_to_committed_offers_but_never_writes_to_finance(client, db, team, founder):
    inv = _add(client, founder, committed_minor=2_000_000_00)
    r = _move(client, founder, inv, "committed")
    assert r.json()["data"]["record_in_finance"] == {
        "amount_minor": 2_000_000_00,
        "currency": "NGN",
        "needs_ngn_amount": False,
        "fits_ledger": True,
    }
    assert db.query(Transaction).filter_by(startup_id=team[0].id).count() == 0


def test_no_offer_without_an_amount(client, founder):
    inv = _add(client, founder)
    resp = _move(client, founder, inv, "committed")
    assert resp.json()["data"]["record_in_finance"] is None


def test_record_funding_writes_one_funding_inflow_once(client, db, team, founder):
    s = team[0]
    inv = _add(client, founder, committed_minor=2_000_000_00)
    resp = client.post(f"{BASE}/investors/{inv['id']}/record-funding", json={}, headers=founder)
    assert resp.status_code == 409
    _move(client, founder, inv, "committed")
    r = client.post(
        f"{BASE}/investors/{inv['id']}/record-funding",
        json={"received_on": "2026-11-01"},
        headers=founder,
    )
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    tx = d["transaction"]
    assert (
        tx["category"] == "Funding"
        and tx["direction"] == "in"
        and tx["amount_minor"] == 2_000_000_00
    )
    assert tx["description"] == "Funding from Ada Obi (Lagos Angels)" and tx["date"] == "2026-11-01"
    assert d["investor"]["recorded_in_finance"] is True
    assert db.query(Transaction).filter_by(startup_id=s.id, category="Funding").count() == 1
    again = client.post(f"{BASE}/investors/{inv['id']}/record-funding", json={}, headers=founder)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_RECORDED"
    assert db.query(Transaction).filter_by(startup_id=s.id).count() == 1
    # Already recorded: no second offer.
    resp = _move(client, founder, inv, "diligence")
    assert resp.json()["data"]["record_in_finance"] is None
    resp = _move(client, founder, inv, "committed")
    assert resp.json()["data"]["record_in_finance"] is None


def test_record_funding_for_a_usd_round_needs_the_naira_amount(client, db, team, founder):
    client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    inv = _add(client, founder, committed_minor=100_000_00)
    offer = _move(client, founder, inv, "committed").json()["data"]["record_in_finance"]
    assert offer == {
        "amount_minor": None,
        "currency": "USD",
        "needs_ngn_amount": True,
        "fits_ledger": True,
    }
    url = f"{BASE}/investors/{inv['id']}/record-funding"
    r = client.post(url, json={}, headers=founder)
    assert r.status_code == 422 and r.json()["error"]["field_errors"][0]["field"] == "amount_minor"
    ok = client.post(url, json={"amount_minor": 150_000_000}, headers=founder)
    assert ok.status_code == 201 and ok.json()["data"]["transaction"]["currency"] == "NGN"


def test_record_funding_above_the_ledger_cap_is_refused_clearly(client, db, team, founder):
    inv = _add(client, founder, committed_minor=9_000_000_000_00)
    offer = _move(client, founder, inv, "committed").json()["data"]["record_in_finance"]
    assert offer["fits_ledger"] is False
    r = client.post(f"{BASE}/investors/{inv['id']}/record-funding", json={}, headers=founder)
    assert r.status_code == 422 and "21,474,836.47" in r.json()["error"]["message"]
    assert db.query(Transaction).filter_by(startup_id=team[0].id).count() == 0


def test_record_funding_takes_a_row_lock_on_the_investor(client, db, team, founder, monkeypatch):
    """The 'already recorded?' read decides a ledger write, so it must hold the investor row."""
    from sqlalchemy.orm import Query

    seen: list[bool] = []
    real = Query.with_for_update

    def spy(self, *a, **k):
        seen.append(True)
        return real(self, *a, **k)

    inv = _add(client, founder, committed_minor=1_000_00)
    _move(client, founder, inv, "committed")
    monkeypatch.setattr(Query, "with_for_update", spy)
    r = client.post(f"{BASE}/investors/{inv['id']}/record-funding", json={}, headers=founder)
    assert r.status_code == 201
    assert seen == [True]


# ---- review fixes: races, frozen money, locks ----
def test_lost_race_on_first_investor_reuses_the_round_instead_of_a_500(
    client, db, founder, monkeypatch
):
    from app.services.funding import rounds as rounds_svc

    client.put(f"{BASE}/round", json={"name": "Seed"}, headers=founder)  # the winner's round
    real_latest, real_active = rounds_svc.latest_round, rounds_svc.active_round
    calls = {"latest": 0}

    def stale_latest(db_, sid):
        calls["latest"] += 1
        return None if calls["latest"] == 1 else real_latest(db_, sid)

    monkeypatch.setattr(rounds_svc, "latest_round", stale_latest)
    r = client.post(f"{BASE}/investors", json={"name": "Racer"}, headers=founder)
    assert r.status_code == 201, r.text
    monkeypatch.setattr(rounds_svc, "latest_round", real_latest)
    rounds = client.get(f"{BASE}/round", headers=founder).json()["data"]
    assert rounds["name"] == "Seed"  # joined the winner's round, did not create a second
    assert real_active is rounds_svc.active_round


def test_lost_race_on_put_round_applies_on_top_of_the_winner(client, founder, monkeypatch):
    from app.services.funding import rounds as rounds_svc

    client.put(f"{BASE}/round", json={"name": "Seed"}, headers=founder)
    real_active = rounds_svc.active_round
    calls = {"n": 0}

    def stale_active(db_, sid):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_active(db_, sid)

    monkeypatch.setattr(rounds_svc, "active_round", stale_active)
    r = client.put(f"{BASE}/round", json={"target_minor": 500}, headers=founder)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["name"] == "Seed" and r.json()["data"]["target_minor"] == 500


def _recorded(client, founder):
    inv = _add(client, founder, committed_minor=2_000_000_00)
    _move(client, founder, inv, "committed")
    resp = client.post(f"{BASE}/investors/{inv['id']}/record-funding", json={}, headers=founder)
    assert resp.status_code == 201
    return inv


def test_amount_is_frozen_once_recorded_in_finance(client, founder):
    inv = _recorded(client, founder)
    url = f"{BASE}/investors/{inv['id']}"
    r = client.patch(url, json={"committed_minor": 3_000_000_00}, headers=founder)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVESTOR_RECORDED"
    same = client.patch(url, json={"committed_minor": 2_000_000_00, "warm": True}, headers=founder)
    assert same.status_code == 200  # an unchanged amount and other edits are fine
    other = client.patch(url, json={"next_step": "Thank them"}, headers=founder)
    assert other.status_code == 200


def test_round_currency_is_frozen_once_money_is_recorded(client, founder):
    _recorded(client, founder)
    r = client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ROUND_HAS_RECORDED_MONEY"
    ok = client.put(f"{BASE}/round", json={"currency": "NGN", "target_minor": 1}, headers=founder)
    assert ok.status_code == 200  # same currency is not a change


def test_move_stage_takes_the_investor_row_lock(client, founder, monkeypatch):
    from sqlalchemy.orm import Query

    inv = _add(client, founder)
    seen: list[bool] = []
    real = Query.with_for_update

    def spy(self, *a, **k):
        seen.append(True)
        return real(self, *a, **k)

    monkeypatch.setattr(Query, "with_for_update", spy)
    resp = _move(client, founder, inv, "outreach")
    assert resp.status_code == 200
    assert seen == [True]
