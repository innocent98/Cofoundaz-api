"""Live Journal PRD-gap journey (J1 to J15): a founder writes several entries, reads them by
date, month and search, sees stats, mood annotations and insights, dismisses the prompt, browses
the library, turns on a passcode and proves the lock (423, wrong code, unlock, lock, remove),
runs a milestone retro, exports and permanently deletes the journal, then gets the opt-in
evening reminder from the scheduler.

Every body is captured to `e2e/_captures/journal_gaps/*.json`: the verbatim source for
`docs/fe-integration-guide-journal.md`. Unlock tokens are REDACTED before capture (GitGuardian
flags signed tokens); the journal text in the export is made-up journey text.
"""

import json
from datetime import UTC, date, datetime, timedelta

import httpx

PASSCODE = "4821"
REDACTED = "<redacted-unlock-token>"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import scheduled as _s  # noqa: F401  (registers the nudge handler)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _tick(now: datetime) -> int:
    from app.db.session import SessionLocal
    from app.services.notifications import registry
    from app.worker.scheduler import scheduler_tick

    registry.register()
    db = SessionLocal()
    try:
        return scheduler_tick(db, now=now)
    finally:
        db.close()


def _redacted(resp: httpx.Response) -> dict:
    body = resp.json()
    if isinstance(body.get("data"), dict) and "token" in body["data"]:
        body["data"]["token"] = REDACTED
    return body


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get first customers"]}
    )
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200


def test_journal_gaps_journey(base_url, make_verified_user, capture, capture_json):
    g = "journal_gaps"
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(access)
        _onboard(c, auth)
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}
        j = "/api/v1/journal"
        today = date.today()

        # 1. Fresh journal: settings, empty stats, no entry for today.
        r = c.get(f"{j}/settings", headers=wh)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["passcode_enabled"] is False
        capture(g, "settings_initial", r)
        r = c.get(f"{j}/stats", headers=wh)
        assert r.json()["data"]["total_entries"] == 0
        capture(g, "stats_empty", r)
        r = c.get(f"{j}/entries/by-date/{today.isoformat()}", headers=wh)
        assert r.status_code == 404
        capture(g, "entry_by_date_missing", r)

        # 2. Six entries across the last days (mood and stress vary).
        moods = ["rough", "meh", "okay", "good", "good", "great"]
        for i, mood in enumerate(moods):
            day = today - timedelta(days=len(moods) - 1 - i)
            r = c.post(
                f"{j}/entries",
                headers=wh,
                json={
                    "date": day.isoformat(),
                    "content": f"Day {i + 1} first line\nSecond line about pricing and runway.",
                    "mood": mood,
                    "stress": 9 - i,
                },
            )
            assert r.status_code == 200, r.text
        r = c.get(f"{j}/entries/by-date/{today.isoformat()}", headers=wh)
        assert r.status_code == 200
        capture(g, "entry_by_date", r)
        r = c.get(f"{j}/entries?limit=3", headers=wh)
        assert r.json()["data"]["total"] == 6
        assert {"stress", "preview", "word_count"} <= set(r.json()["data"]["entries"][0])
        capture(g, "entries_list", r)
        r = c.get(f"{j}/entries/months", headers=wh)
        assert r.status_code == 200
        capture(g, "entries_months", r)
        month = today.strftime("%Y-%m")
        r = c.get(f"{j}/entries?month={month}&search=Day%203", headers=wh)
        assert r.status_code == 200
        capture(g, "entries_month_search", r)
        r = c.get(f"{j}/entries?month=2026-13", headers=wh)
        assert r.status_code == 422
        capture(g, "entries_month_invalid", r)

        # 3. A completed milestone shows up as a mood annotation and a retro offer.
        tree = c.get("/api/v1/roadmap", headers=wh).json()["data"]
        ms = [m for p in tree["phases"] for m in p["milestones"]]
        done = c.patch(
            f"/api/v1/roadmap/milestones/{ms[0]['id']}", headers=wh, json={"status": "done"}
        )
        assert done.status_code == 200, done.text
        r = c.get(f"{j}/mood?range=4w", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["annotations"]
        capture(g, "mood_range", r)
        r = c.get(f"{j}/stats?today={today.isoformat()}", headers=wh)
        assert r.json()["data"]["total_entries"] == 6
        capture(g, "stats", r)
        r = c.get(f"{j}/insights?today={today.isoformat()}", headers=wh)
        assert r.status_code == 200
        capture(g, "insights", r)
        r = c.post(f"{j}/insights/supportive/dismiss", headers=wh)
        assert r.status_code == 200
        capture(g, "supportive_dismissed", r)

        # 4. Prompt: dismiss, library.
        r = c.get(f"{j}/prompts/today", headers=wh)
        assert r.json()["data"]["dismissed"] is False
        capture(g, "prompt_today", r)
        r = c.post(f"{j}/prompts/today/dismiss", headers=wh)
        assert r.status_code == 200
        capture(g, "prompt_dismissed", r)
        r = c.get(f"{j}/prompts/today", headers=wh)
        assert r.json()["data"]["dismissed"] is True
        capture(g, "prompt_today_after_dismiss", r)
        r = c.get(f"{j}/prompts/library?theme=wins", headers=wh)
        assert r.status_code == 200
        capture(g, "prompt_library_theme", r)

        # 5. Settings.
        r = c.put(f"{j}/settings", headers=wh, json={"daily_prompt_notifications": True})
        assert r.json()["data"]["daily_prompt_notifications"] is True
        capture(g, "settings_updated", r)
        r = c.put(f"{j}/settings", headers=wh, json={"unknown": True})
        assert r.status_code == 422
        capture(g, "settings_unknown_field", r)

        # 6. Passcode lock.
        r = c.put(f"{j}/passcode", headers=wh, json={"passcode": "12"})
        assert r.status_code == 422
        capture(g, "passcode_too_short", r)
        r = c.put(f"{j}/passcode", headers=wh, json={"passcode": PASSCODE})
        assert r.status_code == 200
        capture_json(g, "passcode_set", _redacted(r))
        r = c.get(f"{j}/entries", headers=wh)
        assert r.status_code == 423 and r.json()["error"]["code"] == "JOURNAL_LOCKED"
        capture(g, "locked_entries", r)
        r = c.get(f"{j}/settings", headers=wh)
        assert r.json()["data"]["passcode_enabled"] is True
        capture(g, "settings_locked", r)
        r = c.post(f"{j}/unlock", headers=wh, json={"passcode": "0000"})
        assert r.status_code == 403
        capture(g, "unlock_wrong", r)
        r = c.post(f"{j}/unlock", headers=wh, json={"passcode": PASSCODE})
        assert r.status_code == 200
        token = r.json()["data"]["token"]
        capture_json(g, "unlock_ok", _redacted(r))
        th = {**wh, "X-Journal-Token": token}
        assert c.get(f"{j}/entries", headers=th).status_code == 200
        r = c.post(f"{j}/lock", headers=wh)
        assert r.json()["data"] == {"locked": True}
        capture(g, "lock", r)
        assert c.get(f"{j}/entries", headers=th).status_code == 423
        r = c.request("DELETE", f"{j}/passcode", headers=wh, json={"password": "wrong-password"})
        assert r.status_code == 403
        capture(g, "passcode_remove_wrong_password", r)
        r = c.request("DELETE", f"{j}/passcode", headers=wh, json={"password": u["password"]})
        assert r.status_code == 200 and r.json()["data"]["passcode_enabled"] is False
        capture(g, "passcode_removed", r)

        # 7. Retro: offer, run, conflict, list, update, delete.
        r = c.get(f"{j}/retros/offers", headers=wh)
        assert r.status_code == 200 and r.json()["data"]["offers"]
        capture(g, "retro_offers", r)
        body = {
            "milestone_id": ms[0]["id"],
            "what_worked": "Talking to users first",
            "what_didnt": "Pricing guesses",
            "what_change": "Test price earlier",
        }
        r = c.post(f"{j}/retros", headers=wh, json=body)
        assert r.status_code == 201, r.text
        retro_id = r.json()["data"]["id"]
        capture(g, "retro_created", r)
        r = c.post(f"{j}/retros", headers=wh, json=body)
        assert r.status_code == 409
        capture(g, "retro_exists", r)
        r = c.get(f"{j}/retros", headers=wh)
        assert len(r.json()["data"]["retros"]) == 1
        capture(g, "retros_list", r)
        r = c.put(
            f"{j}/retros/{retro_id}",
            headers=wh,
            json={"what_worked": "a", "what_didnt": "b", "what_change": "c"},
        )
        assert r.status_code == 200
        capture(g, "retro_updated", r)
        r = c.post(f"{j}/retros", headers=wh, json={**body, "milestone_id": ms[1]["id"]})
        assert r.status_code == 409 and r.json()["error"]["code"] == "MILESTONE_NOT_DONE"
        capture(g, "retro_milestone_not_done", r)

        # 8. Export, then permanent delete.
        r = c.get(f"{j}/export", headers=wh)
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        exported = json.loads(r.text)
        assert len(exported["data"]["entries"]) == 6
        capture_json(g, "export", exported)
        capture_json(g, "export_headers", dict(r.headers))
        r = c.request("DELETE", j, headers=wh, json={"confirm": "delete"})
        assert r.status_code == 422
        capture(g, "delete_wrong_confirm", r)
        r = c.request("DELETE", j, headers=wh, json={"confirm": "DELETE"})
        assert r.status_code == 200
        capture(g, "delete_all", r)
        r = c.get(f"{j}/entries", headers=wh)
        assert r.json()["data"]["total"] == 0
        capture(g, "entries_after_delete", r)

        # 9. Opt-in evening reminder: the scheduler fires it, the bell shows it.
        assert (
            c.put(f"{j}/settings", headers=wh, json={"evening_reminder": True}).status_code == 200
        )
        _tick(datetime.now(UTC).replace(hour=22, minute=0, second=0, microsecond=0))
        _drain()
        notes = c.get("/api/v1/notifications", headers=wh)
        assert notes.status_code == 200, notes.text
        types = [n["type"] for n in notes.json()["data"]["notifications"]]
        assert "journal.reminder" in types, types
        capture(g, "notifications_after_reminder", notes)
