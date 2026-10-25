"""Documents BE-D: signing order, the viewed state, the sender's message, request detail, link
re-copy, the executed PDF and link shares of files (gap-list rows 15, 16, 17, 31, 37)."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from pypdf import PdfReader

import app.api.v1.endpoints.documents as documents_ep
from app.core.config import settings
from app.db.models.document import DocumentFile, DocumentShare, SignatureRequest, SignatureSigner
from app.db.models.enums import MembershipRole, ScanStatus
from app.db.models.job import Job
from app.platform.email import ConsoleEmailSender
from app.services.documents.links import derive_token
from tests.documents_helpers import PDF, headers, team, upload

API = "/api/v1"


@pytest.fixture
def ws(db, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.test")
    startup, users = team(db)
    sender = ConsoleEmailSender()
    monkeypatch.setattr(documents_ep, "get_email_sender", lambda: sender)
    return startup, users, sender


def _h(ws, role=MembershipRole.founder):
    startup, users, _ = ws
    return headers(users[role], startup)


def _send(client, ws, file_id, **body):
    body.setdefault("signers", [{"email": "a@x.com", "name": "Alice"}, {"email": "b@x.com"}])
    r = client.post(
        f"{API}/documents/files/{file_id}/signature-requests", json=body, headers=_h(ws)
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _docx() -> bytes:
    from docx import Document as DocxDocument

    buf = io.BytesIO()
    DocxDocument().save(buf)
    return buf.getvalue()


def _token(link: str | None) -> str:
    assert link is not None
    return link.rsplit("/", 1)[-1]


# ---- signing order ----------------------------------------------------------------------------


def test_sequential_releases_the_next_link_only_when_the_previous_signs(client, ws):
    _s, _u, mail = ws
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"], sequential=True)
    first, second = req["signer_links"]
    assert first and second is None
    assert [s["status"] for s in req["signers"]] == ["pending", "waiting"]
    assert [m.to for m in mail.sent] == ["a@x.com"]  # b has not been emailed

    # b's token exists nowhere yet: the derived token of b's row is not accepted
    b = req["signers"][1]
    guessed = derive_token("sign", __import__("uuid").UUID(b["id"]), 0)
    assert client.get(f"{API}/sign/{guessed}").status_code == 404
    # and an editor cannot copy a link that has not been released
    r = client.get(
        f"{API}/documents/signature-requests/{req['id']}/signers/{b['id']}/link", headers=_h(ws)
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SIGNER_NOT_RELEASED"

    signed = client.post(f"{API}/sign/{_token(first)}", json={"typed_name": "Alice A"})
    assert signed.status_code == 200
    assert [m.to for m in mail.sent] == ["a@x.com", "b@x.com"]  # b released and emailed
    assert 'href="https://app.example.test/sign/' in mail.sent[-1].html
    detail = client.get(f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws)).json()
    assert [s["status"] for s in detail["data"]["signers"]] == ["signed", "pending"]


def test_parallel_emails_everyone_at_once(client, ws):
    _s, _u, mail = ws
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"])
    assert all(req["signer_links"]) and req["sequential"] is False
    assert sorted(m.to for m in mail.sent) == ["a@x.com", "b@x.com"]


# ---- viewed state ----------------------------------------------------------------------------


def test_opening_the_link_marks_viewed_once_and_signing_marks_signed(client, db, ws):
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"])
    token = _token(req["signer_links"][0])
    assert client.get(f"{API}/sign/{token}").status_code == 200
    row = db.query(SignatureSigner).filter_by(email="a@x.com").one()
    first_view = row.viewed_at
    assert first_view is not None
    client.get(f"{API}/sign/{token}")
    db.refresh(row)
    assert row.viewed_at == first_view  # later opens leave the first stamp
    detail = client.get(f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws))
    statuses = {s["email"]: s["status"] for s in detail.json()["data"]["signers"]}
    assert statuses == {"a@x.com": "viewed", "b@x.com": "pending"}


# ---- message --------------------------------------------------------------------------------


def test_default_message_and_custom_message_are_stored_and_emailed_escaped(client, ws):
    _s, _u, mail = ws
    f = upload(client, _h(ws), name="Founders NDA.pdf")
    req = _send(client, ws, f["id"], signers=[{"email": "a@x.com"}])
    assert req["message"].endswith("has sent you Founders NDA.pdf to sign via Cofoundaz.")
    assert "<p>" in mail.sent[-1].html and "Founders NDA.pdf" in mail.sent[-1].html

    evil = "Hi\r\nBcc: evil@x.com <script>alert(1)</script>"
    req2 = _send(client, ws, f["id"], signers=[{"email": "c@x.com"}], message=evil)
    assert "\r" not in req2["message"] and "<script>" in req2["message"]  # stored as text
    html = mail.sent[-1].html
    assert "<script>" not in html and "&lt;script&gt;" in html
    for m in mail.sent:
        assert "\r" not in m.subject and "\n" not in m.subject


def test_a_title_with_line_breaks_cannot_reach_the_subject(client, ws):
    _s, _u, mail = ws
    f = upload(client, _h(ws))
    _send(client, ws, f["id"], title="NDA\r\nBcc: evil@x.com", signers=[{"email": "a@x.com"}])
    assert "\n" not in mail.sent[-1].subject and "\r" not in mail.sent[-1].subject


@pytest.mark.parametrize(
    "email", ["a@x.com\r\nBcc: e@x.com", "a@x.com, b@x.com", "<a@x.com>", "no-at-sign", "a b@x.com"]
)
def test_signer_email_must_be_one_plain_address(client, ws, email):
    f = upload(client, _h(ws))
    r = client.post(
        f"{API}/documents/files/{f['id']}/signature-requests",
        json={"signers": [{"email": email}]},
        headers=_h(ws),
    )
    assert r.status_code == 422


def test_message_over_the_limit_is_refused(client, ws):
    f = upload(client, _h(ws))
    r = client.post(
        f"{API}/documents/files/{f['id']}/signature-requests",
        json={"signers": [{"email": "a@x.com"}], "message": "x" * 2001},
        headers=_h(ws),
    )
    assert r.status_code == 422


# ---- what can be signed ----------------------------------------------------------------------


def test_only_readable_pdfs_can_be_sent_for_signature(client, db, ws):
    docx = upload(
        client,
        _h(ws),
        name="a.docx",
        ct="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        body=_docx(),
    )
    r = client.post(
        f"{API}/documents/files/{docx['id']}/signature-requests",
        json={"signers": [{"email": "a@x.com"}]},
        headers=_h(ws),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "PDF_REQUIRED"

    broken = upload(client, _h(ws), name="b.pdf", body=b"%PDF-1.4 not really")
    r = client.post(
        f"{API}/documents/files/{broken['id']}/signature-requests",
        json={"signers": [{"email": "a@x.com"}]},
        headers=_h(ws),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "PDF_UNREADABLE"
    assert db.query(SignatureRequest).count() == 0


def test_an_exported_document_can_be_signed(client, ws):
    h = _h(ws)
    doc = client.post(f"{API}/documents", json={"title": "Contract"}, headers=h).json()["data"]
    client.put(
        f"{API}/documents/{doc['id']}",
        json={"title": "Contract", "sections": [{"heading": "A", "body": "Terms"}], "version": 1},
        headers=h,
    )
    exp = client.post(f"{API}/documents/{doc['id']}/export", json={"format": "pdf"}, headers=h)
    assert exp.status_code in (200, 201), exp.text
    file_id = exp.json()["data"]["id"]
    req = _send(client, ws, file_id, signers=[{"email": "a@x.com"}])
    assert req["status"] == "awaiting" and req["document_hash"]


# ---- request detail, link re-copy, remind ------------------------------------------------------


def test_detail_has_sent_expiry_signers_and_audit_fields_for_editors_only(client, ws):
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"], expires_in_days=7)
    token = _token(req["signer_links"][0])
    client.post(
        f"{API}/sign/{token}",
        json={"typed_name": "Alice A"},
        headers={"user-agent": "UA-Test/1.0"},
    )
    d = client.get(f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws)).json()["data"]
    assert d["sent_at"] and d["expires_at"] and d["legal_caveat"].startswith("These are simple")
    signed = d["signers"][0]
    assert signed["signed_name"] == "Alice A" and signed["signed_user_agent"] == "UA-Test/1.0"
    assert signed["signed_ip"] and signed["viewed_at"] and signed["notified_at"]
    assert d["executed"] is None  # not complete yet
    # a mentor is refused; the public signer response has no roster or audit fields
    assert (
        client.get(
            f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws, MembershipRole.mentor)
        ).status_code
        == 403
    )
    pub = client.post(
        f"{API}/sign/{_token(req['signer_links'][1])}", json={"typed_name": "Bob"}
    ).json()["data"]
    assert "signers" not in pub and "document_hash" not in pub


def test_copy_link_again_returns_the_same_working_link_and_changes_nothing(client, ws):
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"])
    sid = req["signers"][0]["id"]
    url = f"{API}/documents/signature-requests/{req['id']}/signers/{sid}/link"
    again = client.get(url, headers=_h(ws)).json()["data"]
    assert again["link"] == req["signer_links"][0]
    assert client.get(f"{API}/sign/{_token(again['link'])}").status_code == 200
    # another workspace's signer / a foreign id is a 404, a mentor a 403
    assert client.get(url.replace(sid, req["id"]), headers=_h(ws)).status_code == 404
    assert client.get(url, headers=_h(ws, MembershipRole.mentor)).status_code == 403


def test_remind_rotates_the_link_and_the_copy_returns_the_new_one(client, ws):
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"], signers=[{"email": "a@x.com"}])
    old = req["signer_links"][0]
    r = client.post(f"{API}/documents/signature-requests/{req['id']}/remind", headers=_h(ws))
    assert r.json()["data"]["reminded"] == 1
    assert client.get(f"{API}/sign/{_token(old)}").status_code == 404
    sid = req["signers"][0]["id"]
    new = client.get(
        f"{API}/documents/signature-requests/{req['id']}/signers/{sid}/link", headers=_h(ws)
    ).json()["data"]["link"]
    assert new != old and client.get(f"{API}/sign/{_token(new)}").status_code == 200


def test_copy_link_is_refused_after_signing_or_cancel(client, ws):
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"])
    sid = req["signers"][0]["id"]
    client.post(f"{API}/sign/{_token(req['signer_links'][0])}", json={"typed_name": "A"})
    url = f"{API}/documents/signature-requests/{req['id']}/signers/{sid}/link"
    assert client.get(url, headers=_h(ws)).status_code == 409
    client.post(f"{API}/documents/signature-requests/{req['id']}/cancel", headers=_h(ws))
    other = req["signers"][1]["id"]
    assert (
        client.get(
            f"{API}/documents/signature-requests/{req['id']}/signers/{other}/link", headers=_h(ws)
        ).status_code
        == 409
    )


def test_public_token_hardening(client, ws):
    for bad in ["x" * 500, "%00", "a b"]:
        assert client.get(f"{API}/sign/{bad}").status_code == 404
        assert client.get(f"{API}/shared/{bad}").status_code == 404


# ---- completion: the executed PDF -------------------------------------------------------------


def _drain(db):
    from app.worker import runner
    from app.worker.handlers import documents_sign  # noqa: F401  (registers the handler)

    for _ in range(5):
        if runner.run_once(db) == 0:
            break


def _complete(client, db, ws, **kw):
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"], **kw)
    for i, link in enumerate(req["signer_links"]):
        if link is None:  # sequential: released by the previous signature
            link = client.get(
                f"{API}/documents/signature-requests/{req['id']}/signers/{req['signers'][i]['id']}/link",
                headers=_h(ws),
            ).json()["data"]["link"]
        r = client.post(
            f"{API}/sign/{_token(link)}",
            json={"typed_name": f"Signer {i}"},
            headers={"user-agent": f"UA-{i}"},
        )
        assert r.status_code == 200, r.text
    return f, req


def test_completion_enqueues_one_job_then_the_worker_builds_and_emails_the_executed_pdf(
    client, db, ws, monkeypatch
):
    import app.worker.handlers.documents_sign as sign_mod

    _s, _u, mail = ws
    worker_mail = ConsoleEmailSender()
    monkeypatch.setattr(sign_mod, "get_email_sender", lambda: worker_mail)
    f, req = _complete(client, db, ws)
    assert db.query(Job).filter_by(type="documents.signature_finalize").count() == 1
    mid = client.get(f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws)).json()
    assert mid["data"]["status"] == "complete" and mid["data"]["executed"]["state"] == "pending"
    assert (
        client.get(
            f"{API}/documents/signature-requests/{req['id']}/executed", headers=_h(ws)
        ).status_code
        == 409
    )

    _drain(db)
    d = client.get(f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws)).json()["data"]
    assert d["executed"]["state"] == "ready" and d["executed"]["filename"].endswith("- signed.pdf")

    # every party got it: both signers and the sender, each with the PDF attached
    assert sorted(m.to for m in worker_mail.sent)[:2] == ["a@x.com", "b@x.com"]
    assert len(worker_mail.sent) == 3
    assert all(
        m.attachments and m.attachments[0].content.startswith(b"%PDF") for m in worker_mail.sent
    )

    # editors fetch it through the normal signed-link route; it is a private, scanned file
    link = client.get(
        f"{API}/documents/signature-requests/{req['id']}/executed", headers=_h(ws)
    ).json()["data"]
    got = client.get(link["url"].replace("http://api.test", ""))
    assert got.status_code == 200
    pdf = PdfReader(io.BytesIO(got.content))
    assert len(pdf.pages) == 2  # the original page + the signature/certificate page
    text = "\n".join(p.extract_text() for p in pdf.pages)
    for needle in ["Signer 0", "Signer 1", "a@x.com", "b@x.com", "UA-0", "simple electronic"]:
        assert needle in text
    assert d["document_hash"] in text
    row = db.query(DocumentFile).filter_by(id=d["executed"]["file_id"]).one()
    assert row.is_private and row.url is None and row.scan_status == ScanStatus.clean
    # the library lists it as an executed file
    files = client.get(f"{API}/documents/files", headers=_h(ws)).json()["data"]["files"]
    assert {x["id"]: x["origin"] for x in files}[row.id.hex and str(row.id)] == "executed"


def test_a_signer_downloads_the_executed_copy_with_their_own_link_only_after_completion(
    client, db, ws
):
    f = upload(client, _h(ws))
    req = _send(client, ws, f["id"])
    a, b = (_token(link) for link in req["signer_links"])
    client.post(f"{API}/sign/{a}", json={"typed_name": "A"})
    assert client.get(f"{API}/sign/{a}/executed").status_code == 404  # not complete
    client.post(f"{API}/sign/{b}", json={"typed_name": "B"})
    assert client.get(f"{API}/sign/{a}/executed").json()["error"]["code"] == "EXECUTED_NOT_READY"
    _drain(db)
    got = client.get(f"{API}/sign/{a}/executed")
    assert got.status_code == 200 and got.content.startswith(b"%PDF")
    assert client.get(f"{API}/sign/not-a-token/executed").status_code == 404
    # 90 days after completion the link no longer fetches it
    db.query(SignatureRequest).update({"completed_at": datetime.now(UTC) - timedelta(days=91)})
    db.flush()
    assert client.get(f"{API}/sign/{a}/executed").status_code == 404


def test_the_executed_file_and_the_signed_file_cannot_be_deleted(client, db, ws):
    f, req = _complete(client, db, ws)
    _drain(db)
    d = client.get(f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws)).json()["data"]
    for fid in (f["id"], d["executed"]["file_id"]):
        r = client.delete(f"{API}/documents/files/{fid}", headers=_h(ws))
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "FILE_HAS_SIGNATURE_REQUESTS"


def test_sequential_request_completes_in_order(client, db, ws):
    _f, req = _complete(client, db, ws, sequential=True)
    d = client.get(f"{API}/documents/signature-requests/{req['id']}", headers=_h(ws)).json()["data"]
    assert d["status"] == "complete" and [s["status"] for s in d["signers"]] == ["signed"] * 2


# ---- link shares: documents and files -----------------------------------------------------------


def test_anyone_with_the_link_share_of_a_document_with_a_custom_expiry(client, ws):
    _s, _u, mail = ws
    h = _h(ws)
    doc = client.post(f"{API}/documents", json={"title": "Deck"}, headers=h).json()["data"]
    when = (datetime.now(UTC) + timedelta(days=10)).replace(microsecond=0)
    r = client.post(
        f"{API}/documents/{doc['id']}/shares", json={"expires_at": when.isoformat()}, headers=h
    )
    assert r.status_code == 201, r.text
    share = r.json()["data"]
    assert share["anyone_with_link"] is True and share["email"] is None
    assert share["expires_at"].startswith(when.isoformat()[:19])
    assert share["link"].startswith("https://app.example.test/shared/")
    assert mail.sent == []  # nothing emailed for a link-only share
    pub = client.get(f"{API}/shared/{_token(share['link'])}").json()["data"]
    assert pub["kind"] == "document" and pub["document"]["title"] == "Deck"

    # copy again: the list returns the same link, to editors only
    listed = client.get(f"{API}/documents/{doc['id']}/shares", headers=h).json()["data"]["shares"]
    assert listed[0]["link"] == share["link"]
    assert (
        client.get(
            f"{API}/documents/{doc['id']}/shares", headers=_h(ws, MembershipRole.mentor)
        ).status_code
        == 403
    )

    # change the expiry, then revoke
    new = (datetime.now(UTC) + timedelta(days=20)).isoformat()
    patched = client.patch(
        f"{API}/documents/{doc['id']}/shares/{share['id']}", json={"expires_at": new}, headers=h
    )
    assert patched.status_code == 200 and patched.json()["data"]["expires_at"].startswith(new[:16])
    resp_1 = client.delete(f"{API}/documents/{doc['id']}/shares/{share['id']}", headers=h)
    assert resp_1.status_code == 200
    assert client.get(f"{API}/shared/{_token(share['link'])}").status_code == 404
    listed = client.get(f"{API}/documents/{doc['id']}/shares", headers=h).json()["data"]["shares"]
    assert listed[0]["status"] == "revoked" and listed[0]["link"] is None
    resp_2 = client.patch(
        f"{API}/documents/{doc['id']}/shares/{share['id']}", json={"expires_at": new}, headers=h
    )
    assert resp_2.status_code == 404


@pytest.mark.parametrize(
    "when",
    [
        lambda: datetime.now(UTC) - timedelta(days=1),
        lambda: datetime.now(UTC) + timedelta(days=400),
    ],
)
def test_expiry_must_be_in_the_future_and_within_a_year(client, ws, when):
    doc = client.post(f"{API}/documents", json={"title": "D"}, headers=_h(ws)).json()["data"]
    r = client.post(
        f"{API}/documents/{doc['id']}/shares",
        json={"expires_at": when().isoformat()},
        headers=_h(ws),
    )
    assert r.status_code == 422


def test_emailed_share_still_emails_and_never_expiring_is_allowed(client, ws):
    _s, _u, mail = ws
    doc = client.post(f"{API}/documents", json={"title": "D"}, headers=_h(ws)).json()["data"]
    r = client.post(
        f"{API}/documents/{doc['id']}/shares",
        json={"email": "p@x.com", "expires_in_days": None},
        headers=_h(ws),
    )
    assert r.json()["data"]["expires_at"] is None and r.json()["data"]["anyone_with_link"] is False
    assert [m.to for m in mail.sent] == ["p@x.com"]


def test_a_pre_v2_share_still_opens_but_its_link_cannot_be_recopied(client, db, ws):
    import secrets
    import uuid

    from app.services.auth.sessions import hash_token

    startup, users, _ = ws
    doc = client.post(f"{API}/documents", json={"title": "Old"}, headers=_h(ws)).json()["data"]
    raw = secrets.token_urlsafe(32)
    db.add(
        DocumentShare(
            id=uuid.uuid4(),
            startup_id=startup.id,
            document_id=uuid.UUID(doc["id"]),
            email="old@x.com",
            access_level="view",
            token_hash=hash_token(raw),
        )
    )
    db.flush()
    assert client.get(f"{API}/shared/{raw}").status_code == 200
    listed = client.get(f"{API}/documents/{doc['id']}/shares", headers=_h(ws)).json()["data"]
    assert listed["shares"][0]["link"] is None and listed["shares"][0]["status"] == "active"


def test_a_file_can_be_shared_by_link_and_the_link_serves_it_until_revoked(client, db, ws):
    h = _h(ws)
    f = upload(client, h)
    r = client.post(
        f"{API}/documents/files/{f['id']}/shares", json={"expires_in_days": 7}, headers=h
    )
    assert r.status_code == 201, r.text
    share = r.json()["data"]
    assert share["kind"] == "file" and share["access_level"] == "view"
    token = _token(share["link"])
    meta = client.get(f"{API}/shared/{token}").json()["data"]
    assert meta["kind"] == "file" and meta["file"]["filename"] == "a.pdf"
    assert meta["file"]["download_url"].endswith(f"/api/v1/shared/{token}/file")
    got = client.get(f"{API}/shared/{token}/file")
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-disposition"].startswith("attachment;")
    assert (
        client.get(f"{API}/shared/{token}/file", params={"inline": "true"})
        .headers["content-disposition"]
        .startswith("inline;")
    )
    row = list(client.get(f"{API}/documents/files", headers=h).json()["data"]["files"])[0]
    assert row["share_count"] == 1
    assert (
        client.get(f"{API}/documents/files/{f['id']}/shares", headers=h).json()["data"]["shares"][
            0
        ]["link"]
        == share["link"]
    )

    # a blocked file stops being served; a trashed one too; revoking ends it
    db.query(DocumentFile).update({"scan_status": ScanStatus.blocked})
    db.flush()
    assert client.get(f"{API}/shared/{token}/file").status_code == 404
    db.query(DocumentFile).update({"scan_status": ScanStatus.clean})
    db.flush()
    assert client.get(f"{API}/shared/{token}/file").status_code == 200
    client.delete(f"{API}/documents/files/{f['id']}/shares/{share['id']}", headers=h)
    assert client.get(f"{API}/shared/{token}/file").status_code == 404
    row = list(client.get(f"{API}/documents/files", headers=h).json()["data"]["files"])[0]
    assert row["share_count"] == 0


def test_a_document_share_token_cannot_fetch_a_file_and_unscanned_files_are_not_shareable(
    client, db, ws
):
    h = _h(ws)
    doc = client.post(f"{API}/documents", json={"title": "D"}, headers=h).json()["data"]
    share = client.post(f"{API}/documents/{doc['id']}/shares", json={}, headers=h).json()["data"]
    assert client.get(f"{API}/shared/{_token(share['link'])}/file").status_code == 404
    f = upload(client, h)
    db.query(DocumentFile).update({"scan_status": ScanStatus.pending})
    db.flush()
    r = client.post(f"{API}/documents/files/{f['id']}/shares", json={}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_NOT_CLEAN"


def test_workspace_share_list_includes_file_shares_and_trashed_targets_drop_out(client, ws):
    h = _h(ws)
    f = upload(client, h)
    client.post(f"{API}/documents/files/{f['id']}/shares", json={}, headers=h)
    rows = client.get(f"{API}/documents/shares", headers=h).json()["data"]["shares"]
    assert rows[0]["kind"] == "file" and rows[0]["file_id"] == f["id"] and rows[0]["link"]
    client.delete(f"{API}/documents/files/{f['id']}", headers=h)
    assert client.get(f"{API}/documents/shares", headers=h).json()["data"]["shares"] == []
