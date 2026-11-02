"""Live Marketing re-walk BE fixes: overview idea count, plan-week slots, item links in search,
stale content-gap runs, and the media file route.

Stub model. Captures go to `e2e/_captures/marketing_rewalk_be/*.json`, the verbatim source for the
"Re-walk fixes" section of `docs/fe-integration-guide-marketing-prd.md`.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

G = "marketing_rewalk_be"
M = "/api/v1/marketing"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import marketing_ai  # noqa: F401  (registers ai.marketing.*)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_marketing_rewalk_be_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        # 1. Gap 5: search hits link to the item.
        camp = c.post(
            f"{M}/campaigns", headers=wh, json={"name": "Zeppelin push", "objective": "launch"}
        )
        cid = camp.json()["data"]["id"]
        post = c.post(
            f"{M}/calendar-entries",
            headers=wh,
            json={"title": "Zeppelin teaser", "channel": "email"},
        )
        eid = post.json()["data"]["id"]
        seg = c.post(f"{M}/segments", headers=wh, json={"name": "Zeppelin fans", "definition": {}})
        assert seg.status_code == 200, seg.text
        s = c.get("/api/v1/search", params={"q": "Zeppelin"}, headers=wh)
        by = {x["type"]: x["href"] for x in s.json()["data"]["results"]}
        assert by["campaign"] == f"/marketing/campaigns/{cid}"
        assert by["post"] == f"/marketing/calendar?entry={eid}"
        assert by["segment"] == "/marketing/segments"
        capture(G, "search_zeppelin", s)

        # 2. Gap 4: plan-week proposals carry a future scheduled_at.
        plan = c.post(f"{M}/calendar/plan-week", headers=wh, json={})
        pid = plan.json()["data"]["id"]
        _drain()
        ready = c.get(f"{M}/calendar/plan-week/{pid}", headers=wh)
        assert ready.json()["data"]["status"] == "ready", ready.text
        capture(G, "plan_week_ready", ready)
        now = datetime.now(UTC)
        for e in ready.json()["data"]["output"]["entries"]:
            assert datetime.fromisoformat(e["scheduled_at"]) > now

        # 3. Gaps 1 + 9: two content-gap runs; the overview counts the newest only; changing the
        #    positioning makes the run stale.
        counts = []
        for _ in range(2):
            g = c.post(f"{M}/seo/content-gaps/generate", headers=wh)
            assert g.status_code == 202, g.text
            _drain()
            counts.append(c.get(M, headers=wh).json()["data"]["ai_content_ideas"])
        runs = c.get(f"{M}/seo/content-gaps", headers=wh)
        newest = runs.json()["data"]["generations"][0]
        assert counts[1] == len(newest["output"]["gaps"]) == counts[0]
        assert newest["stale"] is False
        capture(G, "content_gaps_fresh", runs)
        capture(G, "overview_after_two_runs", c.get(M, headers=wh))
        put = c.put(f"{M}/positioning", headers=wh, json={"audience": "Market traders in Kano"})
        assert put.status_code == 200, put.text
        stale = c.get(f"{M}/seo/content-gaps", headers=wh)
        assert stale.json()["data"]["generations"][0]["stale"] is True
        capture(G, "content_gaps_stale", stale)

        # 4. Gap 7: the image can be fetched with the token.
        up = c.post(f"{M}/media", headers=wh, files={"file": ("rewalk.png", PNG, "image/png")})
        mid = up.json()["data"]["id"]
        f = c.get(f"{M}/media/{mid}/file", headers=wh)
        assert f.status_code == 200 and f.content == PNG
        assert f.headers["content-type"] == "image/png"
        capture(G, "media_upload", up)
        (Path(__file__).parent / "_captures" / G / "media_file_headers.json").write_text(
            json.dumps(
                {
                    "status": f.status_code,
                    "content-type": f.headers["content-type"],
                    "cache-control": f.headers["cache-control"],
                    "x-content-type-options": f.headers["x-content-type-options"],
                    "bytes": len(f.content),
                },
                indent=2,
            )
            + "\n"
        )
        assert c.get(f"{M}/media/{mid}/file").status_code in (401, 403)
