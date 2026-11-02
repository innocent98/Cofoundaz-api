"""Live Team journey (light): invite, list, resend and revoke invitations, change a role, a
Documents grant that dies with the membership, remove, leave, the last-founder guard, invite a
removed person back, the comments index and the audit tab with its CSV export.

Captures land in `e2e/_captures/team_manage/*.json` (verbatim source of
`docs/fe-integration-guide-team.md`). Invitation emails are read through the file mail backend.
"""

import httpx


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Acme Labs"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Get first customers"]}
    )
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200


def test_team_manage_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    g = "team_manage"
    t = "/api/v1/team"
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        a = make_verified_user(c)
        b = make_verified_user(c)
        d = make_verified_user(c)
        a_auth = _auth(c.post("/api/v1/auth/login", json=a).json()["data"]["access_token"])
        _onboard(c, a_auth)
        wid = c.get("/api/v1/auth/me", headers=a_auth).json()["data"]["active_workspace_id"]
        fh = {**a_auth, "X-Workspace-Id": wid}

        def joined(user: dict, role: str) -> dict:
            inv = c.post(
                f"{t}/invitations",
                headers=fh,
                json={"invites": [{"email": user["email"], "role": role}]},
            )
            assert inv.status_code == 200, inv.text
            token = mailbox.latest_token_for(user["email"], subject_contains="invited")
            u_auth = _auth(c.post("/api/v1/auth/login", json=user).json()["data"]["access_token"])
            acc = c.post("/api/v1/invitations/accept", headers=u_auth, json={"token": token})
            assert acc.status_code == 200, acc.text
            return {**u_auth, "X-Workspace-Id": wid}

        bh = joined(b, "mentor")
        dh = joined(d, "mentor")
        r = c.get(f"{t}/members", headers=fh)
        members = {m["email"]: m for m in r.json()["data"]}
        assert members[b["email"]]["role"] == "mentor"
        capture(g, "members", r)
        b_id = members[b["email"]]["user_id"]

        # Comments index: the founder mentions B on a milestone.
        tree = c.get("/api/v1/roadmap", headers=fh).json()["data"]
        ms = tree["phases"][0]["milestones"][0]
        r = c.post(
            f"{t}/comments",
            headers=fh,
            json={
                "entity_type": "roadmap_milestone",
                "entity_id": ms["id"],
                "body": "Can you review the interview plan?",
                "mention_user_ids": [b_id],
            },
        )
        assert r.status_code == 201, r.text
        r = c.get(f"{t}/comments/threads?filter=mentions", headers=bh)
        assert len(r.json()["data"]) == 1 and r.json()["data"][0]["mentions_me"] is True
        capture(g, "comment_threads_mentions", r)
        r = c.get(f"{t}/comments/threads?filter=bad", headers=bh)
        assert r.status_code == 422
        capture(g, "comment_threads_bad_filter", r)

        # A document grant for B (by account): B sees it under "Shared with me".
        doc = c.post("/api/v1/documents", headers=fh, json={"template_key": "one_pager"})
        doc_id = doc.json()["data"]["id"]
        gr = c.post(
            f"/api/v1/documents/{doc_id}/grants",
            headers=fh,
            json={"user_id": b_id, "permission": "view"},
        )
        assert gr.status_code == 201, gr.text
        assert len(c.get("/api/v1/documents/shared-with-me", headers=bh).json()["data"]) >= 1

        # Invitations: list, resend, revoke.
        pending_email = unique_email("pending")
        assert (
            c.post(
                f"{t}/invitations",
                headers=fh,
                json={"invites": [{"email": pending_email, "role": "accountant"}]},
            ).status_code
            == 200
        )
        r = c.get(f"{t}/invitations", headers=fh)
        inv = r.json()["data"][0]
        assert inv["email"] == pending_email and inv["expired"] is False
        capture(g, "invitations_list", r)
        r = c.post(f"{t}/invitations/{inv['id']}/resend", headers=fh)
        assert r.json()["data"]["resent"] is True
        capture(g, "invitation_resend", r)
        r = c.delete(f"{t}/invitations/{inv['id']}", headers=fh)
        assert r.json()["data"]["revoked"] is True
        capture(g, "invitation_revoke", r)
        r = c.post(f"{t}/invitations/{inv['id']}/resend", headers=fh)
        assert r.status_code == 409
        capture(g, "invitation_resend_after_revoke", r)

        # Roles: change, and the rules.
        r = c.patch(f"{t}/members/{b_id}", headers=fh, json={"role": "accountant"})
        assert r.status_code == 200 and r.json()["data"]["role"] == "accountant"
        capture(g, "role_change", r)
        me_id = next(m["user_id"] for m in members.values() if m["role"] == "founder")
        r = c.patch(f"{t}/members/{me_id}", headers=fh, json={"role": "mentor"})
        assert r.status_code == 409
        capture(g, "role_change_self", r)
        r = c.patch(f"{t}/members/{b_id}", headers=fh, json={"role": "investor"})
        assert r.status_code == 422
        capture(g, "role_change_investor", r)
        r = c.get(f"{t}/invitations", headers=bh)
        assert r.status_code == 403
        capture(g, "invitations_not_founder", r)

        # Leave: D leaves; the only founder cannot.
        r = c.post(f"{t}/leave", headers=dh)
        assert r.status_code == 200
        capture(g, "leave", r)
        assert c.get(f"{t}/members", headers=dh).status_code == 403
        r = c.post(f"{t}/leave", headers=fh)
        assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_FOUNDER"
        capture(g, "leave_last_founder", r)

        # Remove B: instant lock-out, grant revoked.
        r = c.delete(f"{t}/members/{b_id}", headers=fh)
        assert r.status_code == 200 and r.json()["data"]["document_grants_revoked"] == 1
        capture(g, "remove", r)
        r = c.get(f"{t}/members", headers=bh)
        assert r.status_code == 403
        capture(g, "removed_member_locked_out", r)
        r = c.delete(f"{t}/members/{me_id}", headers=fh)
        assert r.status_code == 409
        capture(g, "remove_self", r)

        # B invited back and accepted.
        bh2 = joined(b, "team_member")
        r = c.get(f"{t}/members", headers=bh2)
        assert r.status_code == 200
        capture(g, "members_after_rejoin", c.get(f"{t}/members", headers=fh))

        # Audit tab.
        r = c.get(f"{t}/audit?limit=5", headers=fh)
        assert r.status_code == 200 and r.json()["data"]["entries"]
        capture(g, "audit_page1", r)
        cur = r.json()["data"]["next_cursor"]
        assert cur
        r = c.get(f"{t}/audit?limit=5&cursor={cur}", headers=fh)
        assert r.status_code == 200
        capture(g, "audit_page2", r)
        r = c.get(f"{t}/audit/export", headers=fh)
        assert r.status_code == 200 and r.text.startswith("when,actor,action,target")
        assert "attachment" in r.headers["content-disposition"]
        (
            __import__("pathlib").Path(__file__).parent / "_captures" / g / "audit_export.csv"
        ).write_text(r.text)
        assert c.get(f"{t}/audit", headers=bh2).status_code == 403
