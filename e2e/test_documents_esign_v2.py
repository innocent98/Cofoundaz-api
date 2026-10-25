"""Live Documents BE-D journey: e-sign v2 (signing order, viewed, message, executed PDF + audit
certificate, link re-copy) and link shares of documents and files.

Cast: a founder, two external signers, a mentor who is refused. Everything runs against the live
server; the in-process worker drain builds the executed PDF and emails the parties (the server
and this process share the file mail backend). Bodies are captured to
`e2e/_captures/documents_esign_v2/*.json`, the verbatim source for
`docs/fe-integration-guide-documents-esign-v2.md`. Raw tokens in links are replaced with
`REDACTED_TOKEN` BEFORE the capture is written. Not exercised live: the 90-day executed-link
window, a Cloudinary-backed storage (local files here) and the real SMTP/Resend hop.
"""

import io
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

G = "documents_esign_v2"
_TOKEN = re.compile(r"((?:token=)|(?:/sign/)|(?:/shared/))[A-Za-z0-9_\-\.]{20,}")
_LINK = re.compile(r'href="([^"]*/(?:sign|shared)/[^"]+)"')


def _redact(text: str) -> str:
    return _TOKEN.sub(r"\1REDACTED_TOKEN", text)


def _pdf() -> bytes:
    buf = io.BytesIO()
    canvas = Canvas(buf)
    canvas.drawString(72, 720, "Founders agreement (e2e)")
    canvas.showPage()
    canvas.save()
    return buf.getvalue()


def _mail_dir() -> Path:
    return Path(os.environ.get("E2E_MAIL_DIR", "./var/mail-e2e"))


def _mails_for(email: str) -> list[dict]:
    out = []
    for f in sorted(_mail_dir().glob("*.json")):
        data = json.loads(f.read_text())
        if data["to"].lower() == email.lower():
            data["_path"] = f
            out.append(data)
    return out


def _sign_link_in(mail: dict) -> str:
    m = _LINK.search(mail["html"])
    assert m, mail["html"]
    return m.group(1)


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import documents_sign as _sign  # noqa: F401  (registers the job)

    db = SessionLocal()
    try:
        for _ in range(50):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_documents_esign_v2_journey(
    base_url, make_verified_user, unique_email, mailbox, capture_json
):
    def cap(name, resp):
        capture_json(G, name, {"status": resp.status_code, "body": json.loads(_redact(resp.text))})

    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        auth = {
            "Authorization": "Bearer "
            + c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        }
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch(
            "/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"}
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "NovaPay"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        done = c.post("/api/v1/onboarding/complete", headers=auth)
        assert done.status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        founder = {**auth, "X-Workspace-Id": ws}

        m_email = unique_email("mentor")
        inv = c.post(
            "/api/v1/team/invitations",
            headers=founder,
            json={"invites": [{"email": m_email, "role": "mentor"}]},
        )
        assert inv.status_code == 200, inv.text
        invite_token = mailbox.latest_token_for(m_email, subject_contains="invited")
        pw = "Member-" + m_email.split("@")[0][-8:] + "-9"
        c.post("/api/v1/auth/signup", json={"email": m_email, "password": pw})
        c.post(
            "/api/v1/auth/verify",
            json={"token": mailbox.latest_token_for(m_email, subject_contains="Verify")},
        )
        m_access = c.post("/api/v1/auth/login", json={"email": m_email, "password": pw}).json()[
            "data"
        ]["access_token"]
        accepted = c.post(
            "/api/v1/invitations/accept",
            headers={"Authorization": f"Bearer {m_access}"},
            json={"token": invite_token},
        )
        assert accepted.status_code == 200, accepted.text
        mentor = {"Authorization": f"Bearer {m_access}", "X-Workspace-Id": ws}

        # ---- file link share: anyone with the link, custom expiry ------------------------------
        up = c.post(
            "/api/v1/documents/files",
            headers=founder,
            files={"file": ("founders-agreement.pdf", io.BytesIO(_pdf()), "application/pdf")},
        )
        assert up.status_code == 201, up.text
        file_id = up.json()["data"]["id"]

        expiry = (datetime.now(UTC) + timedelta(days=10)).replace(microsecond=0).isoformat()
        past = c.post(
            f"/api/v1/documents/files/{file_id}/shares",
            headers=founder,
            json={"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
        )
        assert past.status_code == 422
        cap("file_share_expiry_in_the_past_422", past)
        fs = c.post(
            f"/api/v1/documents/files/{file_id}/shares",
            headers=founder,
            json={"expires_at": expiry},
        )
        assert fs.status_code == 201, fs.text
        cap("file_share_create", fs)
        share = fs.json()["data"]
        token = share["link"].rsplit("/", 1)[-1]
        assert share["anyone_with_link"] is True and share["kind"] == "file"

        listed = c.get(f"/api/v1/documents/files/{file_id}/shares", headers=founder)
        assert listed.json()["data"]["shares"][0]["link"] == share["link"]  # copy again
        cap("file_share_list", listed)
        meta = c.get(f"/api/v1/shared/{token}")  # public: no auth
        assert meta.status_code == 200 and meta.json()["data"]["kind"] == "file"
        cap("file_share_public_open", meta)
        served = c.get(f"/api/v1/shared/{token}/file")
        assert served.status_code == 200 and served.content.startswith(b"%PDF")
        capture_json(
            G,
            "file_share_public_file_headers",
            {
                "status": served.status_code,
                "content_type": served.headers["content-type"],
                "content_disposition": served.headers["content-disposition"],
            },
        )
        files = c.get("/api/v1/documents/files", headers=founder).json()["data"]["files"]
        assert files[0]["share_count"] == 1
        capture_json(G, "file_row_with_share_count", files[0])

        longer = (datetime.now(UTC) + timedelta(days=20)).replace(microsecond=0).isoformat()
        patched = c.patch(
            f"/api/v1/documents/files/{file_id}/shares/{share['id']}",
            headers=founder,
            json={"expires_at": longer},
        )
        assert patched.status_code == 200
        cap("file_share_change_expiry", patched)
        revoked = c.delete(
            f"/api/v1/documents/files/{file_id}/shares/{share['id']}", headers=founder
        )
        assert revoked.status_code == 200
        gone = c.get(f"/api/v1/shared/{token}")
        assert gone.status_code == 404
        cap("file_share_public_after_revoke_404", gone)

        # ---- link share of a rich document (no email) ------------------------------------------
        doc = c.post("/api/v1/documents", headers=founder, json={"template_key": "one_pager"})
        assert doc.status_code == 201, doc.text
        doc_id = doc.json()["data"]["id"]
        ds = c.post(
            f"/api/v1/documents/{doc_id}/shares", headers=founder, json={"expires_in_days": None}
        )
        assert ds.status_code == 201, ds.text
        cap("document_link_share_never_expires", ds)
        assert ds.json()["data"]["expires_at"] is None
        assert (
            c.get(f"/api/v1/shared/{ds.json()['data']['link'].rsplit('/', 1)[-1]}").json()["data"][
                "kind"
            ]
            == "document"
        )

        # ---- what can be signed: DOCX export is refused, PDF export is accepted -----------------
        dx = c.post(f"/api/v1/documents/{doc_id}/export", headers=founder, json={"format": "docx"})
        assert dx.status_code == 201, dx.text
        refused = c.post(
            f"/api/v1/documents/files/{dx.json()['data']['id']}/signature-requests",
            headers=founder,
            json={"signers": [{"email": unique_email("x")}]},
        )
        assert refused.status_code == 422
        cap("sign_docx_422_pdf_required", refused)
        px = c.post(f"/api/v1/documents/{doc_id}/export", headers=founder, json={"format": "pdf"})
        assert px.status_code == 201, px.text
        exported_pdf_id = px.json()["data"]["id"]
        junk = c.post(
            "/api/v1/documents/files",
            headers=founder,
            files={
                "file": ("broken.pdf", io.BytesIO(b"%PDF-1.4 not a real pdf"), "application/pdf")
            },
        )
        assert junk.status_code == 201
        unreadable = c.post(
            f"/api/v1/documents/files/{junk.json()['data']['id']}/signature-requests",
            headers=founder,
            json={"signers": [{"email": unique_email("x")}]},
        )
        assert unreadable.status_code == 422
        cap("sign_broken_pdf_422_unreadable", unreadable)
        bad_email = c.post(
            f"/api/v1/documents/files/{file_id}/signature-requests",
            headers=founder,
            json={"signers": [{"email": "a@x.com\r\nBcc: evil@x.com"}]},
        )
        assert bad_email.status_code == 422
        cap("sign_bad_signer_email_422", bad_email)
        too_long = c.post(
            f"/api/v1/documents/files/{file_id}/signature-requests",
            headers=founder,
            json={"signers": [{"email": unique_email("x")}], "expires_in_days": 100000},
        )
        assert too_long.status_code == 422
        cap("sign_expires_in_days_out_of_bounds_422", too_long)

        # ---- sequential request with a custom message ------------------------------------------
        s1, s2 = unique_email("signer-one"), unique_email("signer-two")
        sent = c.post(
            f"/api/v1/documents/files/{file_id}/signature-requests",
            headers=founder,
            json={
                "title": "Founders agreement",
                "sequential": True,
                "message": "Hi both, please sign before Friday.\nThanks, Ada",
                "expires_in_days": 14,
                "signers": [
                    {"email": s1, "name": "Ada Investor"},
                    {"email": s2, "name": "Bello Legal"},
                ],
            },
        )
        assert sent.status_code == 201, sent.text
        cap("request_create_sequential", sent)
        req = sent.json()["data"]
        assert req["signer_links"][1] is None and req["sequential"] is True
        assert len(_mails_for(s1)) == 1 and _mails_for(s2) == []  # only the first signer emailed
        capture_json(G, "email_to_first_signer", _redact_mail(_mails_for(s1)[0]))
        link1 = _sign_link_in(_mails_for(s1)[0])
        assert link1 == req["signer_links"][0]
        t1 = link1.rsplit("/", 1)[-1]

        # a mentor cannot see the request or copy a link
        refused_detail = c.get(f"/api/v1/documents/signature-requests/{req['id']}", headers=mentor)
        assert refused_detail.status_code == 403
        cap("mentor_request_detail_403", refused_detail)
        assert (
            c.get(
                f"/api/v1/documents/signature-requests/{req['id']}/signers/{req['signers'][0]['id']}/link",
                headers=mentor,
            ).status_code
            == 403
        )

        # copy a link for the not-yet-released signer: refused
        s2_id = req["signers"][1]["id"]
        early = c.get(
            f"/api/v1/documents/signature-requests/{req['id']}/signers/{s2_id}/link",
            headers=founder,
        )
        assert early.status_code == 409
        cap("copy_link_not_released_409", early)

        # the first signer opens the page -> Viewed
        view = c.get(f"/api/v1/sign/{t1}")
        assert view.status_code == 200
        cap("sign_view", view)
        detail = c.get(f"/api/v1/documents/signature-requests/{req['id']}", headers=founder)
        statuses = [s["status"] for s in detail.json()["data"]["signers"]]
        assert statuses == ["viewed", "waiting"], statuses
        cap("request_detail_after_first_view", detail)

        copy = c.get(
            f"/api/v1/documents/signature-requests/{req['id']}/signers/{req['signers'][0]['id']}/link",
            headers=founder,
        )
        assert copy.status_code == 200 and copy.json()["data"]["link"] == link1
        cap("copy_link_again", copy)

        # signer one signs -> signer two is released and emailed
        sign1 = c.post(
            f"/api/v1/sign/{t1}",
            json={"typed_name": "Ada Investor"},
            headers={"user-agent": "e2e-browser/1.0 (signer one)"},
        )
        assert sign1.status_code == 200, sign1.text
        cap("sign_first", sign1)
        assert len(_mails_for(s2)) == 1
        t2 = _sign_link_in(_mails_for(s2)[0]).rsplit("/", 1)[-1]
        replay = c.post(f"/api/v1/sign/{t1}", json={"typed_name": "Ada Investor"})
        assert replay.status_code == 404
        cap("sign_replay_404", replay)

        sign2 = c.post(
            f"/api/v1/sign/{t2}",
            json={"typed_name": "Bello Legal"},
            headers={"user-agent": "e2e-browser/1.0 (signer two)"},
        )
        assert sign2.status_code == 200 and sign2.json()["data"]["status"] == "complete"
        cap("sign_last_completes", sign2)

        # before the worker runs: complete, executed pending
        pending = c.get(f"/api/v1/documents/signature-requests/{req['id']}", headers=founder)
        assert pending.json()["data"]["executed"]["state"] == "pending"
        cap("request_detail_complete_executed_pending", pending)
        nr = c.get(f"/api/v1/documents/signature-requests/{req['id']}/executed", headers=founder)
        assert nr.status_code == 409
        cap("executed_not_ready_409", nr)
        nr2 = c.get(f"/api/v1/sign/{t1}/executed")
        assert nr2.status_code == 409
        cap("signer_executed_not_ready_409", nr2)

        _drain()

        done = c.get(f"/api/v1/documents/signature-requests/{req['id']}", headers=founder)
        d = done.json()["data"]
        assert d["executed"]["state"] == "ready"
        cap("request_detail_complete_executed_ready", done)
        assert d["signers"][0]["signed_ip"] and d["signers"][0]["signed_user_agent"].startswith(
            "e2e-browser"
        )

        link = c.get(f"/api/v1/documents/signature-requests/{req['id']}/executed", headers=founder)
        assert link.status_code == 200
        cap("executed_link_for_editors", link)
        pdf = c.get(link.json()["data"]["url"])
        assert pdf.status_code == 200
        reader = PdfReader(io.BytesIO(pdf.content))
        text = "\n".join(p.extract_text() for p in reader.pages)
        for needle in [
            "Ada Investor",
            "Bello Legal",
            s1,
            s2,
            "e2e-browser/1.0",
            d["document_hash"],
            "simple electronic",
        ]:
            assert needle in text, needle
        capture_json(
            G,
            "executed_pdf_facts",
            {
                "pages": len(reader.pages),
                "first_page_text": reader.pages[0].extract_text().strip(),
                "certificate_text": reader.pages[-1].extract_text(),
            },
        )

        signer_dl = c.get(f"/api/v1/sign/{t1}/executed")
        assert signer_dl.status_code == 200 and signer_dl.content.startswith(b"%PDF")
        capture_json(
            G,
            "signer_executed_download_headers",
            {
                "status": signer_dl.status_code,
                "content_type": signer_dl.headers["content-type"],
                "content_disposition": signer_dl.headers["content-disposition"],
            },
        )

        # every party was emailed the executed copy with the PDF attached
        for who in (s1, s2, u["email"]):
            mails = [m for m in _mails_for(who) if m["subject"].startswith("Signed:")]
            assert len(mails) == 1, (who, [m["subject"] for m in _mails_for(who)])
            assert mails[0]["attachments"] == ["founders-agreement_-_signed.pdf"]
        capture_json(
            G,
            "email_executed_copy",
            _redact_mail([m for m in _mails_for(s1) if m["subject"].startswith("Signed:")][0]),
        )

        # the executed copy is an ordinary private file in the library
        lib = c.get("/api/v1/documents/files", headers=founder).json()["data"]["files"]
        executed_row = next(f for f in lib if f["id"] == d["executed"]["file_id"])
        assert executed_row["origin"] == "executed" and executed_row["scan_status"] == "clean"
        capture_json(G, "executed_file_row", executed_row)
        for fid in (file_id, d["executed"]["file_id"]):
            blocked = c.delete(f"/api/v1/documents/files/{fid}", headers=founder)
            assert blocked.status_code == 409
        cap("delete_signed_or_executed_file_409", blocked)

        # ---- an exported PDF is signable (parallel, default message) -----------------------------
        s3 = unique_email("signer-three")
        par = c.post(
            f"/api/v1/documents/files/{exported_pdf_id}/signature-requests",
            headers=founder,
            json={"signers": [{"email": s3, "name": "Cleo"}]},
        )
        assert par.status_code == 201, par.text
        cap("request_create_parallel_default_message_from_export", par)
        assert par.json()["data"]["message"].endswith("to sign via Cofoundaz.")
        rem = c.post(
            f"/api/v1/documents/signature-requests/{par.json()['data']['id']}/remind",
            headers=founder,
        )
        assert rem.status_code == 200
        cap("remind", rem)
        # a request completed before e-sign v2 has no executed copy and never will: `legacy`
        from app.db.models.document import SignatureRequest
        from app.db.models.job import Job
        from app.db.session import SessionLocal

        cur = c.get(
            f"/api/v1/documents/signature-requests/{par.json()['data']['id']}/signers/"
            f"{par.json()['data']['signers'][0]['id']}/link",
            headers=founder,
        )
        assert cur.status_code == 200  # the reminder rotated the link; copy the current one
        signed = c.post(
            f"/api/v1/sign/{cur.json()['data']['link'].rsplit('/', 1)[-1]}",
            json={"typed_name": "Cleo"},
        )
        assert signed.status_code == 200, signed.text
        db = SessionLocal()
        try:
            db.query(SignatureRequest).filter_by(id=par.json()["data"]["id"]).update(
                {"document_hash": None}
            )
            db.query(Job).filter_by(type="documents.signature_finalize", status="queued").delete()
            db.commit()
        finally:
            db.close()
        legacy = c.get(
            f"/api/v1/documents/signature-requests/{par.json()['data']['id']}", headers=founder
        )
        assert legacy.json()["data"]["executed"]["state"] == "legacy"
        cap("request_detail_complete_before_v2_legacy", legacy)
        lst = c.get("/api/v1/documents/signature-requests", headers=founder)
        assert lst.status_code == 200
        cap("request_list", lst)


def _redact_mail(mail: dict) -> dict:
    out = {k: v for k, v in mail.items() if k != "_path"}
    out["html"] = _redact(out["html"])
    return out
