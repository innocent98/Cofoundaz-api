"""Live Documents access + integrity journey (Documents BE-A, D-DOC-1).

One founder workspace with a team member, a mentor and a business consultant. Proves against the
running app that:
  * the founder and the team member reach every Documents route, while the mentor and the
    consultant get `403 FORBIDDEN` on all of them (documents, files, shares, signature requests,
    templates) and never see a signer or share-recipient email;
  * an upload is judged by its bytes, not its Content-Type (`MZ` sent as `application/pdf` is a
    422), Markdown is accepted, and every file carries `scan_status`;
  * a file with a signature request cannot be deleted (409), so a completed request survives;
  * the request detail carries `signed_ip` / `signed_user_agent` for editors only;
  * `document.signature.*` and `document.shared` notifications reach only the sender/owner.

Bodies are captured to `e2e/_captures/documents_access/*.json`, the verbatim source for the
Documents FE guides. Raw signer/share tokens in links are replaced with `REDACTED_TOKEN`.
"""

import io
import re

import httpx

G = "documents_access"
_TOKEN_IN_LINK = re.compile(r"(/(?:sign|shared)/)[A-Za-z0-9_\-]{20,}")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _redact(text: str) -> str:
    return _TOKEN_IN_LINK.sub(r"\1REDACTED_TOKEN", text)


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
    return {**auth, "X-Workspace-Id": ws}


def test_documents_access_journey(
    base_url, make_verified_user, unique_email, mailbox, capture_json
):
    def cap(name, resp):
        capture_json(G, name, {"status": resp.status_code, "body": _redact_json(resp)})

    def _redact_json(resp):
        import json

        return json.loads(_redact(resp.text))

    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada F"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "DocsCo"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        founder = {**auth, "X-Workspace-Id": ws}
        team = _join(c, founder, ws, "team_member", unique_email, mailbox)
        mentor = _join(c, founder, ws, "mentor", unique_email, mailbox)
        consultant = _join(c, founder, ws, "business_consultant", unique_email, mailbox)

        # --- founder builds the data ---------------------------------------------------------
        doc = c.post("/api/v1/documents", headers=founder, json={"template_key": "one_pager"})
        assert doc.status_code == 201, doc.text
        doc_id = doc.json()["data"]["id"]

        fake = c.post(
            "/api/v1/documents/files",
            headers=founder,
            files={"file": ("fake.pdf", io.BytesIO(b"MZ"), "application/pdf")},
        )
        assert fake.status_code == 422, fake.text
        cap("upload_exe_as_pdf_422", fake)

        md = c.post(
            "/api/v1/documents/files",
            headers=founder,
            files={"file": ("notes.md", io.BytesIO(b"# Notes\n"), "text/markdown")},
        )
        assert md.status_code == 201, md.text
        assert md.json()["data"]["content_type"] == "text/markdown"
        cap("upload_markdown", md)

        pdf = c.post(
            "/api/v1/documents/files",
            headers=founder,
            files={"file": ("nda.pdf", io.BytesIO(b"%PDF-1.4\n%e2e\n%%EOF"), "application/pdf")},
        )
        assert pdf.status_code == 201, pdf.text
        assert pdf.json()["data"]["scan_status"] == "clean"
        cap("upload_pdf", pdf)
        file_id = pdf.json()["data"]["id"]

        signer_email = unique_email("signer")
        share_email = unique_email("recipient")
        share = c.post(
            f"/api/v1/documents/{doc_id}/shares",
            headers=founder,
            json={"email": share_email, "access_level": "view"},
        )
        assert share.status_code == 201, share.text
        share_id = share.json()["data"]["id"]
        req = c.post(
            f"/api/v1/documents/files/{file_id}/signature-requests",
            headers=founder,
            json={"signers": [{"email": signer_email, "name": "Sam Signer"}], "title": "NDA"},
        )
        assert req.status_code == 201, req.text
        req_id = req.json()["data"]["id"]
        token = req.json()["data"]["signer_links"][0].rsplit("/", 1)[-1]
        cap("signature_request_create", req)

        shared = c.get("/api/v1/shared/" + share.json()["data"]["link"].rsplit("/", 1)[-1])
        assert shared.status_code == 200, shared.text
        assert "id" not in shared.json()["data"]["document"]
        assert "folder" not in shared.json()["data"]["document"]
        cap("shared_open", shared)

        # --- every route, every role ----------------------------------------------------------
        reads = [
            "/api/v1/document-templates",
            "/api/v1/document-templates/business_plan",
            "/api/v1/documents",
            f"/api/v1/documents/{doc_id}",
            "/api/v1/documents/files",
            f"/api/v1/documents/files/{file_id}",
            "/api/v1/documents/shares",
            f"/api/v1/documents/{doc_id}/shares",
            "/api/v1/documents/signature-requests",
            f"/api/v1/documents/signature-requests/{req_id}",
        ]
        for role, h in (("team_member", team),):
            for url in reads:
                r = c.get(url, headers=h)
                assert r.status_code == 200, (role, url, r.text)
        for role, h in (("mentor", mentor), ("business_consultant", consultant)):
            for url in reads:
                r = c.get(url, headers=h)
                assert r.status_code == 403, (role, url, r.text)
                assert r.json()["error"]["code"] == "FORBIDDEN"
                assert signer_email not in r.text and share_email not in r.text
            writes = [
                ("POST", "/api/v1/documents", {"title": "x"}),
                (
                    "PUT",
                    f"/api/v1/documents/{doc_id}",
                    {"title": "x", "sections": [], "version": 1},
                ),
                ("DELETE", f"/api/v1/documents/{doc_id}", None),
                ("DELETE", f"/api/v1/documents/files/{file_id}", None),
                (
                    "POST",
                    f"/api/v1/documents/{doc_id}/shares",
                    {"email": "x@example.com", "access_level": "view"},
                ),
                ("DELETE", f"/api/v1/documents/{doc_id}/shares/{share_id}", None),
                (
                    "POST",
                    f"/api/v1/documents/files/{file_id}/signature-requests",
                    {"signers": [{"email": "x@example.com"}]},
                ),
                ("POST", f"/api/v1/documents/signature-requests/{req_id}/remind", None),
                ("POST", f"/api/v1/documents/signature-requests/{req_id}/cancel", None),
            ]
            for method, url, body in writes:
                r = c.request(method, url, headers=h, json=body)
                assert r.status_code == 403, (role, method, url, r.text)
        up = c.post(
            "/api/v1/documents/files",
            headers=mentor,
            files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        assert up.status_code == 403
        cap("mentor_list_documents_403", c.get("/api/v1/documents", headers=mentor))
        cap(
            "mentor_signature_requests_403",
            c.get("/api/v1/documents/signature-requests", headers=mentor),
        )
        cap("mentor_shares_403", c.get("/api/v1/documents/shares", headers=mentor))
        cap("mentor_files_403", c.get("/api/v1/documents/files", headers=mentor))
        cap("consultant_template_403", c.get("/api/v1/document-templates", headers=consultant))
        # still works for the person it belongs to
        assert c.get("/api/v1/documents", headers=mentor).status_code == 403
        assert c.get("/api/v1/documents", headers=team).status_code == 200

        # --- editors read files through a short-lived signed link only ----------------------------
        assert "url" not in pdf.json()["data"]
        link = c.get(f"/api/v1/documents/files/{file_id}/download", headers=founder)
        assert link.status_code == 200, link.text
        cap("file_download_link", link)
        served = c.get(link.json()["data"]["url"])  # no auth header: the signed token
        assert served.status_code == 200 and served.content.startswith(b"%PDF-")
        assert (
            c.get(f"/api/v1/documents/files/{file_id}/download", headers=mentor).status_code == 403
        )
        assert c.get(link.json()["data"]["url"] + "x").status_code == 404

        # --- delete refused while a request exists ---------------------------------------------
        blocked = c.delete(f"/api/v1/documents/files/{file_id}", headers=founder)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "FILE_HAS_SIGNATURE_REQUESTS"
        cap("file_delete_with_request_409", blocked)

        # --- signer signs; audit fields reach editors only -------------------------------------
        view = c.get(f"/api/v1/sign/{token}")
        assert view.status_code == 200, view.text
        sfile = view.json()["data"]["file"]
        assert set(sfile) == {"filename", "content_type", "size_bytes", "download_url"}
        cap("sign_view", view)
        # the signer's file link is live while the token is, and serves the bytes
        got_file = c.get(sfile["download_url"])
        assert got_file.status_code == 200 and got_file.content.startswith(b"%PDF-")
        signed = c.post(
            f"/api/v1/sign/{token}",
            json={"typed_name": "Sam Signer"},
            headers={"user-agent": "e2e-ua"},
        )
        assert signed.status_code == 200, signed.text
        assert "signers" not in signed.json()["data"] and "e2e-ua" not in signed.text
        cap("sign_public_response", signed)
        assert c.get(sfile["download_url"]).status_code == 404  # signed: the file link is dead too
        gone = c.get(f"/api/v1/sign/{token}")
        assert gone.status_code == 404
        detail = c.get(f"/api/v1/documents/signature-requests/{req_id}", headers=founder)
        s0 = detail.json()["data"]["signers"][0]
        assert s0["signed_user_agent"] == "e2e-ua" and s0["signed_ip"]
        assert detail.json()["data"]["status"] == "complete"
        cap("signature_request_detail_completed", detail)
        again = c.delete(f"/api/v1/documents/files/{file_id}", headers=founder)
        assert again.status_code == 409  # the completed request is the record
        still = c.get(f"/api/v1/documents/signature-requests/{req_id}", headers=founder)
        assert still.status_code == 200 and still.json()["data"]["status"] == "complete"

        # --- a file with no request still deletes ---------------------------------------------
        md_id = md.json()["data"]["id"]
        gone = c.delete(f"/api/v1/documents/files/{md_id}", headers=founder)
        assert gone.status_code == 200, gone.text

        # --- notification audience --------------------------------------------------------------
        def types(h):
            r = c.get("/api/v1/notifications", headers=h)
            assert r.status_code == 200, r.text
            return [n["type"] for n in r.json()["data"]["notifications"]]

        # Founder owns the document and sent the request: told the request was signed and
        # completed; not told of the request (signers get the email) nor of their own share.
        f_types = types(founder)
        assert "document.signature.signed" in f_types
        assert "document.signature.completed" in f_types
        assert "document.signature.requested" not in f_types
        assert "document.shared" not in f_types
        t_types = types(team)
        m_types = types(mentor)
        k_types = types(consultant)
        for got in (t_types, m_types, k_types):
            assert not [t for t in got if t.startswith("document.")], got
        cap("founder_notifications", c.get("/api/v1/notifications", headers=founder))
        cap("mentor_notifications", c.get("/api/v1/notifications", headers=mentor))
