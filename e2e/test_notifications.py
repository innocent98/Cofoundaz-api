"""Live Module 20 Notifications journey (Slice 1: in-app feed + fan-out).

A workspace founder (A) invites a teammate (B), who signs up and accepts --
a REAL second active member of the workspace, not an external share
recipient. A then shares a document, which fires the `document.shared`
event on the platform event bus (`app/platform/events.py`); the bus
dispatches synchronously, in the same transaction, to the notifications
registry (`app/services/notifications/registry.py`), which fans the event
out to one notification row per active member. B -- who did not perform the
action -- reads it back via `GET /notifications`.

**Known gap surfaced by this live run (not fixed here -- Task 6 is
docs/e2e only, no app/ changes):** the spec's "recipient = active members
minus actor" rule (`_members_minus_actor` in registry.py) resolves the actor
via `payload.get(k) for k in (actor_id, shared_by, created_by, user_id,
shared_by_id)`. The `document.shared` publish site
(`app/services/documents/shares.py::create_share`) -- like 13 of the other
14 `_members_minus_actor`-routed v1 events -- never puts any of those keys
in its payload (only `startup_id`/`document_id`/`share_id`), so the actor
resolves to `None` and nobody is excluded: A also receives a notification
for A's own share. This journey asserts the REAL observed behavior (A is
notified too) rather than the spec-aspirational one, and captures A's own
feed as live evidence. The one event where exclusion actually works is
`workspace.member.joined`, which goes through a dedicated
`_existing_members` helper keyed directly off `payload["user_id"]` --
exercised here as a side effect of B's invite acceptance (A receives that
notification; B, the joiner, correctly does not).

Every response along the way is captured to `e2e/_captures/notifications/
*.json` -- those files are the verbatim source for
`docs/fe-integration-guide-notifications.md`.
"""

import httpx


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, name: str) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def test_notifications_journey(base_url, make_verified_user, mailbox, unique_email, capture):
    with (
        httpx.Client(base_url=base_url, timeout=10.0) as a,
        httpx.Client(base_url=base_url, timeout=10.0) as b,
    ):
        # 0. Founder A walks the wizard (steps 1-4) -- invites must be sent
        # BEFORE onboarding/complete (`ensure_draft` 409s once
        # `onboarding_completed_at` is set, mirroring
        # e2e/test_onboarding.py's real ordering).
        u_a = make_verified_user(a)
        access_a = a.post("/api/v1/auth/login", json=u_a).json()["data"]["access_token"]
        auth_a = _auth_header(access_a)

        _onboard_steps(a, auth_a, name="Cofoundaz Notifications")

        # 1. Invite a teammate (B) -- a REAL second member, not an external
        # share recipient, so B has their own workspace-scoped feed.
        teammate_email = unique_email("teammate")
        inv = a.post(
            "/api/v1/onboarding/invites",
            headers=auth_a,
            json={"invites": [{"email": teammate_email, "role": "team_member"}]},
        )
        assert inv.status_code == 200 and teammate_email in inv.json()["data"]["created"]
        invite_token = mailbox.latest_token_for(teammate_email, subject_contains="invited")

        # 2. B signs up, verifies, and accepts the invite -- becomes an
        # ACTIVE member of A's workspace. This itself fires
        # `workspace.member.joined`, whose recipient rule
        # (`_existing_members`) correctly excludes the joiner (B) and
        # notifies existing members (A) -- see module docstring.
        pw_b = "Ev-" + teammate_email.split("-")[-1].split("@")[0] + "-9"
        b.post("/api/v1/auth/signup", json={"email": teammate_email, "password": pw_b})
        vtok = mailbox.latest_token_for(teammate_email, subject_contains="Verify")
        b.post("/api/v1/auth/verify", json={"token": vtok})
        access_b = b.post(
            "/api/v1/auth/login", json={"email": teammate_email, "password": pw_b}
        ).json()["data"]["access_token"]
        auth_b = _auth_header(access_b)
        accept = b.post("/api/v1/invitations/accept", json={"token": invite_token}, headers=auth_b)
        assert accept.status_code == 200, accept.text

        # 2b. Founder A completes onboarding now that the invite is sent --
        # unlocks the workspace's `X-Workspace-Id` scope for both A and B.
        onboarded = a.post("/api/v1/onboarding/complete", headers=auth_a)
        assert onboarded.status_code == 200, onboarded.text

        me_a = a.get("/api/v1/auth/me", headers=auth_a).json()["data"]
        startup_id = me_a["active_workspace_id"]
        wh_a = {**auth_a, "X-Workspace-Id": startup_id}
        wh_b = {**auth_b, "X-Workspace-Id": startup_id}

        # 3. Baseline -- B's feed is empty and unread-count is 0 before A
        # does anything (B's own join does not notify B).
        baseline = b.get("/api/v1/notifications/unread-count", headers=wh_b)
        assert baseline.status_code == 200, baseline.text
        assert baseline.json()["data"]["unread"] == 0
        capture("notifications", "unread_count_before", baseline)

        baseline_feed = b.get("/api/v1/notifications", headers=wh_b)
        assert baseline_feed.status_code == 200, baseline_feed.text
        assert baseline_feed.json()["data"]["notifications"] == []
        assert baseline_feed.json()["data"]["next_cursor"] is None
        capture("notifications", "feed_before_any_event", baseline_feed)

        # 4. Founder A creates a document, then shares it TWICE (with two
        # different external recipients) -- two real `document.shared`
        # events, giving B two notifications to exercise keyset pagination
        # with.
        created = a.post("/api/v1/documents", headers=wh_a, json={"template_key": "one_pager"})
        assert created.status_code == 201, created.text
        doc_id = created.json()["data"]["id"]
        capture("notifications", "trigger_document_create", created)

        recipient1 = unique_email("shared-with")
        share1 = a.post(
            f"/api/v1/documents/{doc_id}/shares",
            headers=wh_a,
            json={"email": recipient1, "access_level": "view"},
        )
        assert share1.status_code == 201, share1.text
        capture("notifications", "trigger_document_share_1", share1)

        recipient2 = unique_email("shared-with")
        share2 = a.post(
            f"/api/v1/documents/{doc_id}/shares",
            headers=wh_a,
            json={"email": recipient2, "access_level": "view"},
        )
        assert share2.status_code == 201, share2.text
        capture("notifications", "trigger_document_share_2", share2)

        # 4b. KNOWN GAP (see module docstring): `document.shared`'s
        # recipient rule is `_members_minus_actor`, but the publish payload
        # ({startup_id, document_id, share_id}) carries no actor-identifying
        # key, so the actor is never excluded -- A also gets notified of A's
        # own two shares. Assert the REAL behavior and capture it as live
        # evidence rather than silently omitting it.
        a_feed = a.get("/api/v1/notifications", headers=wh_a)
        assert a_feed.status_code == 200, a_feed.text
        a_types = [n["type"] for n in a_feed.json()["data"]["notifications"]]
        assert a_types.count("document.shared") == 2, a_types
        # A's own join-adjacent notification (from B's accept) is also here.
        assert "workspace.member.joined" in a_types, a_types
        capture("notifications", "actor_not_excluded_known_gap", a_feed)

        # 5. B -- who did NOT perform the action -- sees exactly two
        # `document.shared` notifications, both unread, and NO
        # `workspace.member.joined` (B was the joiner, correctly excluded).
        unread_feed = b.get("/api/v1/notifications", headers=wh_b, params={"unread": "true"})
        assert unread_feed.status_code == 200, unread_feed.text
        rows = unread_feed.json()["data"]["notifications"]
        assert len(rows) == 2
        assert all(r["type"] == "document.shared" for r in rows)
        assert all(r["read"] is False for r in rows)
        assert {rows[0]["data"]["share_id"], rows[1]["data"]["share_id"]} == {
            share1.json()["data"]["id"],
            share2.json()["data"]["id"],
        }
        capture("notifications", "feed_unread_only", unread_feed)

        unread_count_resp = b.get("/api/v1/notifications/unread-count", headers=wh_b)
        assert unread_count_resp.status_code == 200, unread_count_resp.text
        assert unread_count_resp.json()["data"]["unread"] == 2
        capture("notifications", "unread_count_after_two_events", unread_count_resp)

        # 6. Keyset pagination cadence: limit=1 returns the newest row + a
        # non-null next_cursor; following the cursor returns the second (and
        # last) row with next_cursor now null.
        page1 = b.get("/api/v1/notifications", headers=wh_b, params={"limit": 1})
        assert page1.status_code == 200, page1.text
        page1_data = page1.json()["data"]
        assert len(page1_data["notifications"]) == 1
        assert page1_data["next_cursor"] is not None
        capture("notifications", "feed_page1_limit1", page1)

        page2 = b.get(
            "/api/v1/notifications",
            headers=wh_b,
            params={"limit": 1, "cursor": page1_data["next_cursor"]},
        )
        assert page2.status_code == 200, page2.text
        page2_data = page2.json()["data"]
        assert len(page2_data["notifications"]) == 1
        assert page2_data["next_cursor"] is None
        assert page2_data["notifications"][0]["id"] != page1_data["notifications"][0]["id"]
        capture("notifications", "feed_page2_cursor", page2)

        notif_id_1 = page1_data["notifications"][0]["id"]
        notif_id_2 = page2_data["notifications"][0]["id"]

        # 7. Mark one read -> serialized notification with read=true; the
        # unread-count drops by exactly one (the bell badge).
        marked = b.post(f"/api/v1/notifications/{notif_id_1}/read", headers=wh_b)
        assert marked.status_code == 200, marked.text
        assert marked.json()["data"]["read"] is True
        assert marked.json()["data"]["id"] == notif_id_1
        capture("notifications", "mark_read", marked)

        after_one_read = b.get("/api/v1/notifications/unread-count", headers=wh_b)
        assert after_one_read.status_code == 200, after_one_read.text
        assert after_one_read.json()["data"]["unread"] == 1
        capture("notifications", "unread_count_after_single_read", after_one_read)

        # 7b. Marking the same notification read again is idempotent -- still
        # 200, still read=true, does not change read_at into an error state.
        marked_again = b.post(f"/api/v1/notifications/{notif_id_1}/read", headers=wh_b)
        assert marked_again.status_code == 200, marked_again.text
        assert marked_again.json()["data"]["read"] is True
        capture("notifications", "mark_read_idempotent", marked_again)

        # 7c. A random/unknown notification id 404s -- uniform error shape.
        bad_read = b.post(
            "/api/v1/notifications/00000000-0000-0000-0000-000000000000/read", headers=wh_b
        )
        assert bad_read.status_code == 404, bad_read.text
        capture("notifications", "mark_read_404_unknown", bad_read)

        # 7d. Cross-user scoping: A's OWN `document.shared` notification row
        # (a distinct row id from B's, per-recipient) is not B's to mark --
        # 404, not a leak of "this exists but isn't yours".
        a_own_feed = a.get("/api/v1/notifications", headers=wh_a, params={"unread": "true"})
        a_own_share_notif_id = next(
            n["id"]
            for n in a_own_feed.json()["data"]["notifications"]
            if n["type"] == "document.shared"
        )
        cross_user_read = b.post(f"/api/v1/notifications/{a_own_share_notif_id}/read", headers=wh_b)
        assert cross_user_read.status_code == 404, cross_user_read.text
        capture("notifications", "mark_read_404_cross_user", cross_user_read)

        # 8. read-all marks the remaining unread row(s) -> {marked: N} and
        # zeroes the unread-count.
        read_all = b.post("/api/v1/notifications/read-all", headers=wh_b)
        assert read_all.status_code == 200, read_all.text
        assert read_all.json()["data"]["marked"] == 1
        capture("notifications", "read_all", read_all)

        after_read_all = b.get("/api/v1/notifications/unread-count", headers=wh_b)
        assert after_read_all.status_code == 200, after_read_all.text
        assert after_read_all.json()["data"]["unread"] == 0
        capture("notifications", "unread_count_after_read_all", after_read_all)

        empty_unread_feed = b.get("/api/v1/notifications", headers=wh_b, params={"unread": "true"})
        assert empty_unread_feed.status_code == 200, empty_unread_feed.text
        assert empty_unread_feed.json()["data"]["notifications"] == []
        capture("notifications", "feed_unread_empty_after_read_all", empty_unread_feed)

        # 9. The full (non-unread) feed still shows both rows, now
        # read=true -- read-all does not delete rows, only marks them.
        full_feed = b.get("/api/v1/notifications", headers=wh_b)
        assert full_feed.status_code == 200, full_feed.text
        full_rows = full_feed.json()["data"]["notifications"]
        assert len(full_rows) == 2
        assert all(r["read"] is True for r in full_rows)
        assert notif_id_2 in {r["id"] for r in full_rows}
        capture("notifications", "feed_full_after_read_all", full_feed)
