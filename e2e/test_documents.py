"""Live Document Library Core journey (Module 18, Slice 1): a founder onboards,
browses the in-code template catalog, instantiates a Business Plan document
from the `business_plan` template, reads it back full (with `sections`), edits
it with a full-replace `PUT` (version 1 -> 2, status draft -> final, a folder),
hits the optimistic-concurrency 409 on a stale re-PUT, lists documents by
`folder` (confirming the list is SUMMARY-shaped -- no `sections` key), then
deletes it and confirms the 404.

`test_documents_files_journey` (Module 18, Slice 2 - Upload & Files) covers the
sibling `/documents/files` surface: a founder uploads a small PDF (multipart,
with a `folder`), lists/gets it back, a disallowed content-type 422s, then a
delete + re-GET 404s. The e2e runner sets no `STORAGE_BACKEND`, so this runs on
LocalStorage -- the captured `url` is a local filesystem path, not a Cloudinary
URL (see the FE guide for how that differs in staging/prod).

`test_documents_sharing_journey` (Module 18, Slice 3 - Sharing) covers the
tokenized external-link surface: a founder shares a document by email,
`POST /documents/{id}/shares` returns the link once, the link is also read
back out of the captured share email in the file mail dir (same
`E2E_MAIL_DIR` auth journeys use for verification/reset tokens -- see
`_latest_share_link` below), the public `GET /shared/{token}` opens the
document with NO auth at all, the per-document and workspace "shared with"
lists show it, a revoke fires, and the same public open then 404s.

`test_documents_esignature_journey` (Module 18, Slice 4 - E-signature, the
final slice -- completes Module 18) covers the tokenized-link signing
surface: a founder uploads a file and sends it to two signers, each signer's
secure link is read back out of the captured signature email in the file
mail dir (same mechanism as `_latest_share_link` above, but the signature
email carries a `/sign/{token}` link -- see `_latest_sign_link` below) and
cross-checked against the create response's one-time `signer_links`, both
signers open the file with NO auth via the public `GET /sign/{token}` and
sign it via the public `POST /sign/{token}`, the request goes `awaiting` ->
`complete` after the second signature, and the workspace list reflects `2 of
2` signed + `complete`. A second request exercises the cancel path: create ->
cancel -> the cancelled signer's `GET /sign/{token}` 404s.

Every response body along the way is captured to `e2e/_captures/documents/
*.json` -- those files are the verbatim source for
`docs/fe-integration-guide-documents-templates.md`,
`docs/fe-integration-guide-documents-files.md`,
`docs/fe-integration-guide-documents-sharing.md`, and
`docs/fe-integration-guide-documents-esignature.md`. They must be REAL
bodies from this live run, complete and untrimmed.

Document Library Core has no roadmap/assessment dependency, so onboarding here
is just steps 1-4 + complete -- same shape as e2e/test_business_builder.py and
e2e/test_journal.py.
"""

import io
import json
import os
import re
from pathlib import Path

import httpx

_SHARE_LINK_RE = re.compile(r'href="([^"]*/shared/[^"]+)"')
_SIGN_LINK_RE = re.compile(r'href="([^"]*/sign/[^"]+)"')


def _latest_share_link(email: str) -> str:
    """Reads the raw share link out of the file-backend mail dir.

    Mirrors `mailbox.latest_token_for` in e2e/conftest.py (same `E2E_MAIL_DIR`
    env var, same per-email JSON captures written by `FileEmailSender`), but
    the share email body carries a full `<a href>` link rather than a bare
    `<code>` token, so it needs its own regex instead of the mailbox
    fixture's `_TOKEN_RE`.
    """
    mail_dir = Path(os.environ.get("E2E_MAIL_DIR", "./var/mail-e2e"))
    files = sorted(mail_dir.glob("*.json"))
    for f in reversed(files):  # newest first
        data = json.loads(f.read_text())
        if data["to"].lower() != email.lower():
            continue
        m = _SHARE_LINK_RE.search(data["html"])
        if m:
            return m.group(1)
    raise AssertionError(f"no share email found for {email}")


def _latest_sign_link(email: str) -> str:
    """Reads the raw `/sign/{token}` link out of the file-backend mail dir.

    Mirrors `_latest_share_link` above -- same `E2E_MAIL_DIR` env var, same
    per-email JSON captures written by `FileEmailSender` -- but the signature
    email's `<a href>` points at `/sign/{token}` (see
    `_signature_email` in app/api/v1/endpoints/documents.py) instead of
    `/shared/{token}`, so it needs its own regex.
    """
    mail_dir = Path(os.environ.get("E2E_MAIL_DIR", "./var/mail-e2e"))
    files = sorted(mail_dir.glob("*.json"))
    for f in reversed(files):  # newest first
        data = json.loads(f.read_text())
        if data["to"].lower() != email.lower():
            continue
        m = _SIGN_LINK_RE.search(data["html"])
        if m:
            return m.group(1)
    raise AssertionError(f"no signature email found for {email}")


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
    """Walk steps 1-4 of the wizard (same shape as e2e/test_business_builder.py)."""
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": name})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": stage},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )


def test_documents_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- no roadmap/assessment dependency for this module.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. GET /document-templates -- the in-code registry catalog.
        templates = c.get("/api/v1/document-templates", headers=wh)
        assert templates.status_code == 200, templates.text
        keys = {t["key"] for t in templates.json()["data"]["templates"]}
        assert "business_plan" in keys and "one_pager" in keys
        capture("documents", "templates_catalog", templates)

        # 1b. GET /document-templates/{key} -- one template's headings.
        template_detail = c.get("/api/v1/document-templates/business_plan", headers=wh)
        assert template_detail.status_code == 200, template_detail.text
        assert template_detail.json()["data"]["sections"][0] == "Executive Summary"
        capture("documents", "template_detail", template_detail)

        # 2. POST /documents from the business_plan template -- 201, kind
        # business_plan, 9 sections seeded from the template, version 1.
        created = c.post(
            "/api/v1/documents",
            headers=wh,
            json={"template_key": "business_plan"},
        )
        assert created.status_code == 201, created.text
        doc = created.json()["data"]
        assert doc["kind"] == "business_plan"
        assert doc["title"] == "Business Plan"
        assert doc["version"] == 1
        assert doc["sections"][0]["heading"] == "Executive Summary"
        capture("documents", "document_create", created)

        doc_id = doc["id"]

        # 3. GET /documents/{id} -- full document, WITH sections.
        fetched = c.get(f"/api/v1/documents/{doc_id}", headers=wh)
        assert fetched.status_code == 200, fetched.text
        assert "sections" in fetched.json()["data"]
        capture("documents", "document_get", fetched)

        # 4. PUT full-replace edit -- version 1 -> 2, status draft -> final,
        # a folder assigned.
        edited = c.put(
            f"/api/v1/documents/{doc_id}",
            headers=wh,
            json={
                "title": "Cofoundaz Business Plan",
                "sections": [
                    {"heading": "Executive Summary", "body": "We help founders ship faster."},
                    {"heading": "The Ask", "body": "$500k pre-seed."},
                ],
                "status": "final",
                "folder": "Investor Docs",
                "version": 1,
            },
        )
        assert edited.status_code == 200, edited.text
        edited_doc = edited.json()["data"]
        assert edited_doc["version"] == 2
        assert edited_doc["status"] == "final"
        assert edited_doc["folder"] == "Investor Docs"
        capture("documents", "document_update", edited)

        # 5. PUT again with the now-stale version:1 -> 409 DOCUMENT_VERSION_CONFLICT.
        stale = c.put(
            f"/api/v1/documents/{doc_id}",
            headers=wh,
            json={
                "title": "Should not apply",
                "sections": [],
                "status": "draft",
                "version": 1,
            },
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "DOCUMENT_VERSION_CONFLICT"
        capture("documents", "document_update_conflict", stale)

        # 5b. Confirm the stale write did not partially mutate the row.
        after_stale = c.get(f"/api/v1/documents/{doc_id}", headers=wh)
        assert after_stale.json()["data"]["version"] == 2
        assert after_stale.json()["data"]["status"] == "final"

        # 6. GET /documents?folder=... -- summary list, NO sections key.
        listed = c.get("/api/v1/documents", headers=wh, params={"folder": "Investor Docs"})
        assert listed.status_code == 200, listed.text
        summaries = listed.json()["data"]["documents"]
        assert len(summaries) == 1
        assert summaries[0]["id"] == doc_id
        assert "sections" not in summaries[0]
        capture("documents", "documents_list_by_folder", listed)

        # 7. DELETE then GET -> 404.
        deleted = c.delete(f"/api/v1/documents/{doc_id}", headers=wh)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"]["deleted"] is True
        capture("documents", "document_delete", deleted)

        gone = c.get(f"/api/v1/documents/{doc_id}", headers=wh)
        assert gone.status_code == 404, gone.text
        capture("documents", "document_get_after_delete", gone)


def test_documents_files_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- same shape as test_documents_journey above.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Files")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. POST /documents/files -- multipart upload (field `file` + form
        # field `folder`), a tiny real PDF -- 201, file summary shape.
        pdf_bytes = b"%PDF-1.4\n%a tiny fake PDF for e2e upload\n%%EOF"
        uploaded = c.post(
            "/api/v1/documents/files",
            headers=wh,
            files={"file": ("nda.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
            data={"folder": "Legal"},
        )
        assert uploaded.status_code == 201, uploaded.text
        file_row = uploaded.json()["data"]
        assert file_row["filename"] == "nda.pdf"
        assert file_row["content_type"] == "application/pdf"
        assert file_row["size_bytes"] == len(pdf_bytes)
        assert file_row["folder"] == "Legal"
        assert file_row["url"]
        capture("documents", "file_upload", uploaded)

        file_id = file_row["id"]

        # 2. GET /documents/files?folder=Legal -- shows the upload (summary).
        listed = c.get("/api/v1/documents/files", headers=wh, params={"folder": "Legal"})
        assert listed.status_code == 200, listed.text
        files = listed.json()["data"]["files"]
        assert len(files) == 1
        assert files[0]["id"] == file_id
        capture("documents", "file_list_by_folder", listed)

        # 3. GET /documents/files/{id} -- metadata + url.
        fetched = c.get(f"/api/v1/documents/files/{file_id}", headers=wh)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["data"]["id"] == file_id
        capture("documents", "file_get", fetched)

        # 4. A disallowed content-type -> 422 VALIDATION_ERROR.
        rejected = c.post(
            "/api/v1/documents/files",
            headers=wh,
            files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/x-msdownload")},
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
        capture("documents", "file_upload_bad_type_422", rejected)

        # 5. DELETE then GET -> 404 -- confirms the delete actually persisted
        # (a missing db.commit() would surface here as the file still existing).
        deleted = c.delete(f"/api/v1/documents/files/{file_id}", headers=wh)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"]["deleted"] is True
        capture("documents", "file_delete", deleted)

        gone = c.get(f"/api/v1/documents/files/{file_id}", headers=wh)
        assert gone.status_code == 404, gone.text
        capture("documents", "file_get_after_delete", gone)


def test_documents_sharing_journey(base_url, make_verified_user, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- same shape as the journeys above.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Sharing")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Create a document to share (business_plan template).
        created = c.post("/api/v1/documents", headers=wh, json={"template_key": "business_plan"})
        assert created.status_code == 201, created.text
        doc_id = created.json()["data"]["id"]

        # 2. POST /documents/{id}/shares -- JSON body, editor-only, 201, and
        # the response returns `link` (this is the ONLY response that ever
        # does -- list/get never include it).
        recipient = unique_email("shared-with")
        shared = c.post(
            f"/api/v1/documents/{doc_id}/shares",
            headers=wh,
            json={"email": recipient, "access_level": "view"},
        )
        assert shared.status_code == 201, shared.text
        share_body = shared.json()["data"]
        assert share_body["email"] == recipient
        assert share_body["access_level"] == "view"
        assert share_body["status"] == "active"
        assert share_body["link"]
        capture("documents", "share_create", shared)

        share_id = share_body["id"]
        link = share_body["link"]

        # 3. Confirm the SAME link was actually emailed -- read it back out of
        # the captured email JSON in the file mail dir (the mechanism auth
        # journeys use for verification/reset tokens; here the email body
        # carries a link, not a <code>, so `_latest_share_link` parses the
        # href directly instead of reusing `mailbox.latest_token_for`).
        emailed_link = _latest_share_link(recipient)
        assert emailed_link == link
        token = link.rsplit("/", 1)[-1]

        # 4. GET /shared/{token} -- PUBLIC, NO auth header at all -- returns
        # the document + access level, sets last_viewed_at.
        opened = c.get(f"/api/v1/shared/{token}")
        assert opened.status_code == 200, opened.text
        opened_body = opened.json()["data"]
        assert opened_body["document"]["id"] == doc_id
        assert opened_body["access_level"] == "view"
        capture("documents", "share_open", opened)

        # 5. GET /documents/{id}/shares -- per-document "Shared with" list,
        # last_viewed_at now set from the open above.
        listed = c.get(f"/api/v1/documents/{doc_id}/shares", headers=wh)
        assert listed.status_code == 200, listed.text
        doc_shares = listed.json()["data"]["shares"]
        assert len(doc_shares) == 1
        assert doc_shares[0]["id"] == share_id
        assert doc_shares[0]["last_viewed_at"] is not None
        assert "link" not in doc_shares[0]
        capture("documents", "share_list_by_document", listed)

        # 6. GET /documents/shares -- workspace "Shared with others" overview,
        # carries `document_id` (the per-document list above does not).
        overview = c.get("/api/v1/documents/shares", headers=wh)
        assert overview.status_code == 200, overview.text
        overview_shares = overview.json()["data"]["shares"]
        assert any(s["id"] == share_id and s["document_id"] == doc_id for s in overview_shares)
        capture("documents", "share_list_workspace", overview)

        # 7. DELETE revoke -- a missing db.commit() here would surface as the
        # public open below still succeeding instead of 404ing.
        revoked = c.delete(f"/api/v1/documents/{doc_id}/shares/{share_id}", headers=wh)
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["data"]["revoked"] is True
        capture("documents", "share_revoke", revoked)

        # 8. GET /shared/{token} -- 404 after revoke, uniform shape, no auth.
        gone = c.get(f"/api/v1/shared/{token}")
        assert gone.status_code == 404, gone.text
        capture("documents", "share_open_after_revoke", gone)


def test_documents_esignature_journey(base_url, make_verified_user, unique_email, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        # 0. Onboard a founder -- same shape as the journeys above.
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = _auth_header(access)

        _onboard_steps(c, auth, stage="validation", name="Cofoundaz Signatures")
        onboarded = c.post("/api/v1/onboarding/complete", headers=auth)
        assert onboarded.status_code == 200, onboarded.text

        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Upload a file to send for signature (Slice 2's /documents/files).
        pdf_bytes = b"%PDF-1.4\n%a tiny fake PDF for e2e signing\n%%EOF"
        uploaded = c.post(
            "/api/v1/documents/files",
            headers=wh,
            files={"file": ("investor-agreement.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        file_id = uploaded.json()["data"]["id"]

        # 2. POST /documents/files/{id}/signature-requests -- editor, 201, two
        # signers -- the response returns `signer_links` (once; list/get never).
        signer1_email = unique_email("signer-one")
        signer2_email = unique_email("signer-two")
        created = c.post(
            f"/api/v1/documents/files/{file_id}/signature-requests",
            headers=wh,
            json={
                "signers": [
                    {"email": signer1_email, "name": "Ada Investor"},
                    {"email": signer2_email, "name": "Bello Legal"},
                ],
                "title": "Investor Agreement",
            },
        )
        assert created.status_code == 201, created.text
        created_body = created.json()["data"]
        assert created_body["status"] == "awaiting"
        assert created_body["signed_count"] == 0
        assert created_body["total"] == 2
        signer_links = created_body["signer_links"]
        assert len(signer_links) == 2
        capture("documents", "signature_create", created)

        request_id = created_body["id"]

        # 3. For each signer: the SAME link was actually emailed -- read it back
        # out of the captured signature email (file mail dir) and cross-check
        # against the create response's one-time `signer_links`.
        emailed_link_1 = _latest_sign_link(signer1_email)
        emailed_link_2 = _latest_sign_link(signer2_email)
        assert emailed_link_1 == signer_links[0]
        assert emailed_link_2 == signer_links[1]
        token1 = emailed_link_1.rsplit("/", 1)[-1]
        token2 = emailed_link_2.rsplit("/", 1)[-1]

        # 4. GET /sign/{token} -- PUBLIC, NO auth header at all -- returns the
        # file + request title/status + the signer's own identity.
        view1 = c.get(f"/api/v1/sign/{token1}")
        assert view1.status_code == 200, view1.text
        view1_body = view1.json()["data"]
        assert view1_body["request"]["title"] == "Investor Agreement"
        assert view1_body["request"]["status"] == "awaiting"
        assert view1_body["file"]["id"] == file_id
        assert view1_body["signer"]["email"] == signer1_email
        capture("documents", "signature_sign_view", view1)

        # 5. POST /sign/{token} -- PUBLIC, typed-name signature -- first of two,
        # request stays `awaiting`.
        sign1 = c.post(f"/api/v1/sign/{token1}", json={"typed_name": "Ada Investor"})
        assert sign1.status_code == 200, sign1.text
        sign1_body = sign1.json()["data"]
        assert sign1_body["status"] == "awaiting"
        assert sign1_body["signed_count"] == 1
        capture("documents", "signature_sign_first", sign1)

        # 6. Second signer: view + sign -- this is the LAST signer, so the
        # request flips to `complete`.
        view2 = c.get(f"/api/v1/sign/{token2}")
        assert view2.status_code == 200, view2.text
        capture("documents", "signature_sign_view_second", view2)

        sign2 = c.post(f"/api/v1/sign/{token2}", json={"typed_name": "Bello Legal"})
        assert sign2.status_code == 200, sign2.text
        sign2_body = sign2.json()["data"]
        assert sign2_body["status"] == "complete"
        assert sign2_body["signed_count"] == 2
        assert sign2_body["total"] == 2
        assert sign2_body["completed_at"] is not None
        capture("documents", "signature_sign_second", sign2)

        # 6b. A signed signer's link is single-use -- re-opening it 404s.
        resigned = c.get(f"/api/v1/sign/{token1}")
        assert resigned.status_code == 404, resigned.text
        capture("documents", "signature_sign_after_signed_404", resigned)

        # 7. GET /documents/signature-requests/{id} -- one request, complete.
        fetched = c.get(f"/api/v1/documents/signature-requests/{request_id}", headers=wh)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["data"]["status"] == "complete"
        capture("documents", "signature_get", fetched)

        # 8. GET /documents/signature-requests -- workspace list shows 2 of 2 +
        # complete.
        listed = c.get("/api/v1/documents/signature-requests", headers=wh)
        assert listed.status_code == 200, listed.text
        rows = listed.json()["data"]["requests"]
        row = next(r for r in rows if r["id"] == request_id)
        assert row["signed_count"] == 2
        assert row["total"] == 2
        assert row["status"] == "complete"
        capture("documents", "signature_list", listed)

        # 9. Cancel path -- a second request, one signer, created then
        # cancelled before signing: the signer's link 404s afterward.
        uploaded2 = c.post(
            "/api/v1/documents/files",
            headers=wh,
            files={"file": ("nda.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert uploaded2.status_code == 201, uploaded2.text
        file2_id = uploaded2.json()["data"]["id"]

        signer3_email = unique_email("signer-cancelled")
        created2 = c.post(
            f"/api/v1/documents/files/{file2_id}/signature-requests",
            headers=wh,
            json={"signers": [{"email": signer3_email}], "title": "NDA"},
        )
        assert created2.status_code == 201, created2.text
        created2_body = created2.json()["data"]
        capture("documents", "signature_create_for_cancel", created2)

        request2_id = created2_body["id"]
        token3 = created2_body["signer_links"][0].rsplit("/", 1)[-1]

        cancelled = c.post(f"/api/v1/documents/signature-requests/{request2_id}/cancel", headers=wh)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["data"]["cancelled"] is True
        capture("documents", "signature_cancel", cancelled)

        gone = c.get(f"/api/v1/sign/{token3}")
        assert gone.status_code == 404, gone.text
        capture("documents", "signature_sign_after_cancel_404", gone)
