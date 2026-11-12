"""Live Analytics & Reports slice A: catalog, Overview, By hub, batch resolve, custom metrics.

A founder onboards and invites an accountant, a mentor and an investor while onboarding is a draft.
The workspace is then walked: empty Overview (honest `no_data`), a ledger plus marketing metrics,
the Overview with a previous-period comparison, a founder-entered metric, per-metric authorisation
(the accountant gets finance only; a locked metric carries no value) and the role denials.

Every response body is captured to `e2e/_captures/analytics_slice_a/*.json`, the verbatim source
for `docs/fe-integration-guide-analytics.md`. All money is in minor units (kobo).
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


def _txn(c, wh, direction, amount, days_ago=0, category=None):
    day = (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()
    body = {
        "date": day,
        "description": f"e2e {direction}",
        "amount_minor": amount,
        "currency": "NGN",
        "direction": direction,
    }
    if category:
        body["category"] = category
    r = c.post("/api/v1/finance/transactions", headers=wh, json=body)
    assert r.status_code == 200, r.text


def test_analytics_slice_a_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
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
            accepted = c.post("/api/v1/invitations/accept", json={"token": token}, headers=ra)
            assert accepted.status_code == 200
            who[role] = ra
        completed = c.post("/api/v1/onboarding/complete", headers=auth)
        assert completed.status_code == 200
        fo = _wh(c, auth)
        tm, ac, me, iv = (
            _wh(c, who[r]) for r in ("team_member", "accountant", "mentor", "investor")
        )

        # 1. Catalog: founder sees every hub; the accountant sees finance open, the rest locked with no metrics.
        r = c.get(f"{B}/metrics-catalog", headers=fo)
        assert r.status_code == 200, r.text
        capture("analytics_slice_a", "catalog_founder", r)
        r = c.get(f"{B}/metrics-catalog", headers=ac)
        capture("analytics_slice_a", "catalog_accountant", r)
        hubs = {h["hub"]: h["locked"] for h in r.json()["data"]["hubs"]}
        assert hubs["finance"] is False and hubs["sales"] is True

        # 1b. Team member sees the same hubs as the founder (live evidence for the guide).
        r = c.get(f"{B}/metrics-catalog", headers=tm)
        capture("analytics_slice_a", "catalog_team_member", r)
        assert r.json()["data"] == c.get(f"{B}/metrics-catalog", headers=fo).json()["data"]

        # 2. Overview on an empty workspace: no_data, never a fabricated zero.
        r = c.get(f"{B}/overview", headers=fo, params={"range": "7d"})
        assert r.status_code == 200, r.text
        capture("analytics_slice_a", "overview_empty", r)
        assert all(
            card["status"] == "no_data" and card["value"] is None
            for card in r.json()["data"]["cards"]
        )

        # 3. Ledger (old raise, funding top-up, a previous-period month, this period) + marketing.
        _txn(c, fo, "in", 60_000_000, days_ago=200)
        _txn(c, fo, "in", 9_000_000, days_ago=2, category="Funding")
        _txn(c, fo, "in", 100_000, days_ago=40)
        _txn(c, fo, "out", 300_000, days_ago=41)
        _txn(c, fo, "in", 500_000, days_ago=3)
        _txn(c, fo, "out", 200_000, days_ago=4)
        today = datetime.now(UTC).date().isoformat()
        ingested = c.post(
            "/api/v1/marketing/metrics",
            headers=fo,
            json={
                "points": [
                    {"ts": today, "metric": "impressions", "value": 2000},
                    {"ts": today, "metric": "clicks", "value": 50},
                ]
            },
        )
        assert ingested.status_code == 200
        r = c.get(f"{B}/overview", headers=fo, params={"range": "30d", "compare": "previous"})
        capture("analytics_slice_a", "overview_founder", r)
        cards = {x["key"]: x for x in r.json()["data"]["cards"]}
        assert cards["finance.revenue"]["value"] == 500_000  # funding is cash, not revenue
        assert cards["finance.revenue"]["compare"]["value"] == 100_000

        r = c.get(f"{B}/overview", headers=tm, params={"range": "30d", "compare": "previous"})
        capture("analytics_slice_a", "overview_team_member", r)
        assert [x["key"] for x in r.json()["data"]["cards"]] == [
            x["key"]
            for x in c.get(
                f"{B}/overview", headers=fo, params={"range": "30d", "compare": "previous"}
            ).json()["data"]["cards"]
        ]
        r = c.post(f"{B}/custom-metrics", headers=tm, json={"label": "TM metric", "unit": "count"})
        capture("analytics_slice_a", "custom_create_team_member", r)
        assert r.status_code == 201

        # 4. Accountant: finance cards only, no sales number anywhere.
        r = c.get(f"{B}/overview", headers=ac)
        capture("analytics_slice_a", "overview_accountant", r)
        assert {x["hub"] for x in r.json()["data"]["cards"]} == {"finance"}
        r = c.get(f"{B}/by-hub", headers=fo, params={"hub": "marketing"})
        capture("analytics_slice_a", "by_hub_marketing", r)
        assert r.status_code == 200
        r = c.get(f"{B}/by-hub", headers=ac, params={"hub": "sales"})
        capture("analytics_slice_a", "by_hub_sales_accountant_403", r)
        assert r.status_code == 403

        # 5. Batch resolve: a locked metric and a retired key.
        body = {
            "metrics": [
                {"key": "finance.revenue", "compare": "year", "series": True},
                {"key": "sales.pipeline_value"},
                {"key": "marketing.ctr"},
                {"key": "custom.gone"},
            ]
        }
        r = c.post(f"{B}/metrics/resolve", headers=ac, json=body)
        capture("analytics_slice_a", "resolve_accountant", r)
        got = {m["key"]: m for m in r.json()["data"]["metrics"]}
        assert (
            got["sales.pipeline_value"]["status"] == "locked"
            and "value" not in got["sales.pipeline_value"]
        )
        assert (
            got["marketing.ctr"]["status"] == "locked" and got["custom.gone"]["status"] == "unknown"
        )
        r = c.post(f"{B}/metrics/resolve", headers=fo, json=body)
        capture("analytics_slice_a", "resolve_founder", r)
        assert r.status_code == 200

        # 6. Custom metrics (founder-entered).
        r = c.post(
            f"{B}/custom-metrics", headers=fo, json={"label": "Active users", "unit": "count"}
        )
        assert r.status_code == 201, r.text
        capture("analytics_slice_a", "custom_create", r)
        mid = r.json()["data"]["id"]
        r = c.put(
            f"{B}/custom-metrics/{mid}/points",
            headers=fo,
            json={
                "points": [{"period": "2026-09", "value": 120}, {"period": "2026-10", "value": 180}]
            },
        )
        capture("analytics_slice_a", "custom_points", r)
        assert r.status_code == 200
        r = c.post(
            f"{B}/metrics/resolve",
            headers=fo,
            json={
                "metrics": [
                    {
                        "key": "custom.active_users",
                        "range": "custom",
                        "from": "2026-10-01",
                        "to": "2026-10-31",
                        "compare": "previous",
                        "series": True,
                    }
                ]
            },
        )
        capture("analytics_slice_a", "resolve_custom", r)
        r = c.get(f"{B}/custom-metrics", headers=fo)
        capture("analytics_slice_a", "custom_list", r)
        r = c.put(
            f"{B}/custom-metrics/{mid}/points",
            headers=fo,
            json={"points": [{"period": "2026-13", "value": 1}]},
        )
        capture("analytics_slice_a", "custom_points_422", r)
        assert r.status_code == 422
        r = c.get(f"{B}/custom-metrics", headers=ac)
        capture("analytics_slice_a", "custom_list_accountant_403", r)
        assert r.status_code == 403

        # 6b. A custom key is not an existence oracle for a role that cannot read custom metrics.
        r = c.post(
            f"{B}/metrics/resolve", headers=ac, json={"metrics": [{"key": "custom.active_users"}]}
        )
        capture("analytics_slice_a", "resolve_custom_accountant", r)
        assert r.json()["data"]["metrics"][0] == {
            "key": "custom.active_users",
            "status": "unknown",
            "message": "Metric no longer available.",
        }

        # 7. Errors and role denials.
        r = c.get(f"{B}/overview", headers=fo, params={"range": "bogus"})
        capture("analytics_slice_a", "overview_bad_range_422", r)
        assert r.status_code == 422
        r = c.get(
            f"{B}/overview",
            headers=fo,
            params={"range": "custom", "from": "2020-01-01", "to": "2026-01-01"},
        )
        capture("analytics_slice_a", "overview_too_long_422", r)
        assert r.status_code == 422
        r = c.get(
            f"{B}/overview",
            headers=fo,
            params={"range": "custom", "from": "9998-01-01", "to": "9999-12-31", "compare": "year"},
        )
        capture("analytics_slice_a", "overview_far_future_422", r)
        assert r.status_code == 422
        for name, h in (("mentor", me), ("investor", iv)):
            r = c.get(f"{B}/overview", headers=h)
            capture("analytics_slice_a", f"overview_{name}_403", r)
            assert r.status_code == 403
