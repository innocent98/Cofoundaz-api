"""Live Dashboard activity-feed role journey (D-MIS-8 follow-up to #153).

One founder workspace with a team member, a mentor and an accountant. Proves, against the running
app, what each role gets from `GET /dashboard/activity` and from the live `activity.created`
events on `GET /notifications/stream`. Every body is captured to
`e2e/_captures/dashboard_activity_roles/*.json`, the verbatim source for
`docs/fe-integration-guide-dashboard-role-leaks.md`.

Mission and member rows are written by real endpoints (POST /missions/tasks, invitation accept).
No endpoint writes a finance or marketing activity row yet, so this journey writes one of each
in-process with `write_activity` (the same function every endpoint uses) and commits; the
after-commit listener publishes it to Redis exactly as an endpoint write would. The feed and
stream responses that filter those rows are still real responses from the running server.
"""

import json
import threading
import time
import uuid

import httpx

G = "dashboard_activity_roles"
A = "/api/v1/dashboard/activity"
MISSION_TITLE = "Call the seed lead investor"
SENTINEL = "roadmap.e2e.sentinel"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _join(c, wh, ws, role, unique_email, mailbox):
    """Invite `role` to the workspace and accept as a freshly verified user."""
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Ev-{uuid.uuid4().hex[:12]}-9"
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


def _write(startup_id: str, action: str, entity_type: str, summary: str) -> None:
    from app.db.session import SessionLocal
    from app.platform.activity import write_activity

    db = SessionLocal()
    try:
        write_activity(
            db,
            startup_id=uuid.UUID(startup_id),
            action=action,
            entity_type=entity_type,
            summary=summary,
        )
        db.commit()
    finally:
        db.close()


def _listen(base_url: str, ticket: str, out: dict) -> None:
    """Collect every activity.created frame until the sentinel arrives."""
    try:
        with httpx.Client(base_url=base_url, timeout=15.0) as s:
            with s.stream("GET", f"/api/v1/notifications/stream?ticket={ticket}") as resp:
                assert resp.status_code == 200, resp.read()
                event = None
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event == "activity.created":
                        item = json.loads(line.split(":", 1)[1].strip())
                        out["frames"].append(item)
                        if item["action"] == SENTINEL:
                            return
    except Exception as exc:  # noqa: BLE001 - re-raised on the main thread below
        out["error"] = exc


def _feed(c, h):
    r = c.get(A, headers=h)
    assert r.status_code == 200, r.text
    return r, [i["action"] for i in r.json()["data"]["items"]]


def test_dashboard_activity_roles_journey(
    base_url, make_verified_user, unique_email, mailbox, capture, capture_json
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
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}

        team = _join(c, wh, ws, "team_member", unique_email, mailbox)
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)
        accountant = _join(c, wh, ws, "accountant", unique_email, mailbox)

        # Open a live stream for the founder and the mentor before anything is written.
        streams = {}
        for name, h in (("founder", wh), ("mentor", mentor)):
            ticket = c.post("/api/v1/notifications/stream-ticket", headers=h).json()["data"][
                "ticket"
            ]
            out: dict = {"frames": [], "error": None}
            t = threading.Thread(target=_listen, args=(base_url, ticket, out), daemon=True)
            t.start()
            streams[name] = (t, out)
        time.sleep(1.0)  # let both streams subscribe (past the initial `unread` frame)

        # A real mission write through the API (founder adds a custom task).
        r = c.post("/api/v1/missions/tasks", headers=wh, json={"title": MISSION_TITLE})
        assert r.status_code in (200, 201), r.text
        # No endpoint writes finance/marketing activity yet: write them in-process (see module
        # docstring), then an open-namespace sentinel that ends both stream readers.
        _write(ws, "finance.transaction.created", "transaction", "Ada recorded a 5,000 expense")
        _write(ws, "marketing.campaign.launched", "campaign", "Ada launched the spring campaign")
        _write(ws, SENTINEL, "roadmap", "e2e sentinel (open to every role)")

        for name, (t, out) in streams.items():
            t.join(timeout=10.0)
            assert not t.is_alive(), f"{name} stream stalled"
            if out["error"] is not None:
                raise out["error"]

        founder_live = [f["action"] for f in streams["founder"][1]["frames"]]
        mentor_live = [f["action"] for f in streams["mentor"][1]["frames"]]
        assert founder_live == [
            "mission.task.added",
            "finance.transaction.created",
            "marketing.campaign.launched",
            SENTINEL,
        ]
        assert mentor_live == [SENTINEL]
        capture_json(G, "stream_activity_founder", streams["founder"][1]["frames"])
        capture_json(G, "stream_activity_mentor", streams["mentor"][1]["frames"])

        # The feed: founder and team member see every row; the others are filtered.
        fr, founder = _feed(c, wh)
        assert {"mission.task.added", "finance.transaction.created"} <= set(founder)
        assert {"marketing.campaign.launched", "member.joined", SENTINEL} <= set(founder)
        capture(G, "activity_founder", fr)
        tr, team_actions = _feed(c, team)
        assert team_actions == founder
        capture(G, "activity_team_member", tr)

        mr, mentor_actions = _feed(c, mentor)
        assert MISSION_TITLE not in mr.text
        assert not any(
            a.split(".")[0] in {"mission", "finance", "marketing"} for a in mentor_actions
        )
        assert {"member.joined", SENTINEL} <= set(mentor_actions)
        capture(G, "activity_mentor", mr)
        # The accountant is in FINANCE_ROLES only: finance yes, mission and marketing no.
        ar, accountant_actions = _feed(c, accountant)
        assert MISSION_TITLE not in ar.text
        assert "finance.transaction.created" in accountant_actions
        assert not any(a.split(".")[0] in {"mission", "marketing"} for a in accountant_actions)
        capture(G, "activity_accountant", ar)

        # Pagination with filtering: the mentor's first page of 2 is full and has a cursor.
        p1 = c.get(A, headers=mentor, params={"limit": 2})
        assert p1.status_code == 200, p1.text
        d1 = p1.json()["data"]
        assert len(d1["items"]) == 2 and d1["next_cursor"] is not None
        capture(G, "activity_mentor_page1", p1)
        p2 = c.get(A, headers=mentor, params={"limit": 2, "cursor": d1["next_cursor"]})
        assert p2.status_code == 200, p2.text
        capture(G, "activity_mentor_page2", p2)
        paged = [i["action"] for i in d1["items"] + p2.json()["data"]["items"]]
        assert paged == mentor_actions[: len(paged)]
