"""Live Document Library Core journey (Module 18, Slice 1): a founder onboards,
browses the in-code template catalog, instantiates a Business Plan document
from the `business_plan` template, reads it back full (with `sections`), edits
it with a full-replace `PUT` (version 1 -> 2, status draft -> final, a folder),
hits the optimistic-concurrency 409 on a stale re-PUT, lists documents by
`folder` (confirming the list is SUMMARY-shaped -- no `sections` key), then
deletes it and confirms the 404.

Every response body along the way is captured to `e2e/_captures/documents/
*.json` -- those files are the verbatim source for
`docs/fe-integration-guide-documents-templates.md`. They must be REAL bodies
from this live run, complete and untrimmed.

Document Library Core has no roadmap/assessment dependency, so onboarding here
is just steps 1-4 + complete -- same shape as e2e/test_business_builder.py and
e2e/test_journal.py.
"""

import httpx


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
