"""Analytics & Reports slice B: reports CRUD with an optimistic lock, preview, frozen runs, shares
and the spec section 10 security matrix (per-metric authorisation, tenancy 404s, share scope)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.analytics import AnalyticsReportRun
from app.db.models.enums import MembershipRole, TransactionDirection, TransactionSource
from app.db.models.finance import Transaction
from app.db.models.notification import Notification
from app.services.analytics import service as svc
from app.worker import runner
from app.worker.handlers import analytics as _h
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/analytics"
R = MembershipRole
REVENUE = {"type": "metric", "metric": "finance.revenue", "viz": "number"}
CLICKS = {"type": "metric", "metric": "marketing.clicks", "viz": "number"}
PIPE = {"type": "metric", "metric": "sales.pipeline_value", "viz": "number"}


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


@pytest.fixture(autouse=True)
def _clean_cache(team):
    svc.invalidate(team[0].id)
    yield
    svc.invalidate(team[0].id)


def _work(db):
    for _ in range(5):
        if runner.run_once(db) == 0:
            break


def _seed_revenue(db, s):
    db.add(
        Transaction(
            startup_id=s.id,
            date=date.today() - timedelta(days=2),
            description="x",
            amount_minor=777_000,
            currency="NGN",
            direction=TransactionDirection.inflow,
            source=TransactionSource.manual,
        )
    )
    db.commit()


def _make(client, h, **kw):
    body = {"name": "Board pack", "blocks": [REVENUE, CLICKS], **kw}
    r = client.post(f"{BASE}/reports", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _run(client, db, h, rid):
    r = client.post(f"{BASE}/reports/{rid}/run", headers=h)
    assert r.status_code == 202, r.text
    run_id = r.json()["data"]["run_id"]
    db.expire_all()
    _work(db)
    g = client.get(f"{BASE}/reports/{rid}/runs/{run_id}", headers=h)
    assert g.status_code == 200, g.text
    return g.json()["data"]


def test_role_matrix(client, team):
    _s, hdr, _u = team
    allowed = {R.founder, R.team_member, R.accountant, R.mentor, R.business_consultant}
    for role, h in hdr.items():
        r = client.get(f"{BASE}/reports", headers=h)
        assert r.status_code == (200 if role in allowed else 403), (role, r.status_code)
        w = client.post(f"{BASE}/reports", headers=h, json={"name": "x", "blocks": []})
        assert w.status_code == (201 if role in {R.founder, R.team_member} else 403), role


def test_create_get_list_and_templates(client, team):
    _s, hdr, _u = team
    fo = hdr[R.founder]
    rep = _make(client, fo)
    assert rep["version"] == 1 and rep["block_count"] == 2
    resp = client.get(f"{BASE}/reports/{rep['id']}", headers=fo)
    assert resp.json()["data"]["id"] == rep["id"]
    lst = client.get(f"{BASE}/reports", headers=fo, params={"q": "board"}).json()["data"]
    assert lst["total"] == 1
    t = client.get(f"{BASE}/reports/templates", headers=fo).json()["data"]["templates"]
    assert [x["key"] for x in t] == ["monthly_investor", "weekly_operating_review", "growth_funnel"]
    c = client.post(f"{BASE}/reports", headers=fo, json={"template_key": "growth_funnel"})
    assert c.status_code == 201 and c.json()["data"]["block_count"] == 7
    both = client.post(
        f"{BASE}/reports", headers=fo, json={"template_key": "growth_funnel", "blocks": [CLICKS]}
    )
    assert both.status_code == 422


def test_validation_and_html(client, team):
    fo = team[1][R.founder]
    for body in (
        {"name": "<b>x</b>", "blocks": []},
        {"name": "ok", "blocks": [{"type": "metric", "metric": "nope.nope"}]},
        {"name": "ok", "blocks": [{"type": "text", "text": "<script>1</script>"}]},
        {"name": "ok", "blocks": [CLICKS] * 31},
        {"name": "ok", "blocks": [{**CLICKS, "id": "a"}, {**CLICKS, "id": "a"}]},
    ):
        resp = client.post(f"{BASE}/reports", headers=fo, json=body)
        assert resp.status_code == 422, body


def test_optimistic_lock_409(client, team):
    fo = team[1][R.founder]
    rep = _make(client, fo)
    ok = client.patch(f"{BASE}/reports/{rep['id']}", headers=fo, json={"version": 1, "name": "v2"})
    assert ok.status_code == 200 and ok.json()["data"]["version"] == 2
    stale = client.patch(
        f"{BASE}/reports/{rep['id']}", headers=fo, json={"version": 1, "name": "v3"}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    resp = client.get(f"{BASE}/reports/{rep['id']}", headers=fo)
    assert resp.json()["data"]["name"] == "v2"


def test_tenancy_and_visibility_404(client, db, team):
    s, hdr, _u = team
    fo = hdr[R.founder]
    rep = _make(client, fo)
    # unshared reports do not exist for team member, accountant, mentor, consultant
    for role in (R.team_member, R.accountant, R.mentor, R.business_consultant):
        resp = client.get(f"{BASE}/reports/{rep['id']}", headers=hdr[role])
        assert resp.status_code == 404
        resp = client.post(f"{BASE}/reports/{rep['id']}/run", headers=hdr[role])
        assert resp.status_code == 404
        resp = client.get(f"{BASE}/reports", headers=hdr[role])
        assert resp.json()["data"]["total"] == 0
    # another workspace's founder gets the same 404
    other_owner = create_user(db, email_verified_at=datetime.now(UTC))
    s2 = create_startup(db, owner=other_owner)
    db.flush()
    _u2, h2 = _member(db, s2, R.founder)
    db.commit()
    for method, path in (
        ("get", f"/reports/{rep['id']}"),
        ("delete", f"/reports/{rep['id']}"),
        ("post", f"/reports/{rep['id']}/run"),
        ("get", f"/reports/{rep['id']}/runs"),
        ("get", f"/reports/{rep['id']}/shares"),
    ):
        assert getattr(client, method)(f"{BASE}{path}", headers=h2).status_code == 404, path


def test_team_member_edits_only_own_and_founder_edits_all(client, team):
    _s, hdr, _u = team
    tm, fo = hdr[R.team_member], hdr[R.founder]
    mine = _make(client, tm)
    resp = client.get(f"{BASE}/reports/{mine['id']}", headers=fo)
    assert resp.status_code == 200
    resp = client.patch(
        f"{BASE}/reports/{mine['id']}", headers=fo, json={"version": 1, "name": "by founder"}
    )
    assert resp.status_code == 200
    theirs = _make(client, fo)
    resp = client.put(
        f"{BASE}/reports/{theirs['id']}/shares",
        headers=fo,
        json={"user_ids": [str(team[2][R.team_member].id)]},
    )
    assert resp.status_code == 200
    seen = client.get(f"{BASE}/reports/{theirs['id']}", headers=tm)
    assert seen.status_code == 200 and seen.json()["data"]["can_edit"] is False
    resp = client.patch(
        f"{BASE}/reports/{theirs['id']}", headers=tm, json={"version": 1, "name": "no"}
    )
    assert resp.status_code == 403
    resp = client.delete(f"{BASE}/reports/{theirs['id']}", headers=tm)
    assert resp.status_code == 403
    resp = client.delete(f"{BASE}/reports/{mine['id']}", headers=tm)
    assert resp.status_code == 200
    resp = client.get(f"{BASE}/reports/{mine['id']}", headers=fo)
    assert resp.status_code == 404


def test_preview_is_stateless_and_per_metric(client, db, team):
    s, hdr, _u = team
    _seed_revenue(db, s)
    fo = hdr[R.founder]
    r = client.post(f"{BASE}/reports/preview", headers=fo, json={"blocks": [REVENUE, PIPE]})
    assert r.status_code == 200, r.text
    res = r.json()["data"]["results"]
    assert res[0]["result"]["value"] == 777_000
    assert db.query(AnalyticsReportRun).count() == 0
    for role in (R.accountant, R.mentor, R.business_consultant, R.investor):
        resp = client.post(f"{BASE}/reports/preview", headers=hdr[role], json={"blocks": [REVENUE]})
        assert resp.status_code == 403


def test_run_is_frozen_and_immutable(client, db, team):
    s, hdr, _u = team
    _seed_revenue(db, s)
    fo = hdr[R.founder]
    rep = _make(client, fo)
    first = _run(client, db, fo, rep["id"])
    assert first["status"] == "ready"
    assert first["snapshot"]["results"][0]["result"]["value"] == 777_000
    # change the data, the definition, then delete the report: the run does not move
    _seed_revenue(db, s)
    svc.invalidate(s.id)
    client.patch(f"{BASE}/reports/{rep['id']}", headers=fo, json={"version": 1, "blocks": [CLICKS]})
    again = client.get(f"{BASE}/reports/{rep['id']}/runs/{first['id']}", headers=fo).json()["data"]
    assert again["snapshot"] == first["snapshot"] and again["as_of"] == first["as_of"]
    # a second run reflects the new data and the new definition
    second = _run(client, db, fo, rep["id"])
    assert len(second["snapshot"]["results"]) == 1
    runs = client.get(f"{BASE}/reports/{rep['id']}/runs", headers=fo).json()["data"]["runs"]
    assert len(runs) == 2 and "snapshot" not in runs[0]
    # re-executing a finished run is a no-op
    row = db.get(AnalyticsReportRun, first["id"])
    from app.db.models.job import Job
    from app.services.analytics import runs as rs

    before = row.snapshot_json
    rs.execute(db, Job(type=rs.RUN_JOB, payload={"run_id": str(row.id)}))
    assert db.get(AnalyticsReportRun, first["id"]).snapshot_json == before


def test_double_click_reuses_pending_run(client, db, team):
    fo = team[1][R.founder]
    rep = _make(client, fo)
    a = client.post(f"{BASE}/reports/{rep['id']}/run", headers=fo).json()["data"]
    b = client.post(f"{BASE}/reports/{rep['id']}/run", headers=fo).json()["data"]
    assert a["run_id"] == b["run_id"] and a["job_id"] == b["job_id"]


def test_share_scope_and_locked_blocks(client, db, team):
    s, hdr, users = team
    _seed_revenue(db, s)
    fo = hdr[R.founder]
    rep = _make(client, fo, blocks=[REVENUE, CLICKS, PIPE])
    founder_run = _run(client, db, fo, rep["id"])
    ids = [str(users[r].id) for r in (R.mentor, R.accountant, R.business_consultant)]
    put = client.put(f"{BASE}/reports/{rep['id']}/shares", headers=fo, json={"user_ids": ids})
    assert put.status_code == 200, put.text
    by_role = {x["role"]: x for x in put.json()["data"]["shares"]}
    assert by_role["mentor"]["locked_hubs"] == ["finance"]
    assert by_role["accountant"]["locked_hubs"] == ["marketing", "sales"]

    # mentor: finance block is a bare lock in the definition, the run and the founder's old run
    me = hdr[R.mentor]
    view = client.get(f"{BASE}/reports/{rep['id']}", headers=me).json()["data"]
    assert view["blocks"][0] == {
        "id": view["blocks"][0]["id"],
        "type": "metric",
        "locked": True,
        "needs": "finance",
        "needs_label": "Finance",
    }
    assert view["can_edit"] is False
    mrun = _run(client, db, me, rep["id"])
    assert "777000" not in str(mrun) and "finance.revenue" not in str(mrun)
    old = client.get(f"{BASE}/reports/{rep['id']}/runs/{founder_run['id']}", headers=me).json()[
        "data"
    ]
    assert "777000" not in str(old) and old["snapshot"]["results"][0]["locked"] is True
    # accountant: finance only
    ac = _run(client, db, hdr[R.accountant], rep["id"])
    res = ac["snapshot"]["results"]
    assert res[0]["result"]["value"] == 777_000
    assert res[1]["locked"] and res[2]["locked"]
    # read-only: no edit, no delete, no share changes, no preview
    for role in (R.mentor, R.accountant):
        h = hdr[role]
        resp = client.patch(
            f"{BASE}/reports/{rep['id']}", headers=h, json={"version": 1, "name": "x"}
        )
        assert resp.status_code == 403
        resp = client.delete(f"{BASE}/reports/{rep['id']}", headers=h)
        assert resp.status_code == 403
        resp = client.get(f"{BASE}/reports/{rep['id']}/shares", headers=h)
        assert resp.status_code == 403
        resp = client.put(f"{BASE}/reports/{rep['id']}/shares", headers=h, json={"user_ids": []})
        assert resp.status_code == 403
    # removing a share closes the door (404)
    client.put(f"{BASE}/reports/{rep['id']}/shares", headers=fo, json={"user_ids": [ids[1]]})
    resp = client.get(f"{BASE}/reports/{rep['id']}", headers=me)
    assert resp.status_code == 404
    resp = client.get(f"{BASE}/reports/{rep['id']}/runs/{founder_run['id']}", headers=me)
    assert resp.status_code == 404


def test_share_target_validation(client, db, team):
    s, hdr, users = team
    fo = hdr[R.founder]
    rep = _make(client, fo)
    for bad in (users[R.investor].id, users[R.legal_advisor].id, users[R.founder].id):
        r = client.put(
            f"{BASE}/reports/{rep['id']}/shares", headers=fo, json={"user_ids": [str(bad)]}
        )
        assert r.status_code == 422, bad
    stranger = create_user(db, email_verified_at=datetime.now(UTC))
    db.commit()
    r = client.put(
        f"{BASE}/reports/{rep['id']}/shares", headers=fo, json={"user_ids": [str(stranger.id)]}
    )
    assert r.status_code == 422


def test_duplicate_drops_locked_blocks(client, db, team):
    _s, hdr, users = team
    fo = hdr[R.founder]
    rep = _make(client, fo, blocks=[REVENUE, CLICKS])
    client.put(
        f"{BASE}/reports/{rep['id']}/shares",
        headers=fo,
        json={"user_ids": [str(users[R.team_member].id)]},
    )
    d = client.post(f"{BASE}/reports/{rep['id']}/duplicate", headers=hdr[R.team_member])
    assert d.status_code == 201 and d.json()["data"]["block_count"] == 2
    resp = client.post(f"{BASE}/reports/{rep['id']}/duplicate", headers=hdr[R.mentor])
    assert resp.status_code == 403


def test_notifications_on_share_and_run(client, db, team):
    _s, hdr, users = team
    fo = hdr[R.founder]
    rep = _make(client, fo)
    client.put(
        f"{BASE}/reports/{rep['id']}/shares",
        headers=fo,
        json={"user_ids": [str(users[R.mentor].id)]},
    )
    _run(client, db, fo, rep["id"])
    kinds = {(n.type, n.user_id) for n in db.query(Notification).all()}
    assert ("analytics.report.shared", users[R.mentor].id) in kinds
    assert ("analytics.report.generated", users[R.founder].id) in kinds
    assert not any(t == "analytics.report.generated" and u != users[R.founder].id for t, u in kinds)


def test_run_fails_when_requester_lost_access(client, db, team):
    s, hdr, users = team
    fo = hdr[R.founder]
    rep = _make(client, fo)
    client.put(
        f"{BASE}/reports/{rep['id']}/shares",
        headers=fo,
        json={"user_ids": [str(users[R.accountant].id)]},
    )
    r = client.post(f"{BASE}/reports/{rep['id']}/run", headers=hdr[R.accountant]).json()["data"]
    from app.db.models.enums import MembershipStatus
    from app.db.models.membership import Membership

    db.query(Membership).filter(Membership.user_id == users[R.accountant].id).update(
        {Membership.status: MembershipStatus.removed}
    )
    db.commit()
    _work(db)
    run = db.get(AnalyticsReportRun, r["run_id"])
    assert run.status == "failed" and "no longer" in run.error


def test_run_job_handler_is_registered():
    assert runner.JOB_HANDLERS["analytics.report.run"] is _h.handle_report_run
