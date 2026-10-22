"""Live Sales slice B journey: sequences, enrollments, sending status, AI drafts, the deal
follow-up, the public unsubscribe and bounce intake.

What is LIVE (real uvicorn over HTTP, real Postgres): every route and RBAC check, enrolment and its
skip reasons, the "not configured" state, draft jobs, the public unsubscribe page and one-click
POST with the REAL signed token taken from a REAL SMTP message.

The mailbox: the live server is deliberately UNCONFIGURED (no SALES_OUTREACH_* in its env, see
scripts/e2e_run.sh), which is exactly the state the FE must handle before Adebayo's cPanel env is
set. The worker/scheduler run IN THIS PROCESS (same pattern as the other journeys), pointed at a
fake SMTP server on 127.0.0.1 through the real `SmtpOutreachSender` (a genuine SMTP dialogue over a
socket, plain mode). Nothing here ever connects to a real mailbox.

NOT VERIFIED LIVE (labelled in docs/fe-integration-guide-sales-sequences.md):
  * delivery through the real cPanel mailbox (TLS on 465/587, AUTH, SPF/DKIM/DMARC alignment);
  * reading real bounce mail over IMAP: the DSN here is synthetic, handed to the poller through a
    stub mailbox, so the parsing/idempotency is proven but not a real server's DSN wording;
  * the configured `GET /sending` shape is captured IN-PROCESS (TestClient, same database), since
    the live server's env is unconfigured by design.

Captures: e2e/_captures/sales_b/*.json (verbatim source for the FE guide).
Keep app-internal imports LAZY (inside helpers): the remote live-e2e gate has no app env.
"""

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

G = "sales_b"
S = "/api/v1/sales"
PUBLIC = "/api/v1/public/sales/unsubscribe"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
    assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200


def _join(c, wh, ws, role, unique_email, mailbox):
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
    assert (
        c.post("/api/v1/invitations/accept", headers=auth, json={"token": token}).status_code == 200
    )
    return {**auth, "X-Workspace-Id": ws}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker import runner
    from app.worker.handlers import (
        sales_outreach,  # noqa: F401  (registers ai.sales.draft + the outreach jobs)
        scheduled,  # noqa: F401
    )

    register_notifications()
    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _sql(stmt: str, **params) -> None:
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text(stmt), params)
        db.commit()
    finally:
        db.close()


def test_sales_b_journey(
    base_url, make_verified_user, unique_email, mailbox, capture, capture_json
):
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.core.config import settings

    original = {
        k: getattr(settings, k)
        for k in type(settings).model_fields
        if k.startswith("SALES_OUTREACH_")
    }
    try:
        _journey(base_url, make_verified_user, unique_email, mailbox, capture, capture_json)
    finally:
        for k, v in original.items():
            setattr(settings, k, v)


def _journey(base_url, make_verified_user, unique_email, mailbox, capture, capture_json):
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.platform.outreach import outreach_configured
    from app.services.sales.bounces import poll_bounces
    from app.services.sales.sending import run_send_tick
    from tests.outreach_fakes import FakeMailbox, FakeSmtpServer, dsn

    assert not outreach_configured(), "e2e must start with the outreach mailbox UNSET"

    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth(token)
        _onboard(c, auth)
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        tm = _join(c, wh, ws, "team_member", unique_email, mailbox)
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)

        # 1. Sequences: create, validation errors, list, patch.
        steps = [
            {
                "wait_days": 0,
                "subject": "Quick idea for {{company}}",
                "body": "Hi {{first_name}},\n\nWe help teams like {{company}} get paid faster.\n\n{{sender_name}}",
            },
            {
                "wait_days": 3,
                "subject": "Re: Quick idea for {{company}}",
                "body": "Hi {{first_name}}, bumping this in case it got buried.\n\n{{sender_name}}",
            },
        ]
        r = c.post(f"{S}/sequences", headers=wh, json={"name": "Cold intro", "steps": steps})
        assert r.status_code == 201, r.text
        capture(G, "sequence_created", r)
        seq_id = r.json()["data"]["id"]
        bad = c.post(
            f"{S}/sequences",
            headers=wh,
            json={
                "name": "Bad",
                "steps": [{"wait_days": 0, "subject": "Hi {{nope}}", "body": "b"}],
            },
        )
        assert bad.status_code == 422, bad.text
        capture(G, "sequence_error_unknown_merge_tag", bad)
        empty = c.post(f"{S}/sequences", headers=wh, json={"name": "Bad", "steps": []})
        assert empty.status_code == 422
        capture(G, "sequence_error_no_steps", empty)
        lst = c.get(f"{S}/sequences", headers=wh)
        assert lst.json()["data"]["total"] == 1
        capture(G, "sequences_list", lst)

        # 2. Sending status while the mailbox is unset: the "Sending isn't set up yet" state.
        st = c.get(f"{S}/sending", headers=tm)
        assert st.status_code == 200 and st.json()["data"]["configured"] is False
        capture(G, "sending_not_configured", st)

        # 3. Contacts/leads, enrol (skip reasons), still no 500 while unconfigured.
        def mk_contact(first, email):
            r = c.post(
                f"{S}/contacts",
                headers=wh,
                json={"first_name": first, "last_name": "Test", "email": email},
            )
            assert r.status_code == 200, r.text
            return r.json()["data"]["id"]

        tag = uuid.uuid4().hex[:8]
        ada = mk_contact("Ada", f"ada-{tag}@prospect.example")
        bayo = mk_contact("Bayo", f"bayo-{tag}@prospect.example")
        chi = mk_contact("Chi", f"chi-{tag}@prospect.example")
        no_mail = c.post(f"{S}/contacts", headers=wh, json={"first_name": "Nomail"}).json()["data"][
            "id"
        ]
        lead = c.post(
            f"{S}/leads",
            headers=wh,
            json={
                "name": "Dayo Lead",
                "company": "Dayo Co",
                "email": f"dayo-{tag}@prospect.example",
            },
        ).json()["data"]["id"]
        enrol = c.post(
            f"{S}/sequences/{seq_id}/enroll",
            headers=tm,
            json={"contact_ids": [ada, bayo, chi, no_mail], "lead_ids": [lead]},
        )
        assert enrol.status_code == 200, enrol.text
        d = enrol.json()["data"]
        assert d["enrolled"] == 4 and d["skipped"] == [{"id": no_mail, "reason": "no_email"}]
        capture(G, "enroll_with_skips", enrol)
        again = c.post(f"{S}/sequences/{seq_id}/enroll", headers=wh, json={"contact_ids": [ada]})
        assert again.json()["data"]["skipped"][0]["reason"] == "already_active"
        capture(G, "enroll_already_active", again)
        by_email = {e["email"]: e for e in d["enrollments"]}
        e_ada, e_bayo, e_chi, e_dayo = (
            by_email[f"{n}-{tag}@prospect.example"]["id"] for n in ("ada", "bayo", "chi", "dayo")
        )

        # 4. A worker tick while unconfigured does nothing and does not crash.
        db = SessionLocal()
        try:
            res = run_send_tick(db)
        finally:
            db.close()
        assert res.configured is False and res.sent == 0

        # 5. Point the in-process worker at a fake SMTP server and send for real (over a socket).
        _sql(
            "UPDATE sequence_enrollments SET next_send_at = now() - interval '1 minute' WHERE sequence_id = :s",
            s=seq_id,
        )
        with FakeSmtpServer() as smtp:
            settings.SALES_OUTREACH_PROVIDER = "smtp"
            settings.SALES_OUTREACH_SMTP_HOST = "127.0.0.1"
            settings.SALES_OUTREACH_SMTP_PORT = smtp.port
            settings.SALES_OUTREACH_SMTP_SECURITY = "none"
            settings.SALES_OUTREACH_SMTP_USERNAME = "e2e-user"
            settings.SALES_OUTREACH_SMTP_PASSWORD = "e2e-not-a-secret"
            settings.SALES_OUTREACH_FROM_EMAIL = "outreach@mail.example.test"
            settings.SALES_OUTREACH_IMAP_HOST = "127.0.0.1"
            settings.SALES_OUTREACH_HOURLY_LIMIT = 30
            settings.API_PUBLIC_URL = base_url
            settings.APP_BASE_URL = "http://localhost:3000"

            db = SessionLocal()
            try:
                res = run_send_tick(db)
            finally:
                db.close()
            assert res.configured and res.sent == 4, res
            received = {rc[0]: m for rc, m in smtp.received}

        mail = received[f"ada-{tag}@prospect.example"]
        tm_email = c.get("/api/v1/auth/me", headers=tm).json()["data"]["user"]["email"]
        assert mail["Reply-To"] == tm_email  # the enrolling member, not the founder
        assert mail["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert mail["Subject"] == "Quick idea for"  # {{company}} is empty for a contact w/o account
        body = mail.get_body(("plain",)).get_content()
        assert "Hi Ada," in body and "{{" not in body
        capture_json(
            G,
            "delivered_message_headers",
            {
                "note": "Headers of the message the fake SMTP server received from SmtpOutreachSender; recipient is a placeholder",
                "From": mail["From"],
                "To": "ada-PLACEHOLDER@prospect.example",
                "Reply-To": "<the sending member's account email>",
                "Subject": mail["Subject"],
                "Message-ID": "<send-id@mail.example.test>",
                "X-Cofoundaz-Step": "<send-id>",
                "List-Unsubscribe": mail["List-Unsubscribe"].replace(
                    mail["List-Unsubscribe"].rsplit("/", 1)[1].rstrip(">"), "<signed-token>"
                ),
                "List-Unsubscribe-Post": mail["List-Unsubscribe-Post"],
                "body_text": body.replace(tag, "PLACEHOLDER").replace(
                    body.split("/unsubscribe/")[1].split()[0], "<signed-token>"
                ),
            },
        )
        en = c.get(f"{S}/sequences/{seq_id}/enrollments", headers=wh)
        assert {e["current_step"] for e in en.json()["data"]["items"]} == {1}
        capture(G, "enrollments_after_first_send", en)

        # 6. The configured `GET /sending` shape, in-process (the live server stays unset).
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as tc:
            conf = tc.get(f"{S}/sending", headers=wh)
        assert conf.status_code == 200 and conf.json()["data"]["configured"] is True
        capture_json(G, "sending_configured_in_process", conf.json())

        # 7. Public unsubscribe with the real token from the mail footer / header.
        tok = body.split("/unsubscribe/")[1].split()[0]
        page = c.get(f"{PUBLIC}/{tok}")
        assert page.status_code == 200 and page.json()["data"]["unsubscribed"] is False
        capture(G, "public_unsubscribe_status", page)
        one = c.post(
            f"{PUBLIC}/{tok}",
            content="List-Unsubscribe=One-Click",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert one.status_code == 200 and one.json()["data"]["unsubscribed"] is True
        capture(G, "public_unsubscribe_post", one)
        assert c.get(f"{PUBLIC}/{tok}").json()["data"]["unsubscribed"] is True
        forged = c.post(f"{PUBLIC}/{tok[:-3]}abc")
        assert forged.status_code == 404
        capture(G, "public_unsubscribe_bad_token", forged)
        ada_state = [
            e
            for e in c.get(f"{S}/sequences/{seq_id}/enrollments", headers=wh).json()["data"][
                "items"
            ]
            if e["id"] == e_ada
        ][0]
        assert ada_state["status"] == "unsubscribed"
        re_enrol = c.post(f"{S}/sequences/{seq_id}/enroll", headers=wh, json={"contact_ids": [ada]})
        assert re_enrol.json()["data"]["skipped"][0]["reason"] == "suppressed"
        capture(G, "enroll_suppressed", re_enrol)

        # 8. Bounce intake: a synthetic DSN for Bayo's step-1 mail goes through the poller.
        from sqlalchemy import text

        db = SessionLocal()
        try:
            send_id = db.execute(
                text("SELECT id FROM sequence_sends WHERE enrollment_id = :e"), {"e": e_bayo}
            ).scalar_one()
            box = FakeMailbox(
                messages=[(11, dsn(send_id=send_id, recipient=f"bayo-{tag}@prospect.example"))]
            )
            first = poll_bounces(db, box)
            second = poll_bounces(db, FakeMailbox(messages=list(box.messages)))
        finally:
            db.close()
        assert (first.bounced, second.seen) == (1, 0)
        bounced = c.get(f"{S}/sequences/{seq_id}/enrollments?status=bounced", headers=wh)
        assert bounced.json()["data"]["total"] == 1
        capture(G, "enrollments_filtered_bounced", bounced)

        # 9. Mark replied, stop, and the 409s.
        rep = c.post(f"{S}/enrollments/{e_chi}/replied", headers=tm)
        assert rep.status_code == 200 and rep.json()["data"]["status"] == "replied"
        capture(G, "enrollment_replied", rep)
        rep2 = c.post(f"{S}/enrollments/{e_chi}/replied", headers=tm)
        assert rep2.status_code == 409
        capture(G, "enrollment_error_not_active", rep2)
        stop = c.post(f"{S}/enrollments/{e_dayo}/stop", headers=wh)
        assert stop.json()["data"]["status"] == "stopped"
        capture(G, "enrollment_stopped", stop)

        # 10. Write with AI (stub LLM): 202 -> job -> ready.
        w = c.post(
            f"{S}/sequences/{seq_id}/steps/1/write", headers=wh, json={"instructions": "friendly"}
        )
        assert w.status_code == 202 and w.json()["data"]["status"] == "generating"
        capture(G, "draft_requested_202", w)
        _drain()
        done = c.get(f"{S}/drafts/{w.json()['data']['id']}", headers=wh)
        assert done.json()["data"]["status"] == "ready"
        capture(G, "draft_ready", done)

        # 11. Deal follow-up: draft, then the send reports "not configured" on the live server.
        stages = {s["name"]: s["id"] for s in c.get(f"{S}/stages", headers=wh).json()["data"]}
        deal = c.post(
            f"{S}/deals",
            headers=wh,
            json={"name": "Chi deal", "contact_id": chi, "stage_id": stages["Proposal"]},
        ).json()["data"]["id"]
        fu = c.post(f"{S}/deals/{deal}/follow-up", headers=wh)
        assert fu.status_code == 202
        capture(G, "follow_up_requested_202", fu)
        assert c.post(f"{S}/deals/{deal}/follow-up", headers=wh).status_code == 409
        _drain()
        got = c.get(f"{S}/deals/{deal}/follow-up", headers=wh)
        assert got.json()["data"]["status"] == "ready"
        capture(G, "follow_up_latest_ready", got)
        # the settings object in THIS process is configured, the server's is not: send via HTTP
        # therefore reports the not-configured state, which is what the FE shows.
        snd = c.post(
            f"{S}/deals/{deal}/follow-up/send",
            headers=wh,
            json={
                "draft_id": fu.json()["data"]["id"],
                "subject": "Next step",
                "body": "Can we talk?",
            },
        )
        assert snd.status_code == 409 and snd.json()["error"]["code"] == "SENDING_NOT_CONFIGURED"
        capture(G, "follow_up_send_not_configured", snd)

        # 12. Pause + resume (founder only), role gates, cross-workspace.
        _sql(
            "UPDATE sales_settings SET paused_at = now(), paused_reason = 'bounces' WHERE startup_id = :s",
            s=ws,
        )
        paused = c.get(f"{S}/sending", headers=tm)
        assert paused.json()["data"]["paused"] is True
        capture(G, "sending_paused", paused)
        denied = c.post(f"{S}/sending/resume", headers=tm)
        assert denied.status_code == 403
        capture(G, "resume_forbidden_team_member", denied)
        resumed = c.post(f"{S}/sending/resume", headers=wh)
        assert resumed.json()["data"]["paused"] is False
        capture(G, "resume_ok", resumed)
        m403 = c.get(f"{S}/sequences", headers=mentor)
        assert m403.status_code == 403
        capture(G, "sequences_mentor_403", m403)
        with httpx.Client(base_url=base_url, timeout=20.0) as c2:
            u2 = make_verified_user(c2)
            a2 = _auth(c2.post("/api/v1/auth/login", json=u2).json()["data"]["access_token"])
            _onboard(c2, a2)
            ws2 = c2.get("/api/v1/auth/me", headers=a2).json()["data"]["active_workspace_id"]
            h2 = {**a2, "X-Workspace-Id": ws2}
            cross = c2.get(f"{S}/sequences/{seq_id}", headers=h2)
            assert cross.status_code == 404
            capture(G, "sequence_cross_workspace_404", cross)

        # 13. Archive stops what is left.
        arch = c.delete(f"{S}/sequences/{seq_id}", headers=wh)
        assert arch.status_code == 200 and arch.json()["data"]["archived"] is True
        capture(G, "sequence_archived", arch)
    assert datetime.now(UTC)
