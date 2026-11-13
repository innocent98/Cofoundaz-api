"""Live Analytics & Reports slice B: reports, templates, preview, optimistic lock, frozen runs, shares.

A founder invites a team member, an accountant, a mentor and an investor, seeds a ledger and
marketing metrics, builds a report from a template, previews and saves it, loses a stale save to
a 409, runs it (the in-process worker drains the job), shares it, and each role reads it through
its own access. Every body is captured to `e2e/_captures/analytics_reports/*.json`, the verbatim
source for `docs/fe-integration-guide-analytics-reports.md`.
"""

from datetime import UTC, datetime, timedelta

import httpx

B = "/api/v1/analytics"


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
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import analytics as _a  # noqa: F401

    db = SessionLocal()
    try:
        for _ in range(50):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _run(c, wh, rid, capture, name):
    r = c.post(f"{B}/reports/{rid}/run", headers=wh)
    assert r.status_code == 202, r.text
    capture("analytics_reports", f"{name}_run_start", r)
    _drain()
    run_id = r.json()["data"]["run_id"]
    got = c.get(f"{B}/reports/{rid}/runs/{run_id}", headers=wh)
    assert got.status_code == 200 and got.json()["data"]["status"] == "ready", got.text
    capture("analytics_reports", f"{name}_run", got)
    return got.json()["data"]


def test_analytics_reports_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
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
        assert (
            c.post(
                "/api/v1/marketing/metrics",
                headers=fo,
                json={"points": [{"ts": today, "metric": "clicks", "value": 50}]},
            ).status_code
            == 200
        )

        # 1. Templates (the three chips) and an empty list.
        r = c.get(f"{B}/reports/templates", headers=fo)
        assert r.status_code == 200, r.text
        capture("analytics_reports", "templates", r)
        r = c.get(f"{B}/reports", headers=fo)
        capture("analytics_reports", "list_empty", r)
        assert r.json()["data"]["total"] == 0

        # 2. Preview (stateless), then save from a template and with own blocks.
        blocks = [
            {"type": "metric", "metric": "finance.revenue", "viz": "line"},
            {"type": "metric", "metric": "marketing.clicks", "viz": "number"},
            {"type": "text", "title": "Note", "text": "Clicks are paid plus organic."},
        ]
        r = c.post(f"{B}/reports/preview", headers=fo, json={"blocks": blocks})
        capture("analytics_reports", "preview", r)
        assert r.status_code == 200 and r.json()["data"]["results"][0]["result"]["value"] == 500_000
        r = c.post(f"{B}/reports", headers=fo, json={"template_key": "monthly_investor"})
        capture("analytics_reports", "create_from_template", r)
        assert r.status_code == 201, r.text
        r = c.post(
            f"{B}/reports",
            headers=fo,
            json={"name": "Board pack", "description": "For the board", "blocks": blocks},
        )
        capture("analytics_reports", "create", r)
        rep = r.json()["data"]
        rid = rep["id"]
        r = c.post(f"{B}/reports", headers=fo, json={"name": "<b>x</b>", "blocks": []})
        capture("analytics_reports", "create_html_422", r)
        assert r.status_code == 422
        r = c.post(
            f"{B}/reports",
            headers=fo,
            json={"name": "x", "blocks": [{"type": "metric", "metric": "nope"}]},
        )
        capture("analytics_reports", "create_unknown_metric_422", r)
        assert r.status_code == 422

        # 3. Optimistic lock.
        r = c.patch(f"{B}/reports/{rid}", headers=fo, json={"version": 1, "name": "Board pack Q4"})
        capture("analytics_reports", "patch", r)
        assert r.status_code == 200 and r.json()["data"]["version"] == 2
        r = c.patch(f"{B}/reports/{rid}", headers=fo, json={"version": 1, "name": "stale"})
        capture("analytics_reports", "patch_stale_409", r)
        assert r.status_code == 409

        # 4. Frozen run, then the data moves and the run does not.
        first = _run(c, fo, rid, capture, "founder")
        assert first["snapshot"]["results"][0]["result"]["value"] == 500_000
        _txn(c, fo, "in", 1_000, days_ago=1)
        again = c.get(f"{B}/reports/{rid}/runs/{first['id']}", headers=fo).json()["data"]
        assert again["snapshot"] == first["snapshot"]
        r = c.get(f"{B}/reports/{rid}/runs", headers=fo)
        capture("analytics_reports", "runs_list", r)

        # 5. Unshared: a 404 for the other roles; denied roles 403.
        for name, h in (("team_member", tm), ("accountant", ac), ("mentor", me)):
            r = c.get(f"{B}/reports/{rid}", headers=h)
            assert r.status_code == 404, name
        r = c.get(f"{B}/reports/{rid}", headers=tm)
        capture("analytics_reports", "get_unshared_404", r)
        r = c.get(f"{B}/reports", headers=iv)
        capture("analytics_reports", "list_investor_403", r)
        assert r.status_code == 403

        # 6. Share with the mentor and the accountant.
        r = c.get(f"{B}/reports/{rid}/shares", headers=fo)
        capture("analytics_reports", "shares_before", r)
        cand = {x["role"]: x["user_id"] for x in r.json()["data"]["candidates"]}
        r = c.put(
            f"{B}/reports/{rid}/shares",
            headers=fo,
            json={"user_ids": [cand["mentor"], cand["accountant"]]},
        )
        capture("analytics_reports", "shares_put", r)
        assert r.status_code == 200, r.text
        r = c.put(
            f"{B}/reports/{rid}/shares",
            headers=fo,
            json={"user_ids": [cand["team_member"], "00000000-0000-0000-0000-000000000000"]},
        )
        capture("analytics_reports", "shares_put_422", r)
        assert r.status_code == 422
        r = c.get(f"{B}/reports/{rid}/shares", headers=me)
        capture("analytics_reports", "shares_mentor_403", r)
        assert r.status_code == 403

        # 7. The mentor reads it: finance locked, marketing open, read-only.
        r = c.get(f"{B}/reports/{rid}", headers=me)
        capture("analytics_reports", "get_mentor", r)
        assert r.json()["data"]["blocks"][0]["locked"] is True
        r = c.get(f"{B}/reports", headers=me)
        capture("analytics_reports", "list_mentor", r)
        m_run = _run(c, me, rid, capture, "mentor")
        assert "500000" not in str(m_run)
        old = c.get(f"{B}/reports/{rid}/runs/{first['id']}", headers=me)
        capture("analytics_reports", "founder_run_as_mentor", old)
        assert "500000" not in old.text
        r = c.patch(f"{B}/reports/{rid}", headers=me, json={"version": 2, "name": "no"})
        capture("analytics_reports", "patch_mentor_403", r)
        assert r.status_code == 403
        r = c.post(f"{B}/reports/preview", headers=me, json={"blocks": blocks})
        capture("analytics_reports", "preview_mentor_403", r)
        assert r.status_code == 403

        # 8. The accountant reads it: finance open, the rest locked.
        a_run = _run(c, ac, rid, capture, "accountant")
        res = a_run["snapshot"]["results"]
        assert res[0]["result"]["value"] == 501_000 or res[0]["result"]["value"] == 500_000
        assert res[1]["locked"] is True

        # 9. Notifications (the mentor was told about the share).
        r = c.get("/api/v1/notifications", headers=me)
        if r.status_code == 200:
            capture("analytics_reports", "notifications_mentor", r)

        # 10. Share removed -> 404; duplicate; delete.
        c.put(f"{B}/reports/{rid}/shares", headers=fo, json={"user_ids": [cand["accountant"]]})
        assert c.get(f"{B}/reports/{rid}", headers=me).status_code == 404
        r = c.post(f"{B}/reports/{rid}/duplicate", headers=fo)
        capture("analytics_reports", "duplicate", r)
        assert r.status_code == 201
        r = c.delete(f"{B}/reports/{rid}", headers=fo)
        capture("analytics_reports", "delete", r)
        assert r.status_code == 200
        r = c.get(f"{B}/reports/{rid}", headers=fo)
        capture("analytics_reports", "get_deleted_404", r)
        assert r.status_code == 404
