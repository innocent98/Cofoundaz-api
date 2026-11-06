"""Funding Hub slice C: the cap table. Founder-only on every route, entry shape rules, the ledger
view, the round model over the API, scenarios, the audit trail, locking and the currency guard."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.cap_table import CapAudit, CapEntry, CapHolder
from app.db.models.enums import MembershipRole
from app.services.billing import subscription as subs
from tests.billing_helpers import seed_plans
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/funding"
CT = f"{BASE}/cap-table"


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


def holder(client, h, name="Ada Obi", kind="founder", **kw):
    r = client.post(f"{CT}/holders", json={"name": name, "kind": kind, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def entry(client, h, holder_id, **kw):
    body = {"holder_id": holder_id, "security": "common", "shares": 1000, **kw}
    r = client.post(f"{CT}/entries", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["data"]


@pytest.fixture()
def table(client, founder):
    """Founder 8,000,000 common and an unallocated pool of 1,000,000 options."""
    f = holder(client, founder)
    p = holder(client, founder, "Option pool", "pool")
    entry(client, founder, f["id"], shares=8_000_000)
    entry(client, founder, p["id"], security="option", shares=1_000_000)
    return f, p


# ---------------------------------------------------------------- founder only (D-FUND-5)
ROUTES = [
    ("GET", "/cap-table", None),
    ("POST", "/cap-table/holders", {"name": "X", "kind": "advisor"}),
    ("PATCH", "/cap-table/holders/{holder}", {"name": "Y"}),
    ("DELETE", "/cap-table/holders/{holder}", None),
    ("POST", "/cap-table/entries", {"holder_id": "{holder}", "security": "common", "shares": 5}),
    ("PATCH", "/cap-table/entries/{entry}", {"shares": 9}),
    ("DELETE", "/cap-table/entries/{entry}", None),
    (
        "POST",
        "/cap-table/model",
        {"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]},
    ),
    ("GET", "/cap-table/scenarios", None),
    (
        "POST",
        "/cap-table/scenarios",
        {"name": "S", "inputs": {"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]}},
    ),
    ("GET", "/cap-table/scenarios/{scenario}", None),
    ("PATCH", "/cap-table/scenarios/{scenario}", {"name": "Z"}),
    ("DELETE", "/cap-table/scenarios/{scenario}", None),
    ("GET", "/cap-table/audit", None),
]


def _fill(v, ids):
    if isinstance(v, str):
        return v.format(**ids)
    if isinstance(v, dict):
        return {k: _fill(x, ids) for k, x in v.items()}
    return v


@pytest.fixture()
def ids(client, founder, table):
    e = entry(client, founder, table[0]["id"], shares=10)
    sc = client.post(
        f"{CT}/scenarios",
        json={
            "name": "Seed",
            "inputs": {"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]},
        },
        headers=founder,
    ).json()["data"]
    return {"holder": table[0]["id"], "entry": e["id"], "scenario": sc["id"]}


@pytest.mark.parametrize("role", [r for r in MembershipRole if r != MembershipRole.founder])
@pytest.mark.parametrize("method,path,body", ROUTES)
def test_only_the_founder_can_touch_the_cap_table(client, db, team, ids, role, method, path, body):
    """Team members too: equity is founder only. Nothing changes."""
    before = (
        db.query(CapHolder).count(),
        db.query(CapEntry).count(),
        db.query(CapAudit).count(),
    )
    r = client.request(
        method, BASE + _fill(path, ids), json=_fill(body, ids), headers=team[1][role]
    )
    assert r.status_code == 403
    after = (db.query(CapHolder).count(), db.query(CapEntry).count(), db.query(CapAudit).count())
    assert before == after


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_the_founder_can_call_every_route(client, founder, ids, method, path, body):
    r = client.request(method, BASE + _fill(path, ids), json=_fill(body, ids), headers=founder)
    assert r.status_code < 300, r.text


def test_plan_gate_is_dark_then_402(client, db, team, monkeypatch):
    s, heads, _ = team
    seed_plans(db)
    assert client.get(CT, headers=heads[MembershipRole.founder]).status_code == 200
    monkeypatch.setattr(settings, "PLAN_ENFORCEMENT", True)
    sub = subs.get_or_create(db, s.id)
    sub.trial_used_at = subs.now()
    db.flush()
    r = client.get(CT, headers=heads[MembershipRole.founder])
    assert r.status_code == 402 and r.json()["error"]["code"] == "PLAN_REQUIRED"
    assert client.get(CT, headers=heads[MembershipRole.team_member]).status_code == 403
    sub.status, sub.plan_key = "active", "scale"
    sub.current_period_end = subs.now() + timedelta(days=5)
    db.flush()
    assert client.get(CT, headers=heads[MembershipRole.founder]).status_code == 200


def test_another_workspaces_ids_are_404(client, db, founder, ids):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=owner, name="Other")
    _, oh = _member(db, other, MembershipRole.founder)
    db.commit()
    for method, path, body in ROUTES:
        if "{" not in path:
            continue
        r = client.request(method, BASE + _fill(path, ids), json=_fill(body, ids), headers=oh)
        assert r.status_code == 404, (method, path)
    assert client.get(CT, headers=oh).json()["data"]["empty"] is True
    assert client.get(f"{CT}/scenarios", headers=oh).json()["data"]["items"] == []


# ---------------------------------------------------------------- holders and entries
def test_empty_state(client, founder):
    d = client.get(CT, headers=founder).json()["data"]
    assert d["empty"] is True and d["holders"] == [] and d["currency"] == "NGN"
    assert d["totals"]["fully_diluted_shares"] == 0 and "legal or tax advice" in d["disclaimer"]


def test_one_pool_only_and_pool_holds_options_only(client, founder, table):
    r = client.post(f"{CT}/holders", json={"name": "Pool 2", "kind": "pool"}, headers=founder)
    assert r.status_code == 409 and r.json()["error"]["code"] == "POOL_EXISTS"
    r = client.post(
        f"{CT}/entries",
        json={"holder_id": table[1]["id"], "security": "common", "shares": 5},
        headers=founder,
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"security": "common"},  # no shares
        {"security": "common", "shares": 0},
        {"security": "common", "shares": 10, "amount_minor": 5},
        {"security": "common", "shares": 10, "valuation_cap_minor": 5},
        {"security": "common", "shares": 10, "mfn": True},
        {"security": "safe"},  # no amount
        {"security": "safe", "amount_minor": 5, "shares": 3},
        {"security": "safe", "amount_minor": 5, "price_minor": 3},
        {"security": "safe", "amount_minor": 5, "discount_bps": 10000},
        {"security": "safe", "amount_minor": 5, "interest_bps": 100},
        {
            "security": "safe",
            "amount_minor": 5,
            "vesting_months": 12,
            "vesting_start_on": "2026-01-01",
        },
        {"security": "common", "shares": 5, "vesting_months": 12},
        {"security": "common", "shares": 5, "vesting_start_on": "2026-01-01"},
        {
            "security": "common",
            "shares": 5,
            "vesting_months": 12,
            "vesting_start_on": "2026-01-01",
            "vesting_cliff_months": 13,
        },
        {"security": "common", "shares": 5, "vesting_cliff_months": 3},
        {"security": "bond", "shares": 5},
        {"security": "common", "shares": 5, "surprise": 1},
    ],
)
def test_entry_shape_rules(client, founder, table, body):
    r = client.post(f"{CT}/entries", json={"holder_id": table[0]["id"], **body}, headers=founder)
    assert r.status_code == 422, r.text


def test_safe_and_note_have_no_shares_and_default_to_post_money(client, founder, table):
    angel = holder(client, founder, "Angel", "investor")
    s = entry(
        client,
        founder,
        angel["id"],
        security="safe",
        shares=None,
        amount_minor=1_000_000,
        valuation_cap_minor=10_000_000,
    )
    assert s["shares"] is None and s["safe_kind"] == "post_money" and s["mfn"] is False
    n = entry(
        client,
        founder,
        angel["id"],
        security="note",
        shares=None,
        amount_minor=500_000,
        interest_bps=800,
        issued_on="2025-06-01",
        safe_kind="pre_money",
    )
    assert n["interest_bps"] == 800 and n["safe_kind"] == "pre_money"


def test_vesting_defaults_the_cliff_to_zero(client, founder, table):
    e = entry(
        client,
        founder,
        table[0]["id"],
        shares=48_000,
        vesting_months=48,
        vesting_start_on="2026-01-01",
    )
    assert e["vesting_cliff_months"] == 0


def test_update_merges_and_revalidates_as_a_whole(client, founder, table):
    angel = holder(client, founder, "Angel", "investor")
    s = entry(client, founder, angel["id"], security="safe", shares=None, amount_minor=1_000_000)
    r = client.patch(
        f"{CT}/entries/{s['id']}",
        json={"valuation_cap_minor": 8_000_000, "discount_bps": 2000},
        headers=founder,
    )
    assert r.status_code == 200 and r.json()["data"]["valuation_cap_minor"] == 8_000_000
    bad = client.patch(f"{CT}/entries/{s['id']}", json={"shares": 5}, headers=founder)
    assert bad.status_code == 422
    clear = client.patch(f"{CT}/entries/{s['id']}", json={"discount_bps": None}, headers=founder)
    assert clear.json()["data"]["discount_bps"] is None
    moved = client.patch(
        f"{CT}/entries/{s['id']}", json={"holder_id": table[0]["id"]}, headers=founder
    )
    assert moved.status_code == 200 and moved.json()["data"]["holder_id"] == table[0]["id"]
    to_pool = client.patch(
        f"{CT}/entries/{s['id']}", json={"holder_id": table[1]["id"]}, headers=founder
    )
    assert to_pool.status_code == 422  # the pool holds options only


def test_deleting_a_holder_removes_their_entries(client, db, founder, table):
    r = client.delete(f"{CT}/holders/{table[0]['id']}", headers=founder)
    assert r.status_code == 200
    assert db.query(CapEntry).filter_by(holder_id=table[0]["id"]).count() == 0


# ---------------------------------------------------------------- the ledger view
def test_ledger_fully_diluted_and_basic(client, founder, table):
    eve = holder(client, founder, "Eve", "employee")
    entry(
        client,
        founder,
        eve["id"],
        security="option",
        shares=500_000,
        vesting_months=48,
        vesting_start_on="2024-01-01",
        vesting_cliff_months=12,
    )
    fd = client.get(f"{CT}?fully_diluted=true&as_of=2026-01-01", headers=founder).json()["data"]
    t = fd["totals"]
    assert t["issued_shares"] == 8_000_000 and t["granted_option_shares"] == 500_000
    assert t["unallocated_pool_shares"] == 1_000_000 and t["fully_diluted_shares"] == 9_500_000
    pcts = {h["name"]: h["pct_bps"] for h in fd["holders"]}
    assert sum(pcts.values()) == 10_000 and pcts["Ada Obi"] == 8421  # 8 / 9.5
    eve_entry = next(h for h in fd["holders"] if h["name"] == "Eve")["entries"][0]
    assert eve_entry["vested_shares"] == 250_000  # 24 of 48 months
    basic = client.get(f"{CT}?fully_diluted=false", headers=founder).json()["data"]
    pb = {h["name"]: h["pct_bps"] for h in basic["holders"]}
    assert pb["Ada Obi"] == 10_000 and pb["Eve"] is None and pb["Option pool"] is None


def test_ledger_lists_convertibles_as_amounts_with_accrued_interest(client, founder, table):
    bank = holder(client, founder, "Bank", "investor")
    entry(
        client,
        founder,
        bank["id"],
        security="note",
        shares=None,
        amount_minor=1_000_000,
        interest_bps=800,
        issued_on="2025-06-01",
    )
    d = client.get(f"{CT}?as_of=2026-06-01", headers=founder).json()["data"]
    e = next(h for h in d["holders"] if h["name"] == "Bank")["entries"][0]
    assert e["shares"] is None and e["converting_amount_minor"] == 1_080_000
    assert d["totals"]["convertible_amount_minor"] == 1_080_000
    assert d["totals"]["fully_diluted_shares"] == 9_000_000  # a note adds no shares


# ---------------------------------------------------------------- the round model over the API
def test_model_headline_and_dilution_table(client, founder, table):
    angel = holder(client, founder, "Angel", "investor")
    entry(
        client,
        founder,
        angel["id"],
        security="safe",
        shares=None,
        amount_minor=1_000_000,
        valuation_cap_minor=10_000_000,
    )
    r = client.post(
        f"{CT}/model",
        json={
            "rounds": [
                {"name": "Seed", "new_money_minor": 10_000_000, "pre_money_minor": 40_000_000}
            ]
        },
        headers=founder,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["headline"] == {"founders_before_bps": 8889, "founders_after_bps": 6400}
    step = d["steps"][0]
    assert step["price_per_share"] == "4.000000" and step["new_investor_shares"] == 2_500_000
    assert step["conversions"][0]["shares"] == 1_000_000
    by = {row["name"]: row for row in d["dilution"]}
    assert by["Ada Obi"]["before_bps"] == 8889 and by["Ada Obi"]["after_bps"] == 6400
    assert by["Seed investors"]["after_bps"] == 2000 and by["Angel"]["after_bps"] == 800
    assert sum(row["after_bps"] for row in d["dilution"]) == 10_000
    assert d["total_shares_before"] == 9_000_000 and d["total_shares_after"] == 12_500_000


def test_model_saves_nothing(client, db, founder, table):
    n = db.query(CapAudit).count()
    client.post(
        f"{CT}/model",
        json={"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]},
        headers=founder,
    )
    assert db.query(CapAudit).count() == n


@pytest.mark.parametrize(
    "body,code",
    [
        (
            {
                "rounds": [
                    {
                        "new_money_minor": 10_000_000,
                        "pre_money_minor": 40_000_000,
                        "pool_target_bps": 9000,
                    }
                ]
            },
            "POOL_TARGET_TOO_HIGH",
        ),
    ],
)
def test_model_refusals_are_422_with_an_engine_code(client, founder, table, body, code):
    r = client.post(f"{CT}/model", json=body, headers=founder)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_model_on_an_empty_table_and_bad_inputs(client, founder):
    ok = {"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]}
    r = client.post(f"{CT}/model", json=ok, headers=founder)
    assert r.status_code == 422 and r.json()["error"]["code"] == "EMPTY_CAP_TABLE"
    for bad in (
        {"rounds": []},
        {"rounds": [{"new_money_minor": 0, "pre_money_minor": 40}]},
        {"rounds": [{"new_money_minor": 1, "pre_money_minor": 40}] * 6},
        {"rounds": [{"new_money_minor": 1, "pre_money_minor": 40, "pool_target_bps": 10000}]},
    ):
        refused = client.post(f"{CT}/model", json=bad, headers=founder)
        assert refused.status_code == 422


def test_two_rounds_dilute_across_rounds(client, founder, table):
    r = client.post(
        f"{CT}/model",
        json={
            "rounds": [
                {"name": "Seed", "new_money_minor": 10_000_000, "pre_money_minor": 40_000_000},
                {"name": "Series A", "new_money_minor": 50_000_000, "pre_money_minor": 150_000_000},
            ]
        },
        headers=founder,
    )
    d = r.json()["data"]
    assert len(d["steps"]) == 2
    by = {row["name"]: row["after_bps"] for row in d["dilution"]}
    assert by["Series A investors"] == 2500 and by["Seed investors"] == 1500


# ---------------------------------------------------------------- scenarios
def test_scenarios_save_list_open_rename_delete(client, founder, table):
    body = {
        "name": "Seed at 40",
        "inputs": {"rounds": [{"new_money_minor": 10_000_000, "pre_money_minor": 40_000_000}]},
    }
    r = client.post(f"{CT}/scenarios", json=body, headers=founder)
    assert r.status_code == 201
    s = r.json()["data"]
    assert (
        s["headline"]["founders_after_bps"] == 7111
        and s["inputs"]["rounds"][0]["pool_target_bps"] == 0
    )
    assert "result" in s
    lst = client.get(f"{CT}/scenarios", headers=founder).json()["data"]["items"]
    assert [x["name"] for x in lst] == ["Seed at 40"] and "result" not in lst[0]
    # a snapshot: later edits do not rewrite the saved numbers
    entry(client, founder, table[0]["id"], shares=1_000_000)
    got = client.get(f"{CT}/scenarios/{s['id']}", headers=founder).json()["data"]
    assert got["result"] == s["result"]
    renamed = client.patch(f"{CT}/scenarios/{s['id']}", json={"name": "Renamed"}, headers=founder)
    assert renamed.json()["data"]["name"] == "Renamed"
    deleted = client.delete(f"{CT}/scenarios/{s['id']}", headers=founder)
    assert deleted.status_code == 200
    gone = client.get(f"{CT}/scenarios/{s['id']}", headers=founder)
    assert gone.status_code == 404


def test_a_scenario_that_cannot_work_is_not_saved(client, founder, table):
    body = {
        "name": "Nope",
        "inputs": {
            "rounds": [
                {
                    "new_money_minor": 10_000_000,
                    "pre_money_minor": 40_000_000,
                    "pool_target_bps": 9500,
                }
            ]
        },
    }
    refused = client.post(f"{CT}/scenarios", json=body, headers=founder)
    assert refused.status_code == 422
    assert client.get(f"{CT}/scenarios", headers=founder).json()["data"]["items"] == []


# ---------------------------------------------------------------- audit
def test_every_change_writes_an_audit_row_with_before_and_after(client, db, team, founder):
    h = holder(client, founder)
    e = entry(client, founder, h["id"], shares=100)
    client.patch(f"{CT}/entries/{e['id']}", json={"shares": 250}, headers=founder)
    client.patch(f"{CT}/holders/{h['id']}", json={"name": "Ada O."}, headers=founder)
    client.delete(f"{CT}/entries/{e['id']}", headers=founder)
    client.delete(f"{CT}/holders/{h['id']}", headers=founder)
    log = client.get(f"{CT}/audit?limit=50", headers=founder).json()["data"]
    actions = [(x["entity_type"], x["action"]) for x in reversed(log["items"])]
    assert actions == [
        ("holder", "create"),
        ("entry", "create"),
        ("entry", "update"),
        ("holder", "update"),
        ("entry", "delete"),
        ("holder", "delete"),
    ]
    upd = next(x for x in log["items"] if x["entity_type"] == "entry" and x["action"] == "update")
    assert upd["before"]["shares"] == 100 and upd["after"]["shares"] == 250
    assert upd["actor_id"] == str(team[2][MembershipRole.founder].id)
    dele = next(x for x in log["items"] if x["entity_type"] == "entry" and x["action"] == "delete")
    assert dele["before"]["shares"] == 250 and dele["after"] is None
    page = client.get(f"{CT}/audit?limit=2&offset=2", headers=founder).json()["data"]
    assert page["total"] == 6 and len(page["items"]) == 2


def test_removing_a_holder_audits_each_entry_it_took_with_it(client, founder, table):
    client.delete(f"{CT}/holders/{table[0]['id']}", headers=founder)
    log = client.get(f"{CT}/audit", headers=founder).json()["data"]["items"]
    gone = [x for x in log if x["action"] == "delete"]
    assert {x["entity_type"] for x in gone} == {"entry", "holder"}


def test_scenario_changes_are_audited_without_the_full_result(client, founder, table):
    body = {
        "name": "S",
        "inputs": {"rounds": [{"new_money_minor": 10_000_000, "pre_money_minor": 40_000_000}]},
    }
    s = client.post(f"{CT}/scenarios", json=body, headers=founder).json()["data"]
    client.delete(f"{CT}/scenarios/{s['id']}", headers=founder)
    log = client.get(f"{CT}/audit?limit=2", headers=founder).json()["data"]["items"]
    assert [x["action"] for x in log] == ["delete", "create"]
    assert "result" not in (log[0]["before"] or {})


# ---------------------------------------------------------------- locking
def test_every_write_takes_the_workspace_lock(client, founder, table, monkeypatch):
    from app.services.funding import cap_table as svc

    calls = []
    real = svc.lock_workspace
    monkeypatch.setattr(
        svc, "lock_workspace", lambda db, sid: (calls.append(sid), real(db, sid))[1]
    )
    e = entry(client, founder, table[0]["id"], shares=10)
    client.patch(f"{CT}/entries/{e['id']}", json={"shares": 11}, headers=founder)
    client.delete(f"{CT}/entries/{e['id']}", headers=founder)
    holder(client, founder, "Z", "advisor")
    client.post(
        f"{CT}/scenarios",
        json={"name": "s", "inputs": {"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]}},
        headers=founder,
    )
    assert len(calls) == 5


def test_a_stale_entry_edit_sees_the_latest_row_not_a_cached_copy(client, db, founder, table):
    e = entry(client, founder, table[0]["id"], shares=10)
    db.query(CapEntry).filter_by(id=e["id"]).update({"shares": 99})
    db.commit()
    r = client.patch(f"{CT}/entries/{e['id']}", json={"notes": "x"}, headers=founder)
    assert r.json()["data"]["shares"] == 99  # the merge starts from what is stored now
    log = client.get(f"{CT}/audit?limit=1", headers=founder).json()["data"]["items"][0]
    assert log["before"]["shares"] == 99


# ---------------------------------------------------------------- currency guard
def test_round_currency_is_fixed_once_the_cap_table_has_amounts(client, founder, table):
    r = client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    assert r.status_code == 200  # shares only: no money yet
    client.put(f"{BASE}/round", json={"currency": "NGN"}, headers=founder)
    angel = holder(client, founder, "Angel", "investor")
    entry(client, founder, angel["id"], security="safe", shares=None, amount_minor=1_000_000)
    r = client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CAP_TABLE_HAS_AMOUNTS"
    assert client.get(CT, headers=founder).json()["data"]["currency"] == "NGN"


def test_model_amounts_are_in_the_round_currency(client, founder, table):
    client.put(f"{BASE}/round", json={"currency": "USD"}, headers=founder)
    d = client.post(
        f"{CT}/model",
        json={"rounds": [{"new_money_minor": 10, "pre_money_minor": 40}]},
        headers=founder,
    )
    assert d.json()["data"]["currency"] == "USD"
    assert date.fromisoformat(d.json()["data"]["as_of"])
