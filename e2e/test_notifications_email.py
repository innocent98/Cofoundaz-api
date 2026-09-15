"""Live Module 20 Slice 2 journey: email delivery + preferences + worker drain.

Mirrors `e2e/test_notifications.py`'s A/B setup verbatim (founder A onboards,
invites teammate B, B signs up + verifies + accepts the invite -- a REAL
second active member of the workspace, not an external share recipient) and
its document-create/share steps, then exercises what's new in this slice:

A shares a document -> `document.shared` fires -> the registry (Slice 1,
unchanged) creates B's in-app notification row AND (new, Slice 2) enqueues an
`email.notification` job for B, because B's "documents" email category is ON
by default (opt-out model, spec D3). Nothing sends synchronously -- the job
sits `queued` in the `jobs` table until a worker claims it. There is no
worker container in this e2e harness, so we drain the queue in-process with
the SAME `run_once` the real `python -m app.worker` loop calls, then assert B
received the email via the file email backend (`EMAIL_BACKEND=file`).

B then turns the Documents category off (`PUT /notifications/preferences`)
and A shares again: a second `document.shared` event still creates a new
in-app row for B (in-app delivery ignores preferences -- see the Slice 1 FE
guide's §0), but `email_enabled()` now gates the category off at enqueue
time, so NO new `email.notification` job is created and, after another
drain, B's mailbox count is unchanged.

Every response along the way is captured to `e2e/_captures/notifications_email/
*.json` -- those files are the verbatim source for the "Preferences & email
(Slice 2)" section of `docs/fe-integration-guide-notifications.md`. The
delivered email itself is not an httpx.Response (it comes out of the file
mailbox as a plain dict), so it is written directly rather than through the
`capture` fixture -- see `delivered_email.json`.
"""

import json
from pathlib import Path

import httpx

from app.db.session import SessionLocal
from app.worker import runner
from app.worker.handlers import email as _email  # noqa: F401  (registers handler)

_CAPTURE_DIR = Path(__file__).parent / "_captures" / "notifications_email"


def _auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


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


def _drain() -> None:
    """Drain the queue in-process -- repeated `run_once` batches, the same call the real

    `python -m app.worker` loop makes each iteration. A single `WORKER_BATCH_SIZE` (10)
    batch is not enough here: this e2e run shares one `jobs` table across the WHOLE
    suite, and every earlier test's `document.shared`/`mission.completed`/etc. events
    also enqueue `email.notification` jobs (every category defaults ON, spec D3) for
    their own users -- by the time this test runs, dozens of unrelated jobs are already
    queued ahead of ours (FIFO by `created_at`). Looping until a batch claims nothing
    drains the whole backlog, same as leaving the real worker running long enough.
    """
    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- 5000 jobs -- so a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _write_capture(name: str, data: dict) -> None:
    """Write a raw (non-httpx.Response) captured payload verbatim, like `capture` does for responses."""
    _CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    (_CAPTURE_DIR / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")


def test_email_delivery_and_preferences(
    base_url, make_verified_user, mailbox, unique_email, capture
):
    with (
        httpx.Client(base_url=base_url, timeout=10.0) as a,
        httpx.Client(base_url=base_url, timeout=10.0) as b,
    ):
        # 0. Founder A walks the wizard (steps 1-4) -- invites must be sent
        # BEFORE onboarding/complete, same ordering as test_notifications.py.
        u_a = make_verified_user(a)
        access_a = a.post("/api/v1/auth/login", json=u_a).json()["data"]["access_token"]
        auth_a = _auth(access_a)

        _onboard_steps(a, auth_a, name="Cofoundaz Email Notifications")

        # 1. Invite a teammate (B) -- a REAL second member, not an external
        # share recipient, so B has their own notification preferences row.
        teammate_email = unique_email("teammate")
        inv = a.post(
            "/api/v1/onboarding/invites",
            headers=auth_a,
            json={"invites": [{"email": teammate_email, "role": "team_member"}]},
        )
        assert inv.status_code == 200 and teammate_email in inv.json()["data"]["created"]
        invite_token = mailbox.latest_token_for(teammate_email, subject_contains="invited")

        # 2. B signs up, verifies, and accepts the invite -- becomes an
        # ACTIVE member of A's workspace.
        pw_b = "Ev-" + teammate_email.split("-")[-1].split("@")[0] + "-9"
        b.post("/api/v1/auth/signup", json={"email": teammate_email, "password": pw_b})
        vtok = mailbox.latest_token_for(teammate_email, subject_contains="Verify")
        b.post("/api/v1/auth/verify", json={"token": vtok})
        access_b = b.post(
            "/api/v1/auth/login", json={"email": teammate_email, "password": pw_b}
        ).json()["data"]["access_token"]
        auth_b = _auth(access_b)
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

        # 3. B's preferences default to master_email=True and every category
        # (including "documents") ON -- the opt-out model, spec D3.
        defaults = b.get("/api/v1/notifications/preferences", headers=wh_b)
        assert defaults.status_code == 200, defaults.text
        assert defaults.json()["data"]["master_email"] is True
        assert defaults.json()["data"]["categories"]["documents"] is True

        # 4. Founder A creates a document and shares it -- a real
        # `document.shared` event. The registry (Slice 1, unchanged) creates
        # B's in-app row AND (Slice 2, new) enqueues an `email.notification`
        # job for B, since B's "documents" category is ON by default.
        created = a.post("/api/v1/documents", headers=wh_a, json={"template_key": "one_pager"})
        assert created.status_code == 201, created.text
        doc_id = created.json()["data"]["id"]

        recipient1 = unique_email("shared-with")
        share1 = a.post(
            f"/api/v1/documents/{doc_id}/shares",
            headers=wh_a,
            json={"email": recipient1, "access_level": "view"},
        )
        assert share1.status_code == 201, share1.text

        # 5. Nothing is sent synchronously -- drain the queue in-process (the
        # same `run_once` the real worker loop calls) and assert B received
        # the email via the file email backend.
        _drain()
        msg = mailbox.latest_for(teammate_email)
        assert msg is not None and "document" in msg["subject"].lower()
        _write_capture("delivered_email", msg)

        # 6. B turns the Documents category email OFF. master_email is left
        # untouched (still True) -- this is a per-category opt-out, not a
        # global unsubscribe.
        off = b.put(
            "/api/v1/notifications/preferences",
            headers=wh_b,
            json={"categories": {"documents": False}},
        )
        assert off.status_code == 200, off.text
        assert off.json()["data"]["categories"]["documents"] is False
        assert off.json()["data"]["master_email"] is True
        capture("notifications_email", "preferences_documents_off", off)

        before = mailbox.count_for(teammate_email)

        # 7. A shares again -- a SECOND `document.shared` event. B still
        # gets a new in-app row (in-app delivery ignores preferences), but
        # `email_enabled()` now gates the category off at enqueue time, so
        # NO new `email.notification` job is created.
        recipient2 = unique_email("shared-with")
        share2 = a.post(
            f"/api/v1/documents/{doc_id}/shares",
            headers=wh_a,
            json={"email": recipient2, "access_level": "view"},
        )
        assert share2.status_code == 201, share2.text

        _drain()
        assert mailbox.count_for(teammate_email) == before  # no new email

        prefs = b.get("/api/v1/notifications/preferences", headers=wh_b)
        assert prefs.status_code == 200, prefs.text
        assert prefs.json()["data"]["categories"]["documents"] is False
        capture("notifications_email", "preferences_get", prefs)

        # 8. An unknown category key 422s -- exercised live so the FE guide's
        # error section carries a real captured body, not a re-derived one.
        bad = b.put(
            "/api/v1/notifications/preferences",
            headers=wh_b,
            json={"categories": {"not_a_category": False}},
        )
        assert bad.status_code == 422, bad.text
        capture("notifications_email", "preferences_put_unknown_category_422", bad)
