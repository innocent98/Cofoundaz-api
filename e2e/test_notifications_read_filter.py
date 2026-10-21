"""Live notification READ filter: rows stored before the audience fixes stay hidden by role.

API #150/#152/#153 fixed who a notification is fanned out to, but rows stored earlier still sat in
mentors' and consultants' inboxes. The read paths (list, unread-count, mark-read, read-all, and
the stream's initial unread) now filter by the member's CURRENT role with the same audience map
as fan-out (`app/services/notifications/audience.py`).

One founder workspace with a mentor and a business consultant. Legacy rows are seeded straight
into the table (a fan-out can no longer produce them), for the mentor and the consultant:
`mission.ready`, `dashboard.briefing.ready`, `assessment.completed`, plus an open-type
`healthscore.dropped`. Every body is captured to `e2e/_captures/notifications_read_filter/*.json`,
the verbatim source for `docs/fe-integration-guide-notifications-read-filter.md`.
"""

import uuid

import httpx

G = "notifications_read_filter"
N = "/api/v1/notifications"
LEGACY = ("mission.ready", "dashboard.briefing.ready", "assessment.completed")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _join(c, wh, ws, role, unique_email, mailbox):
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
    uid = c.get("/api/v1/auth/me", headers=auth).json()["data"]["user"]["id"]
    return {**auth, "X-Workspace-Id": ws}, uuid.UUID(uid)


def _seed_legacy(ws: str, user_id: uuid.UUID) -> None:
    from app.db.models.notification import Notification
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        for t in (*LEGACY, "healthscore.dropped"):
            db.add(
                Notification(
                    user_id=user_id,
                    startup_id=uuid.UUID(ws),
                    type=t,
                    title=f"legacy {t}",
                    body="",
                    data={},
                )
            )
        db.commit()
    finally:
        db.close()


def _set_role(ws: str, user_id: uuid.UUID, role: str) -> None:
    from app.db.models.enums import MembershipRole
    from app.db.models.membership import Membership
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        m = db.query(Membership).filter_by(user_id=user_id, startup_id=uuid.UUID(ws)).one()
        m.role = MembershipRole(role)
        db.commit()
    finally:
        db.close()


def _types(r) -> list[str]:
    # Only the seeded rows: real fan-out (member.joined etc.) adds unrelated, open-type noise.
    rows = r.json()["data"]["notifications"]
    return sorted(n["type"] for n in rows if n["title"].startswith("legacy "))


def test_notifications_read_filter_journey(
    base_url, make_verified_user, unique_email, mailbox, capture
):
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
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        ws = me["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        founder_id = uuid.UUID(me["user"]["id"])

        mentor, mentor_id = _join(c, wh, ws, "mentor", unique_email, mailbox)
        consultant, consultant_id = _join(c, wh, ws, "business_consultant", unique_email, mailbox)
        base = {
            k: c.get(f"{N}/unread-count", headers=h).json()["data"]["unread"]
            for k, h in (("f", wh), ("m", mentor), ("c", consultant))
        }
        for uid in (founder_id, mentor_id, consultant_id):
            _seed_legacy(ws, uid)

        # 1. Founder: all four rows (the legacy types are theirs).
        f = c.get(N, headers=wh)
        assert _types(f) == sorted((*LEGACY, "healthscore.dropped"))
        capture(G, "list_founder", f)
        fu = c.get(f"{N}/unread-count", headers=wh)
        assert fu.json()["data"]["unread"] == base["f"] + 4
        capture(G, "unread_count_founder", fu)

        # 2. Mentor + consultant: only the open type; unread counts it out.
        for name, h in (("mentor", mentor), ("business_consultant", consultant)):
            r = c.get(N, headers=h)
            assert _types(r) == ["healthscore.dropped"], r.text
            capture(G, f"list_{name}", r)
            cnt = c.get(f"{N}/unread-count", headers=h)
            assert cnt.json()["data"]["unread"] == base["m" if name == "mentor" else "c"] + 1
            capture(G, f"unread_count_{name}", cnt)

        # 3. A hidden row cannot be marked read by id (404), and read-all skips it.
        from app.db.models.notification import Notification
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            mentor_hidden = (
                db.query(Notification.id)
                .filter_by(user_id=mentor_id, type="mission.ready")
                .scalar()
            )
        finally:
            db.close()
        mr = c.post(f"{N}/{mentor_hidden}/read", headers=mentor)
        assert mr.status_code == 404, mr.text
        capture(G, "mark_read_hidden_mentor_404", mr)
        ra = c.post(f"{N}/read-all", headers=mentor)
        assert ra.json()["data"]["marked"] == base["m"] + 1
        capture(G, "read_all_mentor", ra)
        assert c.get(f"{N}/unread-count", headers=mentor).json()["data"]["unread"] == 0
        assert c.get(f"{N}/unread-count", headers=wh).json()["data"]["unread"] == base["f"] + 4

        # 4. Role change re-filters: promote the mentor to team_member (done in the DB; there is no
        #    role-change endpoint yet) and the legacy rows appear on the very next request.
        _set_role(ws, mentor_id, "team_member")
        after = c.get(N, headers=mentor)
        assert _types(after) == sorted((*LEGACY, "healthscore.dropped"))
        capture(G, "list_after_role_change_to_team_member", after)
        cnt = c.get(f"{N}/unread-count", headers=mentor)
        assert cnt.json()["data"]["unread"] == 3  # healthscore row was read-all'd above
        capture(G, "unread_count_after_role_change", cnt)
