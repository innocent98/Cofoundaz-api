"""Live Finance invoice automation journey (slice b): PDF, overdue notice, reminders, paid + runway events.

A founder sends an NGN invoice (the client mail carries `invoice-{number}.pdf`), downloads the PDF,
and the invoice then goes overdue. The scheduler tick and the worker run IN-PROCESS in this test
(the e2e harness has no separate worker container, same pattern as
`e2e/test_notifications_scheduler.py`), against the SAME database and file mailbox as the live
uvicorn server. What is therefore exercised live is our side: routes, RBAC, the scheduler scan, the
row-locked handler, the file email sender (with attachments), the notification fan-out and the
read filter. Real Resend delivery of attachments is NOT exercised (file sender only).

Walk: invite team_member / accountant / mentor -> NGN-only 422 -> create + send (PDF in the mail)
-> GET .../pdf -> /remind on a not-overdue invoice 422 -> backdate `due_on` (the only way to age a
live invoice) -> tick + drain -> overdue notification (founder / team member / accountant yes,
mentor no) -> reminder mail (subject, PDF) -> second tick: no duplicate -> /remind inside 24h 422
-> auto_remind off: overdue notice but no reminder, manual /remind works once -> role 403s and
cross-tenant 404 -> mark paid -> paid notification -> runway band flip (healthy -> low -> healthy)
-> `finance.runway.updated` notifications, exactly one per flip.

Every response body is captured, complete and untrimmed, to
`e2e/_captures/invoice_automation/*.json` (the verbatim source for
docs/fe-integration-guide-finance-invoices-automation.md). Delivered mails are captured as the JSON
the file sender wrote, plus a small descriptor of the attached PDF.

Not live-verifiable here (covered by unit tests, labelled in the FE guide): the 7-day auto-reminder
spacing and the 8-reminder cap (they need real elapsed time; the DB clock is not moved), and the
two-workers lock race.
"""

import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

G = "invoice_automation"
MAIL_DIR = os.environ.get("E2E_MAIL_DIR", "./var/mail-e2e")
STARTUP = "Automation Co"

# NOTE: app-internal imports (SessionLocal, worker, scheduler) are LAZY, inside the helpers.


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard(c, auth: dict, *, founder: str, startup: str) -> str:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": founder})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": startup})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text
    return c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]


def _join(c, wh, ws, role, unique_email, mailbox):
    """Invite `role` to the workspace and accept as a freshly verified user."""
    email = unique_email(role)
    r = c.post(
        "/api/v1/team/invitations", headers=wh, json={"invites": [{"email": email, "role": role}]}
    )
    assert r.status_code == 200, r.text
    token = mailbox.latest_token_for(email, subject_contains="invited")
    pw = f"Member-{uuid.uuid4().hex[:10]}-9"
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


def _tick_and_drain() -> int:
    """One scheduler tick at the real clock, then drain the worker queue in-process.

    Separate sessions, as the real worker loop does. The notification registry is subscribed
    here too: the app only subscribes it at import of the API router, in the SERVER process, and
    the overdue event is published in THIS process.
    """
    from app.db.session import SessionLocal
    from app.services.notifications.registry import register as register_notifications
    from app.worker.handlers import (  # noqa: F401  (registers the job handlers)
        invoice_email,
        scheduled,
    )
    from app.worker.scheduler import scheduler_tick

    register_notifications()
    db = SessionLocal()
    try:
        enqueued = scheduler_tick(db, now=datetime.now(UTC))
    finally:
        db.close()
    drain()
    return enqueued


def drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import (  # noqa: F401  (registers the job handlers)
        invoice_email,
        scheduled,
    )

    db = SessionLocal()
    try:
        for _ in range(500):  # the jobs table is shared with every earlier journey
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _backdate_due_on(invoice_id: str, due_on: date) -> None:
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE invoices SET due_on = :due_on WHERE id = :id"),
            {"due_on": due_on, "id": uuid.UUID(invoice_id)},
        )
        db.commit()
    finally:
        db.close()


def _mails_for(email: str) -> list[tuple[Path, dict]]:
    out = []
    for f in sorted(Path(MAIL_DIR).glob("*.json")):
        data = json.loads(f.read_text())
        if data["to"].lower() == email.lower():
            out.append((f, data))
    return out


def _mail_with_subject(email: str, subject: str) -> list[tuple[Path, dict]]:
    return [(f, d) for f, d in _mails_for(email) if d["subject"] == subject]


def _attached_pdf(mail_file: Path, name: str) -> bytes:
    return (mail_file.with_suffix(".attachments") / name).read_bytes()


def _types(resp: httpx.Response, kind: str) -> list[dict]:
    return [n for n in resp.json()["data"]["notifications"] if n["type"] == kind]


def test_invoice_automation_journey(
    base_url, make_verified_user, unique_email, mailbox, capture, capture_json
):
    cap = capture
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        # 0. Onboard a founder, invite the other roles.
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        ws = _onboard(c, auth, founder="Ada Founder", startup=STARTUP)
        wh = {**auth, "X-Workspace-Id": ws}
        team = _join(c, wh, ws, "team_member", unique_email, mailbox)
        accountant = _join(c, wh, ws, "accountant", unique_email, mailbox)
        mentor = _join(c, wh, ws, "mentor", unique_email, mailbox)

        # A second, unrelated workspace for the cross-tenant checks.
        other_user = make_verified_user(c)
        other_auth = _auth(
            c.post("/api/v1/auth/login", json=other_user).json()["data"]["access_token"]
        )
        other_ws = _onboard(c, other_auth, founder="Other Founder", startup="Other Co")
        other = {**other_auth, "X-Workspace-Id": other_ws}

        def body(name: str, email: str, **extra) -> dict:
            return {
                "client_name": name,
                "client_email": email,
                "line_items": [
                    {"description": "Retainer", "quantity": 2, "unit_price_minor": 75_000}
                ],
                "tax_percent": 7.5,
                "terms": "net_15",
                **extra,
            }

        # 1. NGN only (D-FIN-6): any other currency is a 422 on create and on edit.
        usd = c.post(
            "/api/v1/finance/invoices",
            headers=wh,
            json=body("Dollar Client", "delivered+usd@resend.dev", currency="USD"),
        )
        assert usd.status_code == 422, usd.text
        cap(G, "error_create_non_ngn_currency", usd)

        # 2. Create the auto-remind invoice (client "Chidi Okafor Stores" -> reminder says "Hi Chidi").
        email1 = unique_email("client1")
        created = c.post(
            "/api/v1/finance/invoices", headers=wh, json=body("Chidi Okafor Stores", email1)
        )
        assert created.status_code == 200, created.text
        inv1 = created.json()["data"]
        cap(G, "invoice_created", created)
        assert inv1["last_reminded_at"] is None and inv1["reminder_count"] == 0
        assert inv1["overdue_notified_at"] is None
        edit_ccy = c.patch(
            f"/api/v1/finance/invoices/{inv1['id']}", headers=wh, json={"currency": "EUR"}
        )
        assert edit_ccy.status_code == 422, edit_ccy.text
        cap(G, "error_patch_non_ngn_currency", edit_ccy)
        auto_on = c.patch(
            f"/api/v1/finance/invoices/{inv1['id']}", headers=wh, json={"auto_remind": True}
        )
        assert auto_on.status_code == 200 and auto_on.json()["data"]["auto_remind"] is True
        cap(G, "invoice_auto_remind_on", auto_on)
        number1 = inv1["number"]

        # 2b. Reminding a draft is a 422 (nothing has been sent, nothing is overdue).
        draft_remind = c.post(f"/api/v1/finance/invoices/{inv1['id']}/remind", headers=wh)
        assert draft_remind.status_code == 422, draft_remind.text
        cap(G, "error_remind_draft", draft_remind)

        # 3. The PDF exists for a draft too.
        draft_pdf = c.get(f"/api/v1/finance/invoices/{inv1['id']}/pdf", headers=wh)
        assert draft_pdf.status_code == 200, draft_pdf.text
        assert draft_pdf.content.startswith(b"%PDF-")

        # 4. Send: the client mail carries the PDF.
        sent = c.post(f"/api/v1/finance/invoices/{inv1['id']}/send", headers=wh)
        assert sent.status_code == 200, sent.text
        cap(G, "invoice_sent", sent)
        drain()
        send_subject = f"Invoice {number1} from {STARTUP}"
        send_mails = _mail_with_subject(email1, send_subject)
        assert len(send_mails) == 1
        send_file, send_mail = send_mails[0]
        assert send_mail["attachments"] == [f"invoice-{number1}.pdf"]
        sent_pdf = _attached_pdf(send_file, f"invoice-{number1}.pdf")
        assert sent_pdf.startswith(b"%PDF-") and number1.encode() in sent_pdf
        capture_json(G, "mail_invoice_sent", {**send_mail, "attachment_bytes": len(sent_pdf)})

        # 5. Download the PDF.
        pdf = c.get(f"/api/v1/finance/invoices/{inv1['id']}/pdf", headers=wh)
        assert pdf.status_code == 200, pdf.text
        assert pdf.content.startswith(b"%PDF-") and pdf.content.rstrip().endswith(b"%%EOF")
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["content-disposition"] == f'attachment; filename="invoice-{number1}.pdf"'
        assert number1.encode() in pdf.content and b"Chidi Okafor Stores" in pdf.content
        capture_json(
            G,
            "pdf_response_headers",
            {
                "status_code": pdf.status_code,
                "content-type": pdf.headers["content-type"],
                "content-disposition": pdf.headers["content-disposition"],
                "body_starts_with": pdf.content[:8].decode("latin-1"),
                "body_bytes": len(pdf.content),
            },
        )

        # 6. Not yet overdue: /remind is a 422.
        early = c.post(f"/api/v1/finance/invoices/{inv1['id']}/remind", headers=wh)
        assert early.status_code == 422, early.text
        cap(G, "error_remind_not_overdue", early)

        # 7. A second sent invoice with auto_remind OFF (the default).
        email2 = unique_email("client2")
        inv2 = c.post(
            "/api/v1/finance/invoices", headers=wh, json=body("Ngozi Bello Ltd", email2)
        ).json()["data"]
        assert inv2["auto_remind"] is False
        sent2 = c.post(f"/api/v1/finance/invoices/{inv2['id']}/send", headers=wh)
        assert sent2.status_code == 200, sent2.text
        drain()

        # 8. Age both invoices: backdate `due_on` (no API sets it). The scheduler has not run.
        today = datetime.now(UTC).date()
        _backdate_due_on(inv1["id"], today - timedelta(days=3))
        _backdate_due_on(inv2["id"], today - timedelta(days=3))
        before = c.get(f"/api/v1/finance/invoices/{inv1['id']}", headers=wh)
        b = before.json()["data"]
        assert b["status"] == "overdue"
        assert b["overdue_notified_at"] is None and b["reminder_count"] == 0
        cap(G, "invoice_overdue_before_scan", before)

        # 9. Scheduler tick + worker drain.
        enqueued = _tick_and_drain()
        assert enqueued >= 2  # at least our two invoices (other journeys' overdue ones too)
        after1 = c.get(f"/api/v1/finance/invoices/{inv1['id']}", headers=wh)
        a1 = after1.json()["data"]
        assert a1["status"] == "overdue" and a1["reminder_count"] == 1
        assert a1["overdue_notified_at"] is not None and a1["last_reminded_at"] is not None
        cap(G, "invoice_after_scan_auto_remind_on", after1)
        after2 = c.get(f"/api/v1/finance/invoices/{inv2['id']}", headers=wh)
        a2 = after2.json()["data"]
        assert a2["overdue_notified_at"] is not None  # the overdue event fires regardless
        assert a2["reminder_count"] == 0 and a2["last_reminded_at"] is None  # no auto reminder
        cap(G, "invoice_after_scan_auto_remind_off", after2)

        # 10. Overdue notification: founder, team member, accountant yes; mentor no.
        reminders_subject = f"Reminder: invoice {number1} from {STARTUP}"
        feeds = {}
        for who, hdr in (("founder", wh), ("team", team), ("accountant", accountant)):
            feed = c.get("/api/v1/notifications", headers=hdr, params={"limit": 100})
            assert feed.status_code == 200, feed.text
            feeds[who] = feed
            mine = [
                n for n in _types(feed, "finance.invoice.overdue") if n["data"]["number"] == number1
            ]
            assert len(mine) == 1, who
            assert mine[0]["body"] == "Send reminder"
            assert mine[0]["title"].startswith(f"Invoice overdue: {number1} · ")
            assert "Chidi Okafor Stores" in mine[0]["title"]
            assert mine[0]["data"]["href"] == "/finance/invoices"
            cap(G, f"notifications_{who}_after_overdue_scan", feed)
        mentor_feed = c.get("/api/v1/notifications", headers=mentor, params={"limit": 100})
        assert mentor_feed.status_code == 200, mentor_feed.text
        assert not [
            n for n in mentor_feed.json()["data"]["notifications"] if "finance" in n["type"]
        ]
        cap(G, "notifications_mentor_after_overdue_scan", mentor_feed)
        mentor_unread = c.get("/api/v1/notifications/unread-count", headers=mentor)
        cap(G, "notifications_mentor_unread_count", mentor_unread)

        # 11. The reminder mail: exact subject, PDF attached, friendly body, only one.
        rem = _mail_with_subject(email1, reminders_subject)
        assert len(rem) == 1
        rem_file, rem_mail = rem[0]
        assert rem_mail["attachments"] == [f"invoice-{number1}.pdf"]
        assert rem_mail["html"].count("Hi Chidi, a friendly nudge that invoice") == 1
        assert f"invoice {number1} for 1,612.50 NGN was due on" in rem_mail["html"]
        assert "Thanks, Ada Founder" in rem_mail["html"]
        assert "\u2014" not in rem_mail["html"] and "\u2013" not in rem_mail["html"]
        assert _attached_pdf(rem_file, f"invoice-{number1}.pdf").startswith(b"%PDF-")
        capture_json(G, "mail_reminder_auto", rem_mail)
        # No auto reminder for the auto_remind-off invoice.
        assert not _mail_with_subject(email2, f"Reminder: invoice {inv2['number']} from {STARTUP}")

        # 12. A second tick the same day: no duplicate reminder, no duplicate notification.
        _tick_and_drain()
        assert len(_mail_with_subject(email1, reminders_subject)) == 1
        again = c.get(f"/api/v1/finance/invoices/{inv1['id']}", headers=wh)
        assert again.json()["data"]["reminder_count"] == 1
        assert again.json()["data"]["last_reminded_at"] == a1["last_reminded_at"]
        cap(G, "invoice_after_second_tick", again)
        feed_again = c.get("/api/v1/notifications", headers=wh, params={"limit": 100})
        assert (
            len(
                [
                    n
                    for n in _types(feed_again, "finance.invoice.overdue")
                    if n["data"]["number"] == number1
                ]
            )
            == 1
        )

        # 13. Manual remind: 422 inside 24h of the auto reminder; works for the auto-off invoice once.
        too_soon = c.post(f"/api/v1/finance/invoices/{inv1['id']}/remind", headers=wh)
        assert too_soon.status_code == 422, too_soon.text
        cap(G, "error_remind_within_24h", too_soon)
        manual = c.post(f"/api/v1/finance/invoices/{inv2['id']}/remind", headers=team)
        assert manual.status_code == 200, manual.text
        m = manual.json()["data"]
        assert m["reminder_count"] == 1 and m["last_reminded_at"] is not None
        cap(G, "remind_manual_ok", manual)
        drain()
        sub2 = f"Reminder: invoice {inv2['number']} from {STARTUP}"
        manual_mail = _mail_with_subject(email2, sub2)
        assert len(manual_mail) == 1 and "Hi Ngozi, a friendly nudge" in manual_mail[0][1]["html"]
        capture_json(G, "mail_reminder_manual", manual_mail[0][1])
        second_manual = c.post(f"/api/v1/finance/invoices/{inv2['id']}/remind", headers=wh)
        assert second_manual.status_code == 422, second_manual.text
        cap(G, "error_remind_manual_twice", second_manual)

        # 14. Role gates and tenancy on /remind and /pdf.
        mentor_remind = c.post(f"/api/v1/finance/invoices/{inv1['id']}/remind", headers=mentor)
        assert mentor_remind.status_code == 403, mentor_remind.text
        cap(G, "error_remind_mentor_forbidden", mentor_remind)
        mentor_pdf = c.get(f"/api/v1/finance/invoices/{inv1['id']}/pdf", headers=mentor)
        assert mentor_pdf.status_code == 403, mentor_pdf.text
        cap(G, "error_pdf_mentor_forbidden", mentor_pdf)
        assert (
            c.get(f"/api/v1/finance/invoices/{inv1['id']}/pdf", headers=accountant).status_code
            == 200
        )
        assert c.get(f"/api/v1/finance/invoices/{inv1['id']}/pdf", headers=team).status_code == 200
        x_remind = c.post(f"/api/v1/finance/invoices/{inv1['id']}/remind", headers=other)
        assert x_remind.status_code == 404, x_remind.text
        cap(G, "error_remind_cross_tenant", x_remind)
        x_pdf = c.get(f"/api/v1/finance/invoices/{inv1['id']}/pdf", headers=other)
        assert x_pdf.status_code == 404, x_pdf.text
        cap(G, "error_pdf_cross_tenant", x_pdf)
        unauth = c.get(f"/api/v1/finance/invoices/{inv1['id']}/pdf")
        assert unauth.status_code == 401, unauth.text
        cap(G, "error_pdf_unauthenticated", unauth)

        # 15. Mark paid: the paid notification (founder / team member / accountant, not mentor),
        # and no further reminder is ever sent.
        paid = c.post(f"/api/v1/finance/invoices/{inv1['id']}/mark-paid", headers=wh)
        assert paid.status_code == 200, paid.text
        assert paid.json()["data"]["status"] == "paid"
        cap(G, "invoice_mark_paid", paid)
        for who, hdr in (("founder", wh), ("team", team), ("accountant", accountant)):
            feed = c.get("/api/v1/notifications", headers=hdr, params={"limit": 100})
            mine = [
                n for n in _types(feed, "finance.invoice.paid") if n["data"]["number"] == number1
            ]
            assert len(mine) == 1, who
            assert mine[0]["title"] == f"Invoice paid: {number1} \U0001f389"
            assert mine[0]["body"] == "View invoice"
            assert mine[0]["data"]["paid_via"] is None
            if who == "founder":
                cap(G, "notifications_founder_after_paid", feed)
        mentor_feed2 = c.get("/api/v1/notifications", headers=mentor, params={"limit": 100})
        assert not [
            n for n in mentor_feed2.json()["data"]["notifications"] if "finance" in n["type"]
        ]
        idem = c.post(f"/api/v1/finance/invoices/{inv1['id']}/mark-paid", headers=wh)
        assert idem.status_code == 200
        feed_idem = c.get("/api/v1/notifications", headers=wh, params={"limit": 100})
        assert (
            len(
                [
                    n
                    for n in _types(feed_idem, "finance.invoice.paid")
                    if n["data"]["number"] == number1
                ]
            )
            == 1
        )
        _tick_and_drain()
        assert len(_mail_with_subject(email1, reminders_subject)) == 1  # paid: no more mail
        paid_remind = c.post(f"/api/v1/finance/invoices/{inv1['id']}/remind", headers=wh)
        assert paid_remind.status_code == 422, paid_remind.text
        cap(G, "error_remind_paid", paid_remind)

        # 16. Runway band flips -> finance.runway.updated (and only on a flip).
        def txn(day: date, amount: int, direction: str, desc: str) -> httpx.Response:
            r = c.post(
                "/api/v1/finance/transactions",
                headers=wh,
                json={
                    "date": day.isoformat(),
                    "description": desc,
                    "category": "Fundraising" if direction == "in" else "Capex",
                    "amount_minor": amount,
                    "currency": "NGN",
                    "direction": direction,
                },
            )
            assert r.status_code == 200, r.text
            return r

        def updated() -> list[dict]:
            f = c.get("/api/v1/notifications", headers=wh, params={"limit": 100})
            return _types(f, "finance.runway.updated")

        assert updated() == []  # the paid invoice alone did not flip the band
        y, mth = today.year, today.month - 4
        while mth <= 0:
            mth += 12
            y -= 1
        txn(date(y, mth, 15), 100_000_000, "in", "Pre-seed round")  # cash only (outside window)
        assert updated() == []  # healthy -> healthy: nothing
        low = txn(today, 90_000_000, "out", "Office build-out")  # runway well under 6 months
        cap(G, "transaction_pushing_low", low)
        ups = updated()
        assert len(ups) == 1
        assert ups[0]["data"]["band"] == "low" and ups[0]["body"] == "Open Finance"
        assert ups[0]["title"].startswith("Your runway is now ") and ups[0]["title"].endswith(
            " months"
        )
        assert ups[0]["data"]["href"] == "/finance/runway"
        low_feed = c.get("/api/v1/notifications", headers=wh, params={"limit": 100})
        lows = _types(low_feed, "finance.runway.low")
        assert len(lows) == 1
        assert lows[0]["title"] == "Runway below 6 months, review recommended"
        assert lows[0]["body"] == "Open Finance"
        cap(G, "notifications_founder_after_runway_low", low_feed)
        txn(today, 1_000_000, "out", "More spend")  # still low: no second update
        assert len(updated()) == 1
        recover = txn(today, 400_000_000, "in", "Bridge round")
        cap(G, "transaction_recovering", recover)
        ups2 = updated()
        assert len(ups2) == 2
        assert {n["data"]["band"] for n in ups2} == {"low", "healthy"}
        healthy = next(n for n in ups2 if n["data"]["band"] == "healthy")
        assert healthy["title"] == "Your runway is healthy"
        final_feed = c.get("/api/v1/notifications", headers=wh, params={"limit": 100})
        cap(G, "notifications_founder_after_runway_recovered", final_feed)
        for hdr in (team, accountant):
            f = c.get("/api/v1/notifications", headers=hdr, params={"limit": 100})
            assert len(_types(f, "finance.runway.updated")) == 2
        mentor_final = c.get("/api/v1/notifications", headers=mentor, params={"limit": 100})
        assert not [
            n for n in mentor_final.json()["data"]["notifications"] if "finance" in n["type"]
        ]
        listing = c.get("/api/v1/finance/invoices", headers=wh)
        assert listing.status_code == 200
        cap(G, "invoices_list", listing)
