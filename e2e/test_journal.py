"""Live Founder Journal journey: a founder onboards, writes today's entry,
re-saves it (the autosave upsert), reads it back, lists and searches their own
entries, reads the mood trend and today's prompt, then deletes the entry.

Every response body along the way is captured to `e2e/_captures/journal/*.json`
-- those files are the verbatim source for `docs/fe-integration-guide-journal.md`.
They must be REAL bodies from this live run, complete and untrimmed.

The journal is founder-only and author-only, so this journey needs nothing but a
freshly onboarded founder: no roadmap, no assessment, no mission.

Module 03 deferred AI upgrade: today's prompt starts static (`get_or_create_today_prompt`,
app/services/journal/service.py) and enqueues `ai.journal.prompt`. The handler
(app/worker/handlers/ai.py:handle_journal_prompt) grounds ONLY in operational signals --
a *shipped* (status=done) roadmap milestone or the current mission's focus task
(app/services/journal/ai_prompt.py:gather_prompt_context) -- and no-ops (keeps the static
prompt) when neither exists. Onboarding to the "validation" stage auto-generates a roadmap
inline (same as e2e/test_mission.py), but none of its tasks are marked done here, so this
journey seeds the mission-focus signal instead by lazily generating today's mission
(GET /missions/today, same lazy-on-read pattern as e2e/test_mission.py) before draining the
worker in-process (LLM_PROVIDER=stub, same pattern as e2e/test_dashboard_ai_briefing.py), so
the drain reliably takes the LLM path.
"""

from datetime import date

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.journal.prompt)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_dashboard.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def test_journal_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- the journal needs nothing else.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        today = date.today().isoformat()

        # 1. Today's prompt -- the writing surface asks for this first. Static/templated on
        # first read (Module 03 deferred AI upgrade); the body carries only `prompt`.
        prompt = c.get("/api/v1/journal/prompts/today", headers=wh)
        assert prompt.status_code == 200, prompt.text
        assert prompt.json()["data"]["prompt"]
        assert set(prompt.json()["data"].keys()) == {"prompt"}
        capture("journal", "prompts_today", prompt)

        # 1b. Seed an operational signal for the AI prompt (gather_prompt_context grounds
        # ONLY in a shipped roadmap milestone or the current mission's focus task -- this
        # journey has no roadmap, so lazily generate today's mission). Weekend guard: see
        # e2e/test_mission.py -- only on Sat/Sun, and only before the first /missions/today
        # call, which materialises an empty mission when weekend_missions is off.
        if date.today().weekday() in (5, 6):
            wk = c.patch("/api/v1/missions/settings", headers=wh, json={"weekend_missions": True})
            assert wk.status_code == 200, wk.text
        mission = c.get("/api/v1/missions/today", headers=wh)
        assert mission.status_code == 200, mission.text

        # 1c. Drain the worker in-process (LLM_PROVIDER=stub) -> ai.journal.prompt personalizes
        # today's prompt from the mission signal (app/worker/handlers/ai.py:handle_journal_prompt)
        # and flips its status to ready. Re-fetch to capture the AI-upgraded prompt.
        _drain()
        ai_prompt = c.get("/api/v1/journal/prompts/today", headers=wh)
        assert ai_prompt.status_code == 200, ai_prompt.text
        assert set(ai_prompt.json()["data"].keys()) == {"prompt"}
        assert "[stub-llm]" in ai_prompt.json()["data"]["prompt"]
        capture("journal", "prompt_today_ai", ai_prompt)

        # 2. Write today's entry.
        created = c.post(
            "/api/v1/journal/entries",
            headers=wh,
            json={
                "date": today,
                "content": "Shipped the invite flow.\nStill unsure about pricing.",
                "mood": "good",
                "stress": 4,
            },
        )
        assert created.status_code == 200, created.text
        entry = created.json()["data"]
        assert entry["content"].startswith("Shipped the invite flow.")
        capture("journal", "entry_created", created)

        entry_id = entry["id"]

        # 3. Autosave: saving the same date again UPDATES, never duplicates.
        resaved = c.post(
            "/api/v1/journal/entries",
            headers=wh,
            json={
                "date": today,
                "content": "Shipped the invite flow.\nPricing decided: usage-based.",
                "mood": "great",
                "stress": 3,
            },
        )
        assert resaved.status_code == 200, resaved.text
        assert resaved.json()["data"]["id"] == entry_id, "autosave must not create a 2nd entry"
        assert resaved.json()["data"]["mood"] == "great"
        capture("journal", "entry_autosaved", resaved)

        # 4. Read it back.
        fetched = c.get(f"/api/v1/journal/entries/{entry_id}", headers=wh)
        assert fetched.status_code == 200, fetched.text
        assert "usage-based" in fetched.json()["data"]["content"]
        capture("journal", "entry_detail", fetched)

        # 5. Edit just the stress -- content and mood must survive.
        edited = c.patch(
            f"/api/v1/journal/entries/{entry_id}",
            headers=wh,
            json={"stress": 6},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["data"]["stress"] == 6
        assert edited.json()["data"]["mood"] == "great"
        capture("journal", "entry_updated", edited)

        # 6. List -- exactly one entry, first line only as the preview.
        listed = c.get("/api/v1/journal/entries", headers=wh)
        assert listed.status_code == 200, listed.text
        body = listed.json()["data"]
        assert body["total"] == 1
        assert body["entries"][0]["first_line"] == "Shipped the invite flow."
        capture("journal", "entries_list", listed)

        # 7. Search over the founder's own entries.
        found = c.get("/api/v1/journal/entries", headers=wh, params={"search": "usage-based"})
        assert found.status_code == 200, found.text
        assert found.json()["data"]["total"] == 1
        capture("journal", "entries_search", found)

        # 8. Mood trend.
        mood = c.get("/api/v1/journal/mood", headers=wh)
        assert mood.status_code == 200, mood.text
        assert mood.json()["data"]["points"], "today's entry should appear in the trend"
        capture("journal", "mood_trend", mood)

        # 9. Delete, then it is gone.
        deleted = c.delete(f"/api/v1/journal/entries/{entry_id}", headers=wh)
        assert deleted.status_code in (200, 204), deleted.text
        if deleted.content:
            capture("journal", "entry_deleted", deleted)

        gone = c.get(f"/api/v1/journal/entries/{entry_id}", headers=wh)
        assert gone.status_code == 404, gone.text
