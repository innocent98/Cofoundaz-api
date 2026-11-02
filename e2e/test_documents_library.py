"""Live Documents library-core journey (Documents BE-B): versions, trash, rename/move/tag, folders,
search, summary fields, inline content, and per-person grants with "Shared with me".

Cast: a founder, a team member, a mentor and a consultant (members), a grantee who joins by EMAIL
after being granted (binds on first Shared-with-me call), a non-grantee consultant and a revoked
mentor. Proves against the running app that grants widen nothing but the `/shared-with-me` routes,
that trashed and revoked documents vanish for grantees and token links, and that the library
features behave over real HTTP.

Bodies are captured to `e2e/_captures/documents_library/*.json`, the verbatim source for
`docs/fe-integration-guide-documents-library.md`. Raw tokens in links are replaced with
`REDACTED_TOKEN`; the 30-day purge is not waited out live (unit-tested, see the guide).
"""

import io
import json
import re

import httpx

G = "documents_library"
_TOKEN_IN_LINK = re.compile(r"(/(?:sign|shared)/|token=)[A-Za-z0-9_\-\.]{20,}")


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
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]["user"]["id"]
    return {**auth, "X-Workspace-Id": ws}, me, email


def test_documents_library_journey(
    base_url, make_verified_user, unique_email, mailbox, capture_json
):
    def cap(name, resp):
        capture_json(G, name, {"status": resp.status_code, "body": json.loads(_redact(resp.text))})

    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        u = make_verified_user(c)
        auth = _auth(c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"])
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch(
            "/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Founder"}
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "LibraryCo"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
        )
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Grow"]})
        _resp = c.post("/api/v1/onboarding/complete", headers=auth)
        assert _resp.status_code == 200
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        founder = {**auth, "X-Workspace-Id": ws}
        team, _team_id, _ = _join(c, founder, ws, "team_member", unique_email, mailbox)
        mentor, mentor_id, _ = _join(c, founder, ws, "mentor", unique_email, mailbox)
        consultant, consultant_id, _ = _join(
            c, founder, ws, "business_consultant", unique_email, mailbox
        )

        # --- create, summary fields ---------------------------------------------------------------
        created = c.post(
            "/api/v1/documents",
            headers=founder,
            json={
                "title": "Seed pitch narrative",
                "tags": ["fundraising", "Pitch"],
                "folder": "Fundraising/Seed",
                "sections": [{"heading": "Problem", "body": "Founders lose track of paperwork"}],
            },
        )
        assert created.status_code == 201, created.text
        cap("document_create", created)
        doc = created.json()["data"]
        doc_id = doc["id"]
        assert doc["version"] == 1 and doc["owner"]["name"] == "Ada Founder"
        assert doc["tags"] == ["fundraising", "Pitch"] and doc["share_count"] == 0

        # --- versions: save twice, list, read the first, restore it --------------------------------
        def save(h, current, body, title="Seed pitch narrative"):
            return c.put(
                f"/api/v1/documents/{doc_id}",
                headers=h,
                json={
                    "title": title,
                    "status": "draft",
                    "folder": "Fundraising/Seed",
                    "version": current,
                    "sections": [{"heading": "Problem", "body": body}],
                },
            )

        s2 = save(team, 1, "Founders lose track of paperwork and deadlines")
        assert s2.status_code == 200, s2.text
        cap("document_save_v2", s2)
        s3 = save(
            founder,
            2,
            "Founders lose paperwork, deadlines and signatures",
            title="Seed pitch (draft 3)",
        )
        assert s3.status_code == 200, s3.text
        stale = save(founder, 2, "stale")
        assert stale.status_code == 409
        cap("document_save_stale_409", stale)
        versions = c.get(f"/api/v1/documents/{doc_id}/versions", headers=founder)
        assert [v["version"] for v in versions.json()["data"]["versions"]] == [3, 2, 1]
        cap("versions_list", versions)
        v1 = c.get(f"/api/v1/documents/{doc_id}/versions/1", headers=founder)
        assert v1.json()["data"]["sections"][0]["body"] == "Founders lose track of paperwork"
        cap("version_get_1", v1)
        restored = c.post(f"/api/v1/documents/{doc_id}/versions/1/restore", headers=founder)
        assert restored.status_code == 200 and restored.json()["data"]["version"] == 4
        cap("version_restore", restored)
        missing = c.get(f"/api/v1/documents/{doc_id}/versions/99", headers=founder)
        assert missing.status_code == 404
        cap("version_get_missing_404", missing)

        # --- rename / tag / move / status, no new version ------------------------------------------
        patched = c.patch(
            f"/api/v1/documents/{doc_id}",
            headers=founder,
            json={
                "title": "Seed pitch narrative v2",
                "tags": ["Pitch", "seed"],
                "folder": "Fundraising",
                "status": "final",
            },
        )
        assert patched.status_code == 200 and patched.json()["data"]["version"] == 4
        cap("document_patch", patched)
        bad_patch = c.patch(f"/api/v1/documents/{doc_id}", headers=founder, json={"title": "   "})
        assert bad_patch.status_code == 422
        cap("document_patch_422", bad_patch)

        # --- folders ----------------------------------------------------------------------------------
        made = c.post("/api/v1/documents/folders", headers=founder, json={"name": "Legal"})
        assert made.status_code == 201
        cap("folder_create", made)
        dup = c.post("/api/v1/documents/folders", headers=founder, json={"name": "Legal"})
        assert dup.status_code == 409
        cap("folder_create_duplicate_409", dup)
        tree = c.get("/api/v1/documents/folders", headers=founder)
        cap("folders_tree", tree)
        legal = next(f for f in tree.json()["data"]["folders"] if f["path"] == "Legal")
        renamed = c.patch(
            f"/api/v1/documents/folders/{legal['id']}", headers=founder, json={"name": "Contracts"}
        )
        assert renamed.status_code == 200
        cap("folder_rename", renamed)
        fund = next(f for f in tree.json()["data"]["folders"] if f["path"] == "Fundraising")
        not_empty = c.delete(f"/api/v1/documents/folders/{fund['id']}", headers=founder)
        assert not_empty.status_code == 409
        cap("folder_delete_not_empty_409", not_empty)
        std = c.post("/api/v1/documents/folders/standard", headers=founder)
        assert std.status_code == 201
        cap("folders_standard", std)

        # --- search -------------------------------------------------------------------------------------
        hit = c.get("/api/v1/documents", headers=founder, params={"q": "signatures deadlines"})
        assert [
            d["id"] for d in hit.json()["data"]["documents"]
        ] == []  # restored v1 text has no "signatures"
        hit = c.get("/api/v1/documents", headers=founder, params={"q": "paperwork"})
        assert [d["id"] for d in hit.json()["data"]["documents"]] == [doc_id]
        cap("documents_search", hit)
        by_tag = c.get("/api/v1/documents", headers=founder, params={"tag": "seed"})
        assert [d["id"] for d in by_tag.json()["data"]["documents"]] == [doc_id]
        assert (
            c.get("/api/v1/documents", headers=founder, params={"q": "zzz-nothing"}).json()["data"][
                "documents"
            ]
            == []
        )

        # --- files: rename/tag/move, inline content, download ----------------------------------------
        up = c.post(
            "/api/v1/documents/files",
            headers=founder,
            files={
                "file": ("term-sheet.pdf", io.BytesIO(b"%PDF-1.4\n%e2e\n%%EOF"), "application/pdf")
            },
            data={"folder": "Fundraising"},
        )
        assert up.status_code == 201, up.text
        cap("file_upload", up)
        file_id = up.json()["data"]["id"]
        fpatch = c.patch(
            f"/api/v1/documents/files/{file_id}",
            headers=founder,
            json={"filename": "Term sheet (signed).pdf", "tags": ["legal"], "folder": "Contracts"},
        )
        assert fpatch.status_code == 200
        cap("file_patch", fpatch)
        content = c.get(f"/api/v1/documents/files/{file_id}/content", headers=founder)
        assert content.status_code == 200 and content.json()["data"]["viewer"] == "pdf"
        cap("file_content_link", content)
        inline = c.get(content.json()["data"]["url"])
        assert inline.status_code == 200 and inline.headers["content-disposition"].startswith(
            "inline;"
        )
        assert (
            c.get(f"/api/v1/documents/files/{file_id}/content", headers=mentor).status_code == 403
        )
        files_q = c.get("/api/v1/documents/files", headers=founder, params={"q": "term"})
        assert [f["id"] for f in files_q.json()["data"]["files"]] == [file_id]
        cap("files_search", files_q)

        # --- grants: mentor by account, grantee by email (no account yet) ---------------------------------
        g_view = c.post(
            f"/api/v1/documents/{doc_id}/grants",
            headers=founder,
            json={"user_id": mentor_id, "permission": "view"},
        )
        assert g_view.status_code == 201, g_view.text
        cap("grant_create_member", g_view)
        invitee = unique_email("grantee")
        g_email = c.post(
            f"/api/v1/documents/{doc_id}/grants",
            headers=team,
            json={"email": invitee.upper(), "permission": "comment"},
        )
        assert g_email.status_code == 201 and g_email.json()["data"]["status"] == "pending"
        cap("grant_create_email_pending", g_email)
        dup_grant = c.post(
            f"/api/v1/documents/{doc_id}/grants", headers=founder, json={"user_id": mentor_id}
        )
        assert dup_grant.status_code == 409
        cap("grant_duplicate_409", dup_grant)
        member_target = c.post(
            f"/api/v1/documents/{doc_id}/grants", headers=founder, json={"user_id": _team_id}
        )
        assert member_target.status_code == 422
        cap("grant_full_access_member_422", member_target)
        grants = c.get(f"/api/v1/documents/{doc_id}/grants", headers=founder)
        assert len(grants.json()["data"]["grants"]) == 2
        cap("grants_list", grants)
        assert c.get("/api/v1/documents/grants", headers=founder).status_code == 200
        [summary] = [
            d
            for d in c.get("/api/v1/documents", headers=founder).json()["data"]["documents"]
            if d["id"] == doc_id
        ]
        assert summary["share_count"] == 2

        # BE-A intact: the mentor holding a grant still gets 403 on every owner route.
        for url in (
            "/api/v1/documents",
            f"/api/v1/documents/{doc_id}",
            f"/api/v1/documents/{doc_id}/versions",
            f"/api/v1/documents/{doc_id}/grants",
            "/api/v1/documents/folders",
            "/api/v1/documents/trash",
        ):
            r = c.get(url, headers=mentor)
            assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN", (url, r.text)
        cap("granted_mentor_documents_403", c.get("/api/v1/documents", headers=mentor))

        # The mentor's Shared with me: exactly the granted document, no folder/tags.
        mine = c.get("/api/v1/documents/shared-with-me", headers=mentor)
        assert [d["id"] for d in mine.json()["data"]["documents"]] == [doc_id]
        cap("shared_with_me_list", mine)
        one = c.get(f"/api/v1/documents/shared-with-me/{doc_id}", headers=mentor)
        assert one.status_code == 200 and "folder" not in one.json()["data"]
        cap("shared_with_me_get", one)
        # The non-grantee consultant sees nothing; the document looks absent, not forbidden.
        none = c.get("/api/v1/documents/shared-with-me", headers=consultant)
        assert none.json()["data"]["documents"] == []
        cap("shared_with_me_empty", none)
        absent = c.get(f"/api/v1/documents/shared-with-me/{doc_id}", headers=consultant)
        assert absent.status_code == 404
        cap("shared_with_me_get_not_granted_404", absent)

        # Comments: view-only mentor is refused; the owner side reads the thread.
        refused = c.post(
            f"/api/v1/documents/shared-with-me/{doc_id}/comments",
            headers=mentor,
            json={"body": "Nice"},
        )
        assert refused.status_code == 403
        cap("shared_comment_view_only_403", refused)
        upgrade = c.patch(
            f"/api/v1/documents/{doc_id}/grants/{g_view.json()['data']['id']}",
            headers=founder,
            json={"permission": "comment"},
        )
        assert upgrade.status_code == 200
        cap("grant_update_permission", upgrade)
        posted = c.post(
            f"/api/v1/documents/shared-with-me/{doc_id}/comments",
            headers=mentor,
            json={"body": "Tighten the problem statement"},
        )
        assert posted.status_code == 201, posted.text
        cap("shared_comment_create", posted)
        thread = c.get(f"/api/v1/documents/{doc_id}/comments", headers=founder)
        assert [x["body"] for x in thread.json()["data"]["comments"]] == [
            "Tighten the problem statement"
        ]
        cap("document_comments_owner", thread)

        # The email grantee signs up AFTER the grant: verified email binds on the first call.
        pw = "Grantee-" + invitee.split("@")[0][-8:] + "-9"
        _resp = c.post("/api/v1/auth/signup", json={"email": invitee, "password": pw})
        assert _resp.status_code in (200, 201)
        c.post(
            "/api/v1/auth/verify",
            json={"token": mailbox.latest_token_for(invitee, subject_contains="Verify")},
        )
        grantee = _auth(
            c.post("/api/v1/auth/login", json={"email": invitee, "password": pw}).json()["data"][
                "access_token"
            ]
        )
        bound = c.get("/api/v1/documents/shared-with-me", headers=grantee)
        assert [d["id"] for d in bound.json()["data"]["documents"]] == [doc_id]
        assert bound.json()["data"]["documents"][0]["permission"] == "comment"
        cap("shared_with_me_after_signup_binding", bound)
        grants2 = c.get(f"/api/v1/documents/{doc_id}/grants", headers=founder).json()["data"][
            "grants"
        ]
        # the member grantee is identified; the outside email stays "pending" (no account probing)
        assert sorted(g["status"] for g in grants2) == ["active", "pending"]
        outsider_h = {**grantee, "X-Workspace-Id": ws}  # not a member: 403 even with a grant
        outsider_try = c.get("/api/v1/documents", headers=outsider_h)
        assert outsider_try.status_code == 403
        cap("granted_non_member_documents_403", outsider_try)
        mail = mailbox.latest_for(invitee)
        assert mail is not None

        # --- notifications -------------------------------------------------------------------------------
        # A save AFTER the grants: the mentor (a member with a grant) is told about the new version.
        v5 = save(founder, 4, "Founders lose track of paperwork", title="Seed pitch narrative v2")
        assert v5.status_code == 200, v5.text
        inbox_m = c.get("/api/v1/notifications", headers=mentor).json()["data"]["notifications"]
        types = [n["type"] for n in inbox_m]
        assert "document.grant.created" in types and "document.version.created" in types
        capture_json(
            G,
            "mentor_notifications",
            {
                "status": 200,
                "body": [
                    {k: n[k] for k in ("type", "title", "body", "data")}
                    for n in inbox_m
                    if n["type"].startswith("document.")
                ],
            },
        )
        inbox_f = c.get("/api/v1/notifications", headers=founder).json()["data"]["notifications"]
        assert any(
            n["type"] == "document.version.created" for n in inbox_f
        )  # the team member's save
        capture_json(
            G,
            "founder_notifications",
            {
                "status": 200,
                "body": [
                    {k: n[k] for k in ("type", "title", "body", "data")}
                    for n in inbox_f
                    if n["type"].startswith("document.")
                ],
            },
        )

        # --- token link + trash ---------------------------------------------------------------------------
        share = c.post(
            f"/api/v1/documents/{doc_id}/shares",
            headers=founder,
            json={"email": unique_email("viewer")},
        )
        link_token = share.json()["data"]["link"].rsplit("/", 1)[-1]
        assert c.get(f"/api/v1/shared/{link_token}").status_code == 200
        trashed = c.delete(f"/api/v1/documents/{doc_id}", headers=team)
        assert trashed.status_code == 200
        cap("document_trash", trashed)
        assert c.get(f"/api/v1/documents/{doc_id}", headers=founder).status_code == 404
        assert (
            c.get("/api/v1/documents/shared-with-me", headers=mentor).json()["data"]["documents"]
            == []
        )
        gone_for_grantee = c.get(f"/api/v1/documents/shared-with-me/{doc_id}", headers=grantee)
        assert gone_for_grantee.status_code == 404
        cap("shared_with_me_get_trashed_404", gone_for_grantee)
        link_dead = c.get(f"/api/v1/shared/{link_token}")
        assert link_dead.status_code == 404
        cap("shared_token_trashed_404", link_dead)
        trash_list = c.get("/api/v1/documents/trash", headers=founder)
        assert [d["id"] for d in trash_list.json()["data"]["documents"]] == [doc_id]
        cap("trash_list", trash_list)
        not_trashed_perm = c.delete(f"/api/v1/documents/{doc_id}/permanent", headers=founder)
        assert not_trashed_perm.status_code == 200  # it IS trashed, so permanent is allowed ...
        # ... so recreate to show restore instead.
        doc2 = c.post("/api/v1/documents", headers=founder, json={"title": "Restore me"}).json()[
            "data"
        ]["id"]
        c.post(f"/api/v1/documents/{doc2}/grants", headers=founder, json={"user_id": mentor_id})
        c.delete(f"/api/v1/documents/{doc2}", headers=founder)
        live_perm = c.delete(f"/api/v1/documents/{doc2}/permanent", headers=founder)
        assert live_perm.status_code == 200
        doc3 = c.post(
            "/api/v1/documents", headers=founder, json={"title": "Live, not trashed"}
        ).json()["data"]["id"]
        not_in_trash = c.delete(f"/api/v1/documents/{doc3}/permanent", headers=founder)
        assert not_in_trash.status_code == 409
        cap("document_permanent_not_in_trash_409", not_in_trash)
        c.post(f"/api/v1/documents/{doc3}/grants", headers=founder, json={"user_id": mentor_id})
        c.delete(f"/api/v1/documents/{doc3}", headers=founder)
        assert (
            c.get("/api/v1/documents/shared-with-me", headers=mentor).json()["data"]["documents"]
            == []
        )
        back = c.post(f"/api/v1/documents/{doc3}/restore", headers=team)
        assert back.status_code == 200 and back.json()["data"]["trashed_at"] is None
        cap("document_restore", back)
        assert [
            d["id"]
            for d in c.get("/api/v1/documents/shared-with-me", headers=mentor).json()["data"][
                "documents"
            ]
        ] == [doc3]

        # --- revoke -------------------------------------------------------------------------------------
        [g3] = c.get(f"/api/v1/documents/{doc3}/grants", headers=founder).json()["data"]["grants"]
        revoked = c.delete(f"/api/v1/documents/{doc3}/grants/{g3['id']}", headers=team)
        assert revoked.status_code == 200
        cap("grant_revoke", revoked)
        after = c.get(f"/api/v1/documents/shared-with-me/{doc3}", headers=mentor)
        assert after.status_code == 404
        cap("shared_with_me_get_revoked_404", after)
        history = c.get(
            f"/api/v1/documents/{doc3}/grants", headers=founder, params={"include_revoked": "true"}
        )
        assert history.json()["data"]["grants"][0]["status"] == "revoked"
        cap("grants_list_with_revoked", history)

        # --- file trash -------------------------------------------------------------------------------------
        ft = c.delete(f"/api/v1/documents/files/{file_id}", headers=founder)
        assert ft.status_code == 200
        cap("file_trash", ft)
        assert c.get(f"/api/v1/documents/files/{file_id}", headers=founder).status_code == 404
        tl = c.get("/api/v1/documents/trash", headers=founder)
        assert [f["id"] for f in tl.json()["data"]["files"]] == [file_id]
        cap("trash_list_with_file", tl)
        fr = c.post(f"/api/v1/documents/files/{file_id}/restore", headers=founder)
        assert fr.status_code == 200
        cap("file_restore", fr)
        c.delete(f"/api/v1/documents/files/{file_id}", headers=founder)
        _resp = c.delete(f"/api/v1/documents/files/{file_id}/permanent", headers=founder)
        assert _resp.status_code == 200
        final_tree = c.get("/api/v1/documents/folders", headers=founder)
        cap("folders_tree_final", final_tree)
