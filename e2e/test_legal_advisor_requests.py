"""Live Legal Advisor Requests journey (Module 13, Slice 2, PRD 13.6).

`test_advisor_request_journey`: a founder onboards, brings in two legal advisors and a mentor,
uploads a contract through Documents' own file route, and asks one advisor to review it. Then:

  * the **named advisor** sees exactly that request, through the narrower shape that carries no
    user ids (D-LEG-11, D-LEG-15); the **second advisor** sees nothing;
  * the advisor **reads the attachment** through `GET .../document`, which is the request itself
    acting as the grant, with no share token and nothing to revoke (D-LEG-16);
  * the advisor's **first comment** moves the request to `in_review` (D-LEG-17), and the
    founder's view of the same thread still carries author ids;
  * the advisor **returns a marked-up .docx**, which goes through Documents' upload (its 15 MB
    cap, its allowlist, a magic-byte sniff and the scan) and moves the request to `returned`;
  * a second request is **cancelled**, after which the document 404s and new comments are
    refused while the thread stays readable as history (D-LEG-14);
  * a **mentor** is 403 everywhere and a second workspace gets a 404, never a 403.

`_auth`, `_onboard_steps`, `_workspace` and `_join` are copied from `e2e/test_legal.py`, which is
how e2e files share helpers here.

Every response body is captured to `e2e/_captures/legal_advisor_requests/*.json` -- those files
are the verbatim source for `docs/fe-integration-guide-legal-advisor-requests.md`. They must be
REAL bodies from this live run, complete and untrimmed. The attachment download is binary, so it
is asserted rather than captured.
"""

import io
import zipfile

import httpx
import pytest

BASE = "/api/v1/legal"
REQUESTS = f"{BASE}/advisor-requests"

PDF = b"%PDF-1.4 supplier agreement, executed copy"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx() -> bytes:
    """A real minimal .docx: the sniffer opens the upload as a ZIP and wants a readable central
    directory, `[Content_Types].xml` and a `word/` entry, so fake PK bytes would be refused."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _onboard_steps(c: httpx.Client, auth: dict, *, stage: str, name: str) -> None:
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


def _workspace(c: httpx.Client, make_verified_user, *, name: str) -> dict:
    u = make_verified_user(c)
    access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
    auth = _auth(access)
    _onboard_steps(c, auth, stage="validation", name=name)
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def _join(c, wh, role, unique_email, mailbox) -> dict:
    """Invite `role` into the founder's workspace and accept as a fresh verified user."""
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
    return {**auth, "X-Workspace-Id": wh["X-Workspace-Id"]}


def _user_id(c, headers) -> str:
    """The caller's own user id. Read from `/auth/me` rather than matched out of the members
    list, so the journey does not depend on which invitation went out first."""
    r = c.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["user"]["id"]


@pytest.mark.critical
def test_advisor_request_journey(base_url, make_verified_user, unique_email, mailbox, capture):
    with httpx.Client(base_url=base_url, timeout=20.0) as c:
        wh = _workspace(c, make_verified_user, name="Cofoundaz Legal Slice 2")

        # Two advisors, so "scoped to the advisor" means something, plus a mentor for the 403s.
        first = _join(c, wh, "legal_advisor", unique_email, mailbox)
        second = _join(c, wh, "legal_advisor", unique_email, mailbox)
        mentor = _join(c, wh, "mentor", unique_email, mailbox)
        named_id = _user_id(c, first)

        # How the FE finds who to send a request to (gap row 43): the members list carries each
        # role, and it is founder + team member only, which is exactly who raises a request.
        members = c.get("/api/v1/team/members", headers=wh)
        assert members.status_code == 200, members.text
        advisors = [m for m in members.json()["data"] if m["role"] == "legal_advisor"]
        assert len(advisors) == 2, members.text
        assert named_id in {m["user_id"] for m in advisors}
        capture("legal_advisor_requests", "members_with_advisors", members)

        # 1. The contract, uploaded through Documents' own route: private, scanned, a real
        #    library file. This is what row 39 will hand to a request from 13.3 later.
        upload = c.post(
            "/api/v1/documents/files",
            headers=wh,
            files={"file": ("supplier-agreement.pdf", io.BytesIO(PDF), "application/pdf")},
        )
        assert upload.status_code == 201, upload.text
        file_id = upload.json()["data"]["id"]
        assert upload.json()["data"]["scan_status"] == "clean"
        capture("legal_advisor_requests", "contract_uploaded", upload)

        # 2. The request itself.
        made = c.post(
            REQUESTS,
            headers=wh,
            json={
                "subject": "Please check clause 7 before we sign",
                "advisor_user_id": named_id,
                "file_id": file_id,
                "note": "The non-compete looks far too wide to me.",
            },
        )
        assert made.status_code == 200, made.text
        rid = made.json()["data"]["id"]
        assert made.json()["data"]["status"] == "requested"
        assert made.json()["data"]["attachment"]["kind"] == "file"
        capture("legal_advisor_requests", "request_created", made)

        # 3. The named advisor sees it, in the narrower shape: no user ids anywhere.
        mine = c.get(REQUESTS, headers=first)
        assert mine.status_code == 200, mine.text
        rows = mine.json()["data"]["advisor_requests"]
        assert len(rows) == 1 and rows[0]["id"] == rid
        assert "advisor" not in rows[0] and "created_by" not in rows[0]
        assert set(rows[0]["requested_by"]) == {"name"}
        assert named_id not in mine.text
        capture("legal_advisor_requests", "advisor_list", mine)

        # 4. The OTHER advisor sees nothing: scoped to the advisor, not to the workspace.
        theirs = c.get(REQUESTS, headers=second)
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["data"]["advisor_requests"] == []
        capture("legal_advisor_requests", "other_advisor_sees_nothing", theirs)

        # 5. The attachment read: the request IS the grant. Binary, so asserted not captured.
        doc = c.get(f"{REQUESTS}/{rid}/document", headers=first)
        assert doc.status_code == 200, doc.text
        assert doc.content == PDF
        assert doc.headers["x-content-type-options"] == "nosniff"
        assert doc.headers["cache-control"] == "private, no-store"
        assert "supplier-agreement.pdf" in doc.headers["content-disposition"]

        # 6. The advisor's FIRST comment moves it to in_review (D-LEG-17), with no new control.
        said = c.post(
            f"{REQUESTS}/{rid}/comments",
            headers=first,
            json={"body": "Clause 7 is unenforceable as drafted. Suggest 12 months, Lagos only."},
        )
        assert said.status_code == 200, said.text
        assert set(said.json()["data"]["author"]) == {"name"}
        capture("legal_advisor_requests", "advisor_comment", said)

        moved = c.get(f"{REQUESTS}/{rid}", headers=wh)
        assert moved.status_code == 200, moved.text
        assert moved.json()["data"]["status"] == "in_review"
        capture("legal_advisor_requests", "request_in_review", moved)

        # 7. The founder's view of the same thread keeps author ids; the advisor's did not.
        thread = c.get(f"{REQUESTS}/{rid}/comments", headers=wh)
        assert thread.status_code == 200, thread.text
        assert thread.json()["data"]["comments"][0]["author"]["id"]
        capture("legal_advisor_requests", "founder_thread", thread)

        # 8. The reviewed draft: a .docx, which Slice 1's proof sniff would have refused.
        draft = c.post(
            f"{REQUESTS}/{rid}/reviewed-draft",
            headers=first,
            files={"file": ("supplier-agreement-reviewed.docx", io.BytesIO(_docx()), DOCX_TYPE)},
        )
        assert draft.status_code == 200, draft.text
        assert draft.json()["data"]["status"] == "returned"
        assert draft.json()["data"]["returned_file"]["filename"] == (
            "supplier-agreement-reviewed.docx"
        )
        capture("legal_advisor_requests", "reviewed_draft_returned", draft)

        # A returned review cannot be cancelled: it is already complete.
        too_late = c.post(f"{REQUESTS}/{rid}/cancel", headers=wh)
        assert too_late.status_code == 409, too_late.text
        capture("legal_advisor_requests", "cancel_after_return_409", too_late)

        # 9. A second request, cancelled, to prove access ends with the request itself.
        second_made = c.post(
            REQUESTS,
            headers=wh,
            json={
                "subject": "Check the NDA too",
                "advisor_user_id": named_id,
                "file_id": file_id,
            },
        )
        assert second_made.status_code == 200, second_made.text
        rid2 = second_made.json()["data"]["id"]

        reachable = c.get(f"{REQUESTS}/{rid2}/document", headers=first)
        assert reachable.status_code == 200, reachable.text

        cancelled = c.post(f"{REQUESTS}/{rid2}/cancel", headers=wh)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["data"]["status"] == "cancelled"
        capture("legal_advisor_requests", "request_cancelled", cancelled)

        gone = c.get(f"{REQUESTS}/{rid2}/document", headers=first)
        assert gone.status_code == 404, gone.text
        refused = c.post(
            f"{REQUESTS}/{rid2}/comments", headers=first, json={"body": "Anything else?"}
        )
        assert refused.status_code == 409, refused.text
        capture("legal_advisor_requests", "comment_after_cancel_409", refused)

        # The history of a cancelled request is not hidden, only closed.
        still_readable = c.get(f"{REQUESTS}/{rid2}/comments", headers=wh)
        assert still_readable.status_code == 200, still_readable.text

        # 10. The mentor is outside the reader set entirely.
        blocked = c.get(REQUESTS, headers=mentor)
        assert blocked.status_code == 403, blocked.text
        capture("legal_advisor_requests", "mentor_forbidden", blocked)
        assert c.get(f"{REQUESTS}/{rid}/document", headers=mentor).status_code == 403

        # 11. Another workspace: 404, never 403.
        other = _workspace(c, make_verified_user, name="Another Startup")
        cross = c.get(f"{REQUESTS}/{rid}", headers=other)
        assert cross.status_code == 404, cross.text
        capture("legal_advisor_requests", "cross_tenant_404", cross)

        # 12. The advisor was told, and the copy names the founder without any address (row 47).
        inbox = c.get("/api/v1/notifications", headers=first)
        assert inbox.status_code == 200, inbox.text
        mine_notes = [
            n
            for n in inbox.json()["data"]["notifications"]
            if n["type"] == "legal.advisor_request.created"
        ]
        assert len(mine_notes) == 2, inbox.text
        assert "requested your review of" in mine_notes[0]["title"]
        assert "@" not in mine_notes[0]["title"]
        capture("legal_advisor_requests", "advisor_inbox", inbox)
