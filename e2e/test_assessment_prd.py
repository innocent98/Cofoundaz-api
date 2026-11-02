"""Live Assessment PRD (Module 07) journey: role gate (D-ASM-1), progress (D-ASM-2), status filter,
stage snapshot (D-ASM-3), Health Score change on completion (D-ASM-4), structured answers with
`changed_from`, `quarterly_due` and the quarterly / completed notification audiences.

Every body is captured to `e2e/_captures/assessment_prd/*.json`, the verbatim source for
`docs/fe-integration-guide-assessment-prd.md`.

The 90-day clock is moved the only way a fresh signup can: the e2e process backdates
`assessments.completed_at` directly in the e2e database (lazy app imports, see
`test_notifications_scheduler.py`), then runs the real scheduler tick + worker drain in-process.
"""

from datetime import UTC, datetime, timedelta

import httpx
from test_notifications_scheduler import _tick_and_drain

G = "assessment_prd"
A = "/api/v1/assessments"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _walk(c, wh, aid, *, pick):
    vals = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
    keys = []
    while True:
        nq = c.get(f"{A}/{aid}/next-question", headers=wh).json()["data"]["next_question"]
        if nq is None:
            return keys
        if nq["qtype"] == "single_choice":
            val = nq["options"][pick]["value"]
        elif nq["qtype"] == "multi_choice":
            val = [nq["options"][pick]["value"]]
        else:
            val = vals[nq["qtype"]]
        r = c.post(f"{A}/{aid}/answers", headers=wh, json={"question_key": nq["key"], "value": val})
        assert r.status_code == 200, r.text
        keys.append(nq["key"])


def _join(c, wh, ws, role, unique_email, mailbox):
    """Invite `role` to the workspace and accept as a freshly verified user."""
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
    auth = _auth(access)
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": ws}


def _feed_types(c, h) -> list[dict]:
    r = c.get("/api/v1/notifications", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


def _backdate_completed(startup_id: str, days: int) -> None:
    from app.db.models.assessment import Assessment
    from app.db.models.enums import AssessmentStatus
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.query(Assessment).filter(
            Assessment.startup_id == startup_id, Assessment.status == AssessmentStatus.completed
        ).update(
            {"completed_at": datetime.now(UTC) - timedelta(days=days)}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def test_assessment_prd_journey(base_url, make_verified_user, unique_email, mailbox, capture):
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "build"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)
        team = _join(c, wh, ws, "team_member", unique_email, mailbox)

        # 0. Nothing yet: empty list still carries meta.quarterly_due.
        empty = c.get(A, headers=wh)
        assert empty.status_code == 200 and empty.json()["meta"]["quarterly_due"] is False
        capture(G, "list_empty", empty)

        # 1. Start: progress 0, then one answer moves it; 'live' opens a follow-up (denominator +1).
        start = c.post(A, headers=wh)
        assert start.status_code == 201, start.text
        capture(G, "start", start)
        aid = start.json()["data"]["assessment_id"]
        ans = c.post(
            f"{A}/{aid}/answers",
            headers=wh,
            json={"question_key": "product_stage", "value": "live"},
        )
        assert ans.status_code == 200, ans.text
        assert ans.json()["data"]["progress"] == {"answered": 1, "remaining": 8, "pct": 11}
        capture(G, "answer_progress", ans)
        nq = c.get(f"{A}/{aid}/next-question", headers=wh)
        capture(G, "next_question_progress", nq)

        # 2. Status filter + the in-progress list row (progress present, stage_at_time null).
        capture(G, "list_in_progress_filter", c.get(f"{A}?status=in_progress", headers=wh))
        none_done = c.get(f"{A}?status=completed", headers=wh)
        assert none_done.json()["data"] == []
        capture(G, "list_completed_filter_empty", none_done)
        bad = c.get(f"{A}?status=done", headers=wh)
        assert bad.status_code == 422, bad.text
        capture(G, "list_status_invalid_422", bad)

        # 3. First completion ('most advanced' branch): health previous is null.
        _walk(c, wh, aid, pick=-1)
        done1 = c.post(f"{A}/{aid}/complete", headers=wh)
        assert done1.status_code == 200, done1.text
        assert done1.json()["data"]["health"]["previous_score"] is None
        capture(G, "complete_first", done1)
        replay = c.post(f"{A}/{aid}/complete", headers=wh)
        assert replay.json()["data"]["health"] is None
        capture(G, "complete_replay", replay)

        list1 = c.get(A, headers=wh)
        row = list1.json()["data"][0]
        assert row["stage_at_time"] == "build" and row["progress"] is None
        capture(G, "list_after_first", list1)
        detail1 = c.get(f"{A}/{aid}", headers=wh)
        assert all(
            q["changed"] is False and q["changed_from"] is None
            for g in detail1.json()["data"]["answers"]
            for q in g["questions"]
        )
        capture(G, "detail_first_no_previous", detail1)

        # 4. Role gate: mentor 403 on all three reads; team member 200; other workspace untouched.
        for name, url in (
            ("list", A),
            ("detail", f"{A}/{aid}"),
            ("compare", f"{A}/compare?ids={aid}"),
        ):
            r = c.get(url, headers=mentor)
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "FORBIDDEN"
            capture(G, f"{name}_mentor_403", r)
        tm_list = c.get(A, headers=team)
        assert tm_list.status_code == 200, tm_list.text
        capture(G, "list_team_member_200", tm_list)

        # 5. Re-assessment on the 'first option' branch: deltas against the first Health Score.
        aid2 = c.post(A, headers=wh).json()["data"]["assessment_id"]
        _walk(c, wh, aid2, pick=0)
        done2 = c.post(f"{A}/{aid2}/complete", headers=wh)
        assert done2.status_code == 200, done2.text
        h2 = done2.json()["data"]["health"]
        assert h2["previous_score"] == done1.json()["data"]["health"]["score"]
        capture(G, "complete_second_health_delta", done2)
        detail2 = c.get(f"{A}/{aid2}", headers=wh)
        stage_q = next(
            q
            for g in detail2.json()["data"]["answers"]
            for q in g["questions"]
            if q["question_key"] == "product_stage"
        )
        assert stage_q["changed"] is True and stage_q["changed_from"] == "Live with users"
        capture(G, "detail_second_changed_from", detail2)
        capture(G, "list_after_second", c.get(A, headers=wh))
        capture(G, "list_status_completed", c.get(f"{A}?status=completed", headers=wh))

        # 6. Notifications: assessment.completed reached the team member, never the mentor, and
        # not the founder who completed it.
        assert any(n["type"] == "assessment.completed" for n in _feed_types(c, team))
        assert not any(n["type"] == "assessment.completed" for n in _feed_types(c, mentor))
        assert not any(n["type"] == "assessment.completed" for n in _feed_types(c, wh))
        capture(G, "notifications_team_member", c.get("/api/v1/notifications", headers=team))

        # 7. quarterly_due: false now; backdate 100 days -> true (same rule as the cron).
        assert c.get(A, headers=wh).json()["meta"]["quarterly_due"] is False
        _backdate_completed(ws, 100)
        due = c.get(A, headers=wh)
        assert due.json()["meta"]["quarterly_due"] is True
        capture(G, "list_quarterly_due", due)

        # 8. The real scheduler tick + worker drain: founder gets the CTA notification, the
        # mentor and the team member do not.
        _tick_and_drain(datetime.now(UTC))
        founder_feed = _feed_types(c, wh)
        q = [n for n in founder_feed if n["type"] == "assessment.quarterly.due"]
        assert len(q) == 1, founder_feed
        assert q[0]["body"] == "Start re-assessment" and q[0]["data"]["href"] == "/assessment"
        capture(G, "notifications_founder_quarterly", c.get("/api/v1/notifications", headers=wh))
        assert not any(n["type"] == "assessment.quarterly.due" for n in _feed_types(c, mentor))
        assert not any(n["type"] == "assessment.quarterly.due" for n in _feed_types(c, team))
        capture(G, "notifications_mentor", c.get("/api/v1/notifications", headers=mentor))

        # 9. A re-assessment in flight clears the flag.
        c.post(A, headers=wh)
        assert c.get(A, headers=wh).json()["meta"]["quarterly_due"] is False
