"""Live Analytics & Reports slice C: exports.

A founder invites a team member, an accountant, a mentor and an investor, seeds a ledger and
marketing clicks, builds a report, exports it as PDF and XLSX (the in-process worker drains the
render job), downloads the files, lets the accountant export the founder's finished run (redacted
to finance), exports data tables as CSV and XLSX, and walks the error paths: 409 while pending,
403 for the wrong role, 404 for someone else's export, 422s, and 410 once expired (expiry forced
by back-dating `expires_at`; the purge then runs in-process). Every body is captured to
`e2e/_captures/analytics_exports/*.json`, the verbatim source for
`docs/fe-integration-guide-analytics-exports.md`.
"""

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import httpx

B = "/api/v1/analytics"
G = "analytics_exports"
CLICKS = 4321  # distinctive, so "not in the accountant's file" is a real check


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _signup_verify_login(c, mailbox, email, password):
    c.post("/api/v1/auth/signup", json={"email": email, "password": password})
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    return _auth(
        c.post("/api/v1/auth/login", json={"email": email, "password": password}).json()["data"][
            "access_token"
        ]
    )


def _wh(c, auth):
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _txn(c, wh, direction, amount, days_ago=0):
    day = (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()
    r = c.post(
        "/api/v1/finance/transactions",
        headers=wh,
        json={
            "date": day,
            "description": f"e2e {direction}",
            "amount_minor": amount,
            "currency": "NGN",
            "direction": direction,
        },
    )
    assert r.status_code == 200, r.text


def _drain() -> None:
    """Run queued jobs in-process, like the worker. The worker also subscribes the notification
    registry to the event bus (app/worker/__main__.py); do the same once, so "export ready" is
    delivered by the drain exactly as it is in production (`register` is idempotent)."""
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register
    from app.worker import runner
    from app.worker.handlers import analytics as _a  # noqa: F401

    register()

    db = SessionLocal()
    try:
        for _ in range(50):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _file_meta(r: httpx.Response, text: str | None = None) -> dict:
    """What the FE sees of a file response: status, the headers that matter, size, a peek."""
    out = {
        "status_code": r.status_code,
        "headers": {
            k: r.headers.get(k)
            for k in (
                "content-type",
                "content-disposition",
                "cache-control",
                "x-content-type-options",
                "content-security-policy",
            )
        },
        "size_bytes": len(r.content),
        "first_bytes": r.content[:8].decode("latin-1"),
    }
    if text is not None:
        out["text"] = text.splitlines()
    return out


def _xlsx_xml(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xml"))


def _ready(c, wh, export_id, capture, name):
    _drain()
    r = c.get(f"{B}/exports/{export_id}", headers=wh)
    assert r.status_code == 200 and r.json()["data"]["status"] == "ready", r.text
    capture(G, name, r)
    return r.json()["data"]


def test_analytics_exports_journey(  # noqa: PLR0915 - one journey, read top to bottom
    base_url, make_verified_user, mailbox, unique_email, capture, capture_json
):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
        )
        c.patch(
            "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow revenue"]}
        )
        roles = {
            "team_member": unique_email("tm"),
            "accountant": unique_email("acct"),
            "mentor": unique_email("mentor"),
            "investor": unique_email("inv"),
        }
        inv = c.post(
            "/api/v1/onboarding/invites",
            headers=auth,
            json={"invites": [{"email": e, "role": r} for r, e in roles.items()]},
        )
        assert inv.status_code == 200, inv.text
        who = {}
        for role, email in roles.items():
            token = mailbox.latest_token_for(email, subject_contains="invited")
            ra = _signup_verify_login(c, mailbox, email, f"An-{role}-{email[:6]}-9")
            assert (
                c.post("/api/v1/invitations/accept", json={"token": token}, headers=ra).status_code
                == 200
            )
            who[role] = ra
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        fo = _wh(c, auth)
        tm, ac, me, iv = (
            _wh(c, who[r]) for r in ("team_member", "accountant", "mentor", "investor")
        )

        _txn(c, fo, "in", 500_000, days_ago=3)
        _txn(c, fo, "out", 200_000, days_ago=4)
        today = datetime.now(UTC).date().isoformat()
        r = c.post(
            "/api/v1/marketing/metrics",
            headers=fo,
            json={"points": [{"ts": today, "metric": "clicks", "value": CLICKS}]},
        )
        assert r.status_code == 200, r.text
        blocks = [
            {"type": "metric", "metric": "finance.revenue", "viz": "line"},
            {"type": "metric", "metric": "marketing.clicks", "viz": "number"},
            {"type": "text", "title": "Note", "text": "Clicks are paid plus organic."},
        ]
        r = c.post(
            f"{B}/reports",
            headers=fo,
            json={"name": "Board pack", "description": "For the board", "blocks": blocks},
        )
        assert r.status_code == 201, r.text
        rid = r.json()["data"]["id"]

        # 1. Export history starts empty.
        r = c.get(f"{B}/exports", headers=fo)
        capture(G, "list_empty", r)
        assert r.json()["data"]["total"] == 0

        # 2. Export the report as PDF: 202 + a pending row; download is 409 until the job runs.
        r = c.post(f"{B}/reports/{rid}/export", headers=fo, json={"format": "pdf"})
        capture(G, "report_pdf_start", r)
        assert r.status_code == 202, r.text
        pdf_id = r.json()["data"]["id"]
        r = c.get(f"{B}/exports/{pdf_id}/download", headers=fo)
        capture(G, "download_pending_409", r)
        assert r.status_code == 409
        pdf = _ready(c, fo, pdf_id, capture, "report_pdf_ready")
        r = c.get(pdf["download_path"], headers=fo)
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        capture_json(G, "download_pdf", _file_meta(r))

        # 3. The same report as XLSX (a second run, frozen again).
        r = c.post(f"{B}/reports/{rid}/export", headers=fo, json={"format": "xlsx"})
        capture(G, "report_xlsx_start", r)
        xlsx = _ready(c, fo, r.json()["data"]["id"], capture, "report_xlsx_ready")
        r = c.get(xlsx["download_path"], headers=fo)
        assert r.status_code == 200 and str(CLICKS) in _xlsx_xml(r.content)
        capture_json(G, "download_xlsx", _file_meta(r))

        # 4. Bad formats.
        r = c.post(f"{B}/reports/{rid}/export", headers=fo, json={"format": "csv"})
        capture(G, "report_csv_422", r)
        assert r.status_code == 422

        # 5. The accountant: cannot start a run; exports the founder's finished run, finance only.
        r = c.post(f"{B}/reports/{rid}/run", headers=fo)
        run_id = r.json()["data"]["run_id"]
        _drain()
        cand = {
            x["role"]: x["user_id"]
            for x in c.get(f"{B}/reports/{rid}/shares", headers=fo).json()["data"]["candidates"]
        }
        r = c.put(
            f"{B}/reports/{rid}/shares",
            headers=fo,
            json={"user_ids": [cand["accountant"], cand["mentor"]]},
        )
        assert r.status_code == 200, r.text
        r = c.post(f"{B}/reports/{rid}/export", headers=ac, json={"format": "pdf"})
        capture(G, "accountant_new_run_403", r)
        assert r.status_code == 403
        r = c.post(
            f"{B}/reports/{rid}/export", headers=ac, json={"format": "xlsx", "run_id": run_id}
        )
        capture(G, "accountant_run_export_start", r)
        assert r.status_code == 202, r.text
        acx = _ready(c, ac, r.json()["data"]["id"], capture, "accountant_run_export_ready")
        r = c.get(acx["download_path"], headers=ac)
        xml = _xlsx_xml(r.content)
        assert r.status_code == 200 and str(CLICKS) not in xml
        assert "Locked: Marketing access needed" in xml and "5000.00" in xml
        capture_json(G, "download_accountant_xlsx", _file_meta(r))

        # 6. The mentor can read the shared report but never gets a file; the investor nothing.
        r = c.post(
            f"{B}/reports/{rid}/export", headers=me, json={"format": "pdf", "run_id": run_id}
        )
        capture(G, "mentor_export_403", r)
        assert r.status_code == 403
        r = c.get(f"{B}/exports", headers=iv)
        capture(G, "list_investor_403", r)
        assert r.status_code == 403

        # 7. Data tables: CSV (one table) and XLSX (sheets), with the 422 paths.
        r = c.post(
            f"{B}/exports/data-tables",
            headers=fo,
            json={
                "metrics": ["finance.revenue", "marketing.clicks"],
                "range": {"range": "30d"},
                "format": "csv",
                "include_metadata": True,
            },
        )
        capture(G, "tables_csv_start", r)
        assert r.status_code == 202, r.text
        csv_e = _ready(c, fo, r.json()["data"]["id"], capture, "tables_csv_ready")
        r = c.get(csv_e["download_path"], headers=fo)
        assert r.status_code == 200
        capture_json(G, "download_csv", _file_meta(r, r.content.decode("utf-8-sig")))
        r = c.post(
            f"{B}/exports/data-tables",
            headers=tm,
            json={"metrics": ["marketing.clicks"], "format": "xlsx"},
        )
        capture(G, "tables_xlsx_start_team_member", r)
        tmx = _ready(c, tm, r.json()["data"]["id"], capture, "tables_xlsx_ready_team_member")
        for name, h, body in (
            ("tables_unknown_metric_422", fo, {"metrics": ["nope.nope"], "format": "csv"}),
            (
                "tables_accountant_marketing_422",
                ac,
                {"metrics": ["marketing.clicks"], "format": "csv"},
            ),
            ("tables_pdf_422", fo, {"metrics": ["finance.revenue"], "format": "pdf"}),
        ):
            r = c.post(f"{B}/exports/data-tables", headers=h, json=body)
            capture(G, name, r)
            assert r.status_code == 422, (name, r.text)

        # 8. Who sees what in history: founder all, team member and accountant their own.
        r = c.get(f"{B}/exports", headers=fo)
        capture(G, "list_founder", r)
        assert r.json()["data"]["total"] == 5
        r = c.get(f"{B}/exports", headers=tm, params={"artifact_type": "data_tables"})
        capture(G, "list_team_member", r)
        assert [x["id"] for x in r.json()["data"]["exports"]] == [tmx["id"]]
        r = c.get(f"{B}/exports/{pdf_id}", headers=tm)
        capture(G, "get_not_yours_404", r)
        assert r.status_code == 404
        r = c.get(f"{B}/exports/{pdf_id}/download", headers=ac)
        assert r.status_code == 404

        # 9. Notification to the requester only.
        r = c.get("/api/v1/notifications", headers=fo)
        capture(G, "notifications_founder", r)
        titles = [n["title"] for n in r.json()["data"]["notifications"]]
        assert "Your export is ready: Board pack" in titles

        # 10. Expiry: back-date one export (no 30-day wait), download is 410; purge removes the file.
        from app.db.models.analytics import AnalyticsExport
        from app.db.session import SessionLocal
        from app.services.analytics import exports as exp

        db = SessionLocal()
        try:
            row = db.get(AnalyticsExport, uuid.UUID(pdf_id))
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()
            r = c.get(f"{B}/exports/{pdf_id}/download", headers=fo)
            capture(G, "download_expired_410", r)
            assert r.status_code == 410
            purged = exp.purge(db)
            db.commit()
            assert purged["exports_purged"] >= 1
        finally:
            db.close()
        r = c.get(f"{B}/exports/{pdf_id}", headers=fo)
        capture(G, "get_expired", r)
        assert r.json()["data"]["status"] == "expired" and r.json()["data"]["download_path"] is None
