"""Live Investor Readiness slice D journey: a founder starts a mock investor session, answers
(each answer is a model call), ends it, polls the scoring job, reads the scorecard, and sees the
Story signal, the history log and the "summary ready" notification. Also: the one-session rule,
an early end with no answers, the 10-answer cap, validation, the budget gate, and the privacy line
between founder and team member (a team member gets ratings and counts, never the words).

The job and the investor are driven in this process with a SCRIPTED language model (fixed
questions, fixed ratings): the live part is the API, the queue, the transcript handling, the
scoring maths and the privacy shapes. Responses go to e2e/_captures/readiness_qa/ and are the
verbatim source of the Mock Q&A section of docs/fe-integration-guide-investor-readiness.md.
"""

import httpx
import pytest

from tests.qa_helpers import ScriptedLLM

R = "/api/v1/readiness"
Q = f"{R}/qa-sessions"
G = "readiness_qa"
SECRET = "Our runway is 4.2 months and we are talking to two banks."


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Placeholder Co"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "launch"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Raise a seed round"]}
    )
    c.post("/api/v1/onboarding/complete", headers=auth)


def _join(c: httpx.Client, wh: dict, role: str, unique_email, mailbox) -> dict:
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = "Member-" + email.split("@")[0][-8:] + "-9"
    c.post("/api/v1/auth/signup", json={"email": email, "password": pw})
    c.post(
        "/api/v1/auth/verify",
        json={"token": mailbox.latest_token_for(email, subject_contains="Verify")},
    )
    access = c.post("/api/v1/auth/login", json={"email": email, "password": pw}).json()["data"][
        "access_token"
    ]
    auth = {"Authorization": f"Bearer {access}"}
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": wh["X-Workspace-Id"]}


def _drain(llm: ScriptedLLM) -> None:
    """Run queued jobs here, as the worker would, with the language model scripted."""
    from app.db.session import SessionLocal
    from app.platform import llm_budget
    from app.services.notifications.registry import register as register_notifications
    from app.services.readiness.triggers import register as register_readiness
    from app.worker import runner
    from app.worker.handlers import readiness as handlers

    register_notifications()
    register_readiness()
    runner.register_handler("ai.readiness.qa_score", handlers.handle_qa_scoring)
    llm_budget.get_llm_client = lambda: llm  # type: ignore[assignment]
    db = SessionLocal()
    try:
        for _ in range(100):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


@pytest.fixture()
def scripted():
    from app.platform import llm_budget

    real = llm_budget.get_llm_client
    llm = ScriptedLLM()
    llm_budget.get_llm_client = lambda: llm  # the API server is another process: this only
    yield llm  # steers the in-process drain; the server's own turns use LLM_PROVIDER=stub
    llm_budget.get_llm_client = real


def test_mock_qa_journey(base_url, make_verified_user, capture, unique_email, mailbox, scripted):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        _onboard(c, auth)
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Nothing yet; the Story signal says to rehearse.
        empty = c.get(Q, headers=wh)
        assert empty.status_code == 200 and empty.json()["data"]["sessions"] == []
        capture(G, "list_empty", empty)

        # 2. Validation before anything is spent.
        bad = c.post(Q, headers=wh, json={"focus": "everything", "intensity": "skeptical_vc"})
        assert bad.status_code == 422, bad.text
        capture(G, "start_invalid_422", bad)

        # 3. Start: the investor's opening question comes back with the session.
        start = c.post(Q, headers=wh, json={"focus": "financials", "intensity": "skeptical_vc"})
        assert start.status_code == 201, start.text
        s = start.json()["data"]
        sid = s["session"]["id"]
        assert s["session"]["status"] == "active" and s["session"]["turns"] == 0
        assert isinstance(s["reply"], str) and s["reply"]
        capture(G, "start_201", start)

        # 4. One session at a time.
        second = c.post(Q, headers=wh, json={"focus": "market", "intensity": "friendly_angel"})
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "SESSION_ACTIVE"
        capture(G, "start_second_409", second)

        # 5. Answer a few times.
        for n in range(3):
            ans = c.post(
                f"{Q}/{sid}/messages",
                headers=wh,
                json={"content": f"{SECRET} ({n})", "expected_turn": n},
            )
            assert ans.status_code == 200, ans.text
            if n == 0:
                capture(G, "answer_200", ans)
        retry = c.post(
            f"{Q}/{sid}/messages", headers=wh, json={"content": SECRET, "expected_turn": 0}
        )
        assert retry.status_code == 409, retry.text
        assert retry.json()["error"]["code"] == "TURN_MISMATCH"
        capture(G, "answer_retry_409", retry)
        missing = c.post(f"{Q}/{sid}/messages", headers=wh, json={"content": "no turn given"})
        assert missing.status_code == 422, missing.text
        capture(G, "answer_missing_expected_turn_422", missing)
        blank = c.post(
            f"{Q}/{sid}/messages", headers=wh, json={"content": "   ", "expected_turn": 3}
        )
        assert blank.status_code == 422, blank.text
        capture(G, "answer_blank_422", blank)
        long = c.post(
            f"{Q}/{sid}/messages", headers=wh, json={"content": "x" * 2001, "expected_turn": 3}
        )
        assert long.status_code == 422, long.text
        capture(G, "answer_too_long_422", long)

        # 6. End the session: scoring is queued.
        ended = c.post(f"{Q}/{sid}/end", headers=wh)
        assert ended.status_code == 200, ended.text
        assert ended.json()["data"]["session"]["status"] == "ending"
        job_id = ended.json()["data"]["session"]["job_id"]
        capture(G, "end_200", ended)
        again = c.post(f"{Q}/{sid}/end", headers=wh)
        assert again.status_code == 200 and again.json()["data"]["session"]["status"] == "ending"
        capture(G, "job_queued", c.get(f"/api/v1/jobs/{job_id}", headers=wh))
        no_more = c.post(
            f"{Q}/{sid}/messages", headers=wh, json={"content": "one more", "expected_turn": 3}
        )
        assert no_more.status_code == 409, no_more.text
        assert no_more.json()["error"]["code"] == "SESSION_NOT_ACTIVE"
        capture(G, "answer_after_end_409", no_more)

        # 7. The worker scores it.
        _drain(scripted)
        job = c.get(f"/api/v1/jobs/{job_id}", headers=wh)
        assert job.json()["data"]["status"] == "succeeded", job.text
        capture(G, "job_completed", job)
        done = c.get(f"{Q}/{sid}", headers=wh)
        assert done.status_code == 200, done.text
        d = done.json()["data"]
        assert d["status"] == "scored" and d["rating"] == "good"
        assert d["scorecard"]["weakest"]["i"] == 2 and len(d["transcript"]) >= 7
        capture(G, "session_scored_founder", done)

        # 8. Score, history, notification.
        score = c.get(f"{R}/score", headers=wh).json()["data"]
        story = next(x for x in score["dimensions"] if x["key"] == "story")
        qa_sig = next(sg for sg in story["signals"] if sg["key"] == "story.mock_qa")
        assert qa_sig["value"] > 0
        capture(G, "signal_mock_qa", _wrap(qa_sig))
        hist = c.get(f"{R}/history", params={"range": 30}, headers=wh)
        assert any(e["type"] == "qa_session" for e in hist.json()["data"]["log"])
        capture(G, "history_log", hist)
        notifs = c.get("/api/v1/notifications", headers=wh)
        titles = [n["title"] for n in notifs.json()["data"]["notifications"]]
        assert "Mock Q&A session summary ready" in titles
        row = next(
            n
            for n in notifs.json()["data"]["notifications"]
            if n["title"] == "Mock Q&A session summary ready"
        )
        capture(G, "notification", _wrap(row))
        listed = c.get(Q, headers=wh)
        assert [x["id"] for x in listed.json()["data"]["sessions"]] == [sid]
        capture(G, "list_one", listed)

        # 9. Team member: ratings and counts, never the words; cannot start or answer.
        tm = _join(c, wh, "team_member", unique_email, mailbox)
        tm_one = c.get(f"{Q}/{sid}", headers=tm)
        assert tm_one.status_code == 200 and SECRET not in tm_one.text
        for forbidden in ("transcript", "scorecard", "answers"):
            assert forbidden not in tm_one.json()["data"]
        capture(G, "session_scored_team_member", tm_one)
        tm_list = c.get(Q, headers=tm)
        assert tm_list.status_code == 200 and SECRET not in tm_list.text
        tm_start = c.post(Q, headers=tm, json={"focus": "market", "intensity": "friendly_angel"})
        assert tm_start.status_code == 403, tm_start.text
        capture(G, "team_member_start_403", tm_start)
        tm_answer = c.post(
            f"{Q}/{sid}/messages", headers=tm, json={"content": "hi", "expected_turn": 3}
        )
        assert tm_answer.status_code == 403

        # 10. A mentor sees nothing; a stranger's session id is a 404.
        mentor = _join(c, wh, "mentor", unique_email, mailbox)
        m = c.get(Q, headers=mentor)
        assert m.status_code == 403, m.text
        capture(G, "mentor_list_403", m)
        stranger = make_verified_user(c)
        stoken = c.post("/api/v1/auth/login", json=stranger).json()["data"]["access_token"]
        sauth = {"Authorization": f"Bearer {stoken}"}
        _onboard(c, sauth)
        sme = c.get("/api/v1/auth/me", headers=sauth).json()["data"]
        swh = {**sauth, "X-Workspace-Id": sme["active_workspace_id"]}
        cross = c.get(f"{Q}/{sid}", headers=swh)
        assert cross.status_code == 404, cross.text
        capture(G, "other_workspace_404", cross)

        # 11. Ending a session with no answers costs nothing and is not scored.
        s2 = c.post(Q, headers=wh, json={"focus": "team", "intensity": "friendly_angel"})
        assert s2.status_code == 201, s2.text
        empty_end = c.post(f"{Q}/{s2.json()['data']['session']['id']}/end", headers=wh)
        assert empty_end.json()["data"]["session"]["status"] == "abandoned"
        assert empty_end.json()["data"]["session"]["job_id"] is None
        capture(G, "end_no_answers_abandoned", empty_end)


class _Wrapped:
    """Lets `capture` (which wants a response) store an already-decoded body."""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _wrap(body):
    return _Wrapped(body)
