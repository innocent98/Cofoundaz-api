"""Analytics & Reports slice C: exports (report PDF / XLSX, data tables XLSX / CSV), the authorised
download, 410 after expiry, the daily purge, and the spec section 10 security matrix: role matrix
per route, tenancy 404s, per-metric redaction inside the FILE, download re-check after a role
change, and the audit row."""

import io
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest
from pypdf import PdfReader

from app.core.security import create_access_token
from app.db.models.analytics import AnalyticsExport, AnalyticsReportRun
from app.db.models.audit import AuditLog
from app.db.models.enums import (
    MembershipRole,
    MembershipStatus,
    TransactionDirection,
    TransactionSource,
)
from app.db.models.finance import Transaction
from app.db.models.membership import Membership
from app.db.models.notification import Notification
from app.platform.storage import get_storage
from app.services.analytics import exports as exp
from app.services.analytics import service as svc
from app.worker import runner
from app.worker import scheduler as sched
from app.worker.handlers import analytics as _h  # noqa: F401 - registers the handlers
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/analytics"
R = MembershipRole
REVENUE = {"type": "metric", "metric": "finance.revenue", "viz": "line"}
CLICKS = {"type": "metric", "metric": "marketing.clicks", "viz": "number"}
NOTE = {"type": "text", "title": "Note", "text": "=1+1 is not a formula here"}
EXPORTERS = {R.founder, R.team_member, R.accountant}
SECRET = 987654  # a marketing number planted in a founder run; must never reach an accountant


@pytest.fixture(autouse=True)
def _local_storage(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path))


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
    db.expire_all()
    for _ in range(8):
        if runner.run_once(db) == 0:
            break
    db.expire_all()


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


def _report(client, h, blocks=None):
    body = {"name": "Board pack", "blocks": blocks or [REVENUE, CLICKS, NOTE]}
    r = client.post(f"{BASE}/reports", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _export(client, db, h, rid, fmt="pdf", run_id=None):
    body = {"format": fmt, **({"run_id": run_id} if run_id else {})}
    r = client.post(f"{BASE}/reports/{rid}/export", headers=h, json=body)
    assert r.status_code == 202, r.text
    assert r.json()["data"]["status"] == "pending"
    _work(db)
    g = client.get(f"{BASE}/exports/{r.json()['data']['id']}", headers=h)
    assert g.status_code == 200, g.text
    return g.json()["data"]


def _tables(client, db, h, metrics, fmt="xlsx", **kw):
    r = client.post(
        f"{BASE}/exports/data-tables", headers=h, json={"metrics": metrics, "format": fmt, **kw}
    )
    assert r.status_code == 202, r.text
    _work(db)
    return client.get(f"{BASE}/exports/{r.json()['data']['id']}", headers=h).json()["data"]


def _xlsx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xml"))


def _share(client, h, rid, *user_ids):
    r = client.put(
        f"{BASE}/reports/{rid}/shares", headers=h, json={"user_ids": [str(u) for u in user_ids]}
    )
    assert r.status_code == 200, r.text


def _founder_run_with_secret(client, db, fo, rid):
    """A ready founder run whose marketing block carries SECRET."""
    r = client.post(f"{BASE}/reports/{rid}/run", headers=fo).json()["data"]
    _work(db)
    run = db.get(AnalyticsReportRun, uuid.UUID(r["run_id"]))
    snap = dict(run.snapshot_json)
    results = [dict(x) for x in snap["results"]]
    clicks = next(x for x in results if x.get("hub") == "marketing")
    clicks["result"] = {**clicks["result"], "status": "ok", "value": SECRET}
    run.snapshot_json = {**snap, "results": results}
    db.commit()
    return r["run_id"]


# ---- role matrix, per route ----
def test_role_matrix_every_route(client, db, team):
    s, hdr, users = team
    rep = _report(client, hdr[R.founder], [REVENUE])
    _share(
        client,
        hdr[R.founder],
        rep["id"],
        *(users[r].id for r in (R.team_member, R.accountant, R.mentor, R.business_consultant)),
    )
    founder_export = _export(client, db, hdr[R.founder], rep["id"])
    for role, h in hdr.items():
        ok = role in EXPORTERS
        lst = client.get(f"{BASE}/exports", headers=h)
        assert lst.status_code == (200 if ok else 403), (role, lst.status_code)
        tbl = client.post(
            f"{BASE}/exports/data-tables",
            headers=h,
            json={"metrics": ["finance.revenue"], "format": "csv"},
        )
        assert tbl.status_code == (202 if ok else 403), (role, tbl.status_code)
        rep_exp = client.post(
            f"{BASE}/reports/{rep['id']}/export", headers=h, json={"format": "pdf"}
        )
        want = {R.founder: 202, R.team_member: 202, R.accountant: 403}.get(role, 403)
        assert rep_exp.status_code == want, (role, rep_exp.status_code)
        det = client.get(f"{BASE}/exports/{founder_export['id']}", headers=h)
        assert det.status_code == (200 if role == R.founder else 404 if ok else 403), role
        dl = client.get(f"{BASE}/exports/{founder_export['id']}/download", headers=h)
        assert dl.status_code == (200 if role == R.founder else 404 if ok else 403), role


def test_report_pdf_end_to_end_audited_and_notified(client, db, team):
    s, hdr, users = team
    _seed_revenue(db, s)
    fo = hdr[R.founder]
    rep = _report(client, fo)
    e = _export(client, db, fo, rep["id"], "pdf")
    assert e["status"] == "ready" and e["format"] == "pdf" and e["artifact_type"] == "report"
    assert e["download_path"] == f"/api/v1/analytics/exports/{e['id']}/download"
    assert e["filename"].startswith("Board pack ") and e["filename"].endswith(".pdf")
    dl = client.get(e["download_path"], headers=fo)
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.headers["content-disposition"].startswith("attachment; filename=")
    assert dl.headers["cache-control"] == "private, no-store"
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(dl.content)).pages)
    assert "Board pack" in text and "7,770.00" in text and "=1+1 is not a formula" in text
    row = db.get(AnalyticsExport, uuid.UUID(e["id"]))
    assert row.download_count == 1 and row.hubs == ["finance", "marketing"]
    audit = db.query(AuditLog).filter(AuditLog.entity_id == row.id).all()
    assert [a.action for a in audit] == ["analytics.export.downloaded"]
    assert audit[0].actor_user_id == users[R.founder].id
    notes = db.query(Notification).filter(Notification.user_id == users[R.founder].id).all()
    titles = [n.title for n in notes]
    assert "Your export is ready: Board pack" in titles
    assert not any(t.startswith("Your report is ready") for t in titles)  # one notice, not two


def test_xlsx_report_has_numeric_cells_and_text_not_formula(client, db, team):
    s, hdr, _u = team
    _seed_revenue(db, s)
    fo = hdr[R.founder]
    e = _export(client, db, fo, _report(client, fo)["id"], "xlsx")
    content = client.get(e["download_path"], headers=fo).content
    xml = _xlsx_text(content)
    assert "<v>7770.00</v>" in xml  # money as a number, in major units
    assert "<f>" not in xml  # nothing is ever a formula
    assert "Numbers as of" in xml  # the About sheet


def test_accountant_export_of_founder_run_never_carries_locked_numbers(client, db, team):
    s, hdr, users = team
    _seed_revenue(db, s)
    fo, ac = hdr[R.founder], hdr[R.accountant]
    rep = _report(client, fo)
    run_id = _founder_run_with_secret(client, db, fo, rep["id"])
    _share(client, fo, rep["id"], users[R.accountant].id)
    # control: the founder's own export of the same run carries the number
    founder_xlsx = _export(client, db, fo, rep["id"], "xlsx", run_id)
    assert str(SECRET) in _xlsx_text(client.get(founder_xlsx["download_path"], headers=fo).content)
    # the accountant cannot start a run, but can export a finished one, redacted to finance
    no_run = client.post(f"{BASE}/reports/{rep['id']}/export", headers=ac, json={"format": "pdf"})
    assert no_run.status_code == 403
    for fmt in ("xlsx", "pdf"):
        e = _export(client, db, ac, rep["id"], fmt, run_id)
        assert e["status"] == "ready", e
        content = client.get(e["download_path"], headers=ac).content
        if fmt == "xlsx":
            body = _xlsx_text(content)
            assert "Locked: Marketing access needed" in body
        else:
            body = "".join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
            assert "Marketing access is needed" in body
        assert str(SECRET) not in body and "987,654" not in body
        assert "7770" in body or "7,770.00" in body
        assert db.get(AnalyticsExport, uuid.UUID(e["id"])).hubs == ["finance"]


def test_tenancy_and_ownership_404(client, db, team):
    s, hdr, users = team
    fo, tm = hdr[R.founder], hdr[R.team_member]
    mine = _tables(client, db, tm, ["marketing.clicks"])
    founders = _tables(client, db, fo, ["finance.revenue"])
    # team member: own only; founder: all
    assert client.get(f"{BASE}/exports/{founders['id']}", headers=tm).status_code == 404
    assert client.get(f"{BASE}/exports/{mine['id']}", headers=fo).status_code == 200
    tm_list = client.get(f"{BASE}/exports", headers=tm).json()["data"]
    assert [x["id"] for x in tm_list["exports"]] == [mine["id"]]
    fo_list = client.get(f"{BASE}/exports", headers=fo).json()["data"]
    assert fo_list["total"] == 2 and fo_list["retention_days"] == 30
    # another workspace: uniform 404 on every route
    other_owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=other_owner)
    db.flush()
    _, oh = _member(db, other, R.founder)
    db.commit()
    rep = _report(client, fo, [REVENUE])
    for path in (f"/exports/{mine['id']}", f"/exports/{mine['id']}/download"):
        assert client.get(f"{BASE}{path}", headers=oh).status_code == 404
    r = client.post(f"{BASE}/reports/{rep['id']}/export", headers=oh, json={"format": "pdf"})
    assert r.status_code == 404
    assert client.get(f"{BASE}/exports", headers=oh).json()["data"]["total"] == 0
    # a run of another report is not a run of this one
    run = client.post(f"{BASE}/reports/{rep['id']}/run", headers=fo).json()["data"]
    _work(db)
    rep2 = _report(client, fo, [REVENUE])
    r = client.post(
        f"{BASE}/reports/{rep2['id']}/export",
        headers=fo,
        json={"format": "pdf", "run_id": run["run_id"]},
    )
    assert r.status_code == 404


def test_data_tables_csv_and_validation(client, db, team):
    s, hdr, _u = team
    _seed_revenue(db, s)
    fo, ac = hdr[R.founder], hdr[R.accountant]
    e = _tables(
        client, db, fo, ["finance.revenue", "finance.revenue"], "csv", include_metadata=False
    )
    assert e["metrics"] == ["finance.revenue"] and e["artifact_type"] == "data_tables"
    dl = client.get(e["download_path"], headers=fo)
    assert dl.headers["content-type"].startswith("text/csv")
    lines = dl.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("Metric,Hub,Value") and "7770.00" in lines[1]
    x = _tables(client, db, fo, ["finance.revenue", "marketing.clicks"], "xlsx")
    assert x["status"] == "ready"
    bad = [
        (fo, {"metrics": ["nope.nope"], "format": "csv"}, 422),
        (fo, {"metrics": ["finance.revenue"], "format": "pdf"}, 422),
        (fo, {"metrics": [], "format": "csv"}, 422),
        (fo, {"metrics": ["finance.revenue"] * 25, "format": "csv"}, 422),
        (fo, {"metrics": ["finance.revenue"], "format": "csv", "extra": 1}, 422),
        # the accountant cannot see marketing: same answer as a metric that does not exist
        (ac, {"metrics": ["marketing.clicks"], "format": "csv"}, 422),
    ]
    for h, body, code in bad:
        r = client.post(f"{BASE}/exports/data-tables", headers=h, json=body)
        assert r.status_code == code, (body, r.text)
    a = client.post(
        f"{BASE}/exports/data-tables",
        headers=ac,
        json={"metrics": ["marketing.clicks"], "format": "csv"},
    ).json()
    b = client.post(
        f"{BASE}/exports/data-tables",
        headers=ac,
        json={"metrics": ["custom.nope"], "format": "csv"},
    ).json()
    assert a["error"]["message"] == b["error"]["message"]
    rep = _report(client, fo, [REVENUE])
    r = client.post(f"{BASE}/reports/{rep['id']}/export", headers=fo, json={"format": "csv"})
    assert r.status_code == 422


def test_pending_409_expired_410_and_purge(client, db, team):
    s, hdr, _u = team
    fo = hdr[R.founder]
    r = client.post(
        f"{BASE}/exports/data-tables",
        headers=fo,
        json={"metrics": ["finance.revenue"], "format": "csv"},
    ).json()["data"]
    pending = client.get(f"{BASE}/exports/{r['id']}/download", headers=fo)
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "EXPORT_NOT_READY"
    _work(db)
    row = db.get(AnalyticsExport, uuid.UUID(r["id"]))
    key = row.file_key
    assert get_storage().read_private(key) is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    old_run = db.get(AnalyticsReportRun, row.run_id)
    db.commit()
    gone = client.get(f"{BASE}/exports/{r['id']}/download", headers=fo)
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "EXPORT_EXPIRED"
    view = client.get(f"{BASE}/exports/{r['id']}", headers=fo).json()["data"]
    assert view["status"] == "expired" and view["download_path"] is None
    # purge: the file goes, the row stays as Expired; runs older than 90 days are deleted
    old_run.created_at = datetime.now(UTC) - timedelta(days=91)
    db.commit()
    out = exp.purge(db)
    db.commit()
    assert out == {"exports_purged": 1, "runs_deleted": 1}
    db.expire_all()
    row = db.get(AnalyticsExport, uuid.UUID(r["id"]))
    assert row.purged_at is not None and row.run_id is None
    assert get_storage().read_private(key) is None
    assert exp.purge(db) == {"exports_purged": 0, "runs_deleted": 0}  # idempotent


def test_download_rechecks_role_after_export(client, db, team):
    s, hdr, users = team
    tm = hdr[R.team_member]
    e = _tables(client, db, tm, ["marketing.clicks"])
    assert client.get(e["download_path"], headers=tm).status_code == 200
    # the team member becomes an accountant: still an exporter, but marketing is now locked
    db.query(Membership).filter(Membership.user_id == users[R.team_member].id).update(
        {Membership.role: R.accountant}
    )
    db.commit()
    assert client.get(e["download_path"], headers=tm).status_code == 403
    # as a mentor they have no exports at all
    db.query(Membership).filter(Membership.user_id == users[R.team_member].id).update(
        {Membership.role: R.mentor}
    )
    db.commit()
    assert client.get(e["download_path"], headers=tm).status_code == 403


def test_render_fails_when_requester_lost_access(client, db, team):
    s, hdr, users = team
    r = client.post(
        f"{BASE}/exports/data-tables",
        headers=hdr[R.team_member],
        json={"metrics": ["marketing.clicks"], "format": "xlsx"},
    ).json()["data"]
    db.query(Membership).filter(Membership.user_id == users[R.team_member].id).update(
        {Membership.status: MembershipStatus.removed}
    )
    db.commit()
    _work(db)
    row = db.get(AnalyticsExport, uuid.UUID(r["id"]))
    assert row.status == "failed" and "no longer" in row.error and row.file_key is None
    run = db.get(AnalyticsReportRun, row.run_id)
    assert run.status == "pending"  # never resolved for someone who lost access


def test_double_export_shares_one_pending_run(client, db, team):
    fo = team[1][R.founder]
    rep = _report(client, fo, [REVENUE])
    a = client.post(f"{BASE}/reports/{rep['id']}/export", headers=fo, json={"format": "pdf"})
    b = client.post(f"{BASE}/reports/{rep['id']}/export", headers=fo, json={"format": "xlsx"})
    assert a.json()["data"]["run_id"] == b.json()["data"]["run_id"]
    _work(db)
    for x in (a, b):
        d = client.get(f"{BASE}/exports/{x.json()['data']['id']}", headers=fo).json()["data"]
        assert d["status"] == "ready"


def test_handlers_and_daily_purge_are_wired(db):
    assert (
        exp.RENDER_JOB in runner.JOB_HANDLERS and sched.SCHED_ANALYTICS_PURGE in runner.JOB_HANDLERS
    )
    due = sched._due_analytics_purge(db, datetime.now(UTC))
    assert [d.job_type for d in due] == [sched.SCHED_ANALYTICS_PURGE]
