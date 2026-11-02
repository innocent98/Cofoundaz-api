"""Live Documents BE-C journey: template generator, AI section drafts, legal drafts and export.

Cast: a founder with a Business Builder (lean canvas + a revenue stream), a mentor who is refused.
Proves against the running app that a template pre-fills from the workspace, that an AI draft runs
through the worker and fills only what is empty, that the legal drafts carry the disclaimer, and
that an export is a stored, scanned, signable file with the same disclaimer printed on it.

Bodies are captured to `e2e/_captures/documents_templates_export/*.json`, the verbatim source for
`docs/fe-integration-guide-documents-templates-export.md`. Raw tokens in links are replaced with
`REDACTED_TOKEN`. Not exercised live: the daily AI budget being exhausted and a provider failure
(both unit-tested with a fake client; the live stack uses the stub provider).
"""

import io
import json
import re

import httpx
from docx import Document as DocxDocument

G = "documents_templates_export"
_TOKEN = re.compile(r"((?:token=)|(?:/sign/)|(?:/shared/))[A-Za-z0-9_\-\.]{20,}")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _redact(text: str) -> str:
    return _TOKEN.sub(r"\1REDACTED_TOKEN", text)


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import documents_ai as _docs  # noqa: F401  (registers the draft job)

    db = SessionLocal()
    try:
        for _ in range(500):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def test_documents_templates_export_journey(
    base_url, make_verified_user, unique_email, mailbox, capture_json
):
    def cap(name, resp):
        capture_json(G, name, {"status": resp.status_code, "body": json.loads(_redact(resp.text))})

    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
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
        assert c.post("/api/v1/onboarding/complete", headers=auth).status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        founder = {**auth, "X-Workspace-Id": ws}

        # a mentor joins (refused everywhere below)
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
        assert (
            c.post(
                "/api/v1/invitations/accept", headers=_auth(m_access), json={"token": invite_token}
            ).status_code
            == 200
        )
        mentor = {**_auth(m_access), "X-Workspace-Id": ws}

        # --- Business Builder data to pre-fill from -----------------------------------------------
        canvas = c.get("/api/v1/business-builder/canvases/lean", headers=founder).json()["data"]
        saved = c.put(
            "/api/v1/business-builder/canvases/lean",
            headers=founder,
            json={
                "version": canvas["version"],
                "blocks": {
                    "problem": ["Traders lose money to slow cash handling", "Card fraud"],
                    "solution": ["A mobile wallet for market traders"],
                    "customer_segments": ["Open-air market traders"],
                },
            },
        )
        assert saved.status_code == 200, saved.text
        rec = c.post(
            "/api/v1/business-builder/revenue-streams",
            headers=founder,
            json={"data": {"name": "Transaction fees", "pricing_basis": "1% per payment"}},
        )
        assert rec.status_code == 201, rec.text

        # --- catalog and form state ---------------------------------------------------------------
        catalog = c.get("/api/v1/document-templates", headers=founder)
        assert catalog.status_code == 200
        cap("templates_list", catalog)
        keys = {t["key"]: t for t in catalog.json()["data"]["templates"]}
        assert {t["category"] for t in keys.values()} == {
            "Legal",
            "Financial",
            "Pitch",
            "Operational",
        }
        form = c.get("/api/v1/document-templates/business_plan", headers=founder)
        assert form.status_code == 200
        cap("template_form_business_plan", form)
        secs = {s["heading"]: s for s in form.json()["data"]["form"]["sections"]}
        assert (
            secs["Problem"]["source"] == "business_builder" and secs["Problem"]["state"] == "filled"
        )
        assert secs["Go-to-Market"]["state"] == "empty" and secs["Go-to-Market"]["ai_draftable"]
        nda_form = c.get("/api/v1/document-templates/nda", headers=founder)
        cap("template_form_nda", nda_form)
        assert nda_form.json()["data"]["form"]["unfilled_required"]
        mentor_form = c.get("/api/v1/document-templates/nda", headers=mentor)
        assert mentor_form.status_code == 403
        cap("template_form_mentor_403", mentor_form)

        # --- instantiate with an AI draft ---------------------------------------------------------
        inst = c.post(
            "/api/v1/documents/templates/business_plan/instantiate",
            headers=founder,
            json={"title": "NovaPay business plan", "folder": "Fundraising", "ai_draft": True},
        )
        assert inst.status_code == 201, inst.text
        cap("instantiate_business_plan_ai", inst)
        data = inst.json()["data"]
        plan_id, job_id = data["document"]["id"], data["ai"]["job_id"]
        assert data["ai"]["status"] == "queued" and "go_to_market" in data["ai"]["sections"]
        queued = c.get(f"/api/v1/jobs/{job_id}", headers=founder)
        cap("job_queued", queued)
        _drain()
        done = c.get(f"/api/v1/jobs/{job_id}", headers=founder)
        assert done.json()["data"]["status"] == "succeeded", done.text
        assert done.json()["data"]["result"]["status"] == "ready"
        cap("job_done", done)
        plan = c.get(f"/api/v1/documents/{plan_id}", headers=founder)
        cap("document_after_ai", plan)
        by_h = {s["heading"]: s["body"] for s in plan.json()["data"]["sections"]}
        assert "[stub-llm]" in by_h["Go-to-Market"] and by_h["The Ask"] == ""
        assert by_h["Problem"].startswith("- Traders lose money")
        assert plan.json()["data"]["version"] == 2 and plan.json()["data"]["ai_generated"] is True
        again = c.post(f"/api/v1/documents/{plan_id}/ai-draft", headers=founder, json={})
        assert again.status_code == 422
        cap("ai_draft_nothing_left_422", again)
        bad_ai = c.post(
            "/api/v1/documents/templates/nda/instantiate", headers=founder, json={"ai_draft": True}
        )
        assert bad_ai.status_code == 422
        cap("instantiate_nda_ai_422", bad_ai)

        # --- legal draft + disclaimer -------------------------------------------------------------
        nda = c.post(
            "/api/v1/documents/templates/nda/instantiate",
            headers=founder,
            json={
                "values": {
                    "receiving_party": "Bright Ventures Ltd",
                    "purpose": "Evaluating a seed investment",
                    "governing_law": "Lagos State, Nigeria",
                }
            },
        )
        assert nda.status_code == 201, nda.text
        cap("instantiate_nda", nda)
        nda_doc = nda.json()["data"]["document"]
        assert nda_doc["disclaimer"] and nda_doc["kind"] == "nda"
        unknown = c.post(
            "/api/v1/documents/templates/nda/instantiate",
            headers=founder,
            json={"values": {"nonsense": "x"}},
        )
        assert unknown.status_code == 422
        cap("instantiate_unknown_field_422", unknown)
        assert (
            c.post(
                "/api/v1/documents/templates/nda/instantiate", headers=mentor, json={}
            ).status_code
            == 403
        )

        # --- export -------------------------------------------------------------------------------
        pdf = c.post(
            f"/api/v1/documents/{nda_doc['id']}/export", headers=founder, json={"format": "pdf"}
        )
        assert pdf.status_code == 201, pdf.text
        cap("export_pdf", pdf)
        pdf_file = pdf.json()["data"]
        assert pdf_file["scan_status"] == "clean" and pdf_file["origin"] == "export"
        again_pdf = c.post(
            f"/api/v1/documents/{nda_doc['id']}/export", headers=founder, json={"format": "pdf"}
        )
        assert again_pdf.status_code == 200 and again_pdf.json()["data"]["id"] == pdf_file["id"]
        cap("export_pdf_again_200", again_pdf)
        docx = c.post(
            f"/api/v1/documents/{nda_doc['id']}/export", headers=founder, json={"format": "docx"}
        )
        assert docx.status_code == 201, docx.text
        cap("export_docx", docx)
        link = c.get(f"/api/v1/documents/files/{pdf_file['id']}/download", headers=founder)
        cap("export_pdf_download_link", link)
        body = c.get(link.json()["data"]["url"]).content
        assert body.startswith(b"%PDF")
        capture_json(G, "export_pdf_bytes", {"starts_with": body[:8].decode(), "size": len(body)})
        dlink = c.get(
            f"/api/v1/documents/files/{docx.json()['data']['id']}/download", headers=founder
        ).json()["data"]["url"]
        parsed = DocxDocument(io.BytesIO(c.get(dlink).content))
        texts = [p.text for p in parsed.paragraphs]
        assert any("not legal advice" in t for t in texts) and any(
            "Bright Ventures Ltd" in t for t in texts
        )
        capture_json(G, "export_docx_paragraphs", {"first_paragraphs": texts[:4]})

        files = c.get("/api/v1/documents/files", headers=founder)
        cap("files_list_with_exports", files)
        sign = c.post(
            f"/api/v1/documents/files/{pdf_file['id']}/signature-requests",
            headers=founder,
            json={"signers": [{"email": "signer@example.com", "name": "Bright Signer"}]},
        )
        assert sign.status_code == 201, sign.text
        cap("export_sent_for_signature", sign)

        denied = c.post(
            f"/api/v1/documents/{nda_doc['id']}/export", headers=mentor, json={"format": "pdf"}
        )
        assert denied.status_code == 403
        cap("export_mentor_403", denied)
        trashed = c.delete(f"/api/v1/documents/{plan_id}", headers=founder)
        assert trashed.status_code == 200
        gone = c.post(
            f"/api/v1/documents/{plan_id}/export", headers=founder, json={"format": "pdf"}
        )
        assert gone.status_code == 404
        cap("export_trashed_404", gone)
        bad_fmt = c.post(
            f"/api/v1/documents/{nda_doc['id']}/export", headers=founder, json={"format": "rtf"}
        )
        assert bad_fmt.status_code == 422
        cap("export_bad_format_422", bad_fmt)
