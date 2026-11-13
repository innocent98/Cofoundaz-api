"""Live Investor Readiness slice B journey: a founder uploads a pitch deck PDF through Documents,
queues a review, polls the job, reads the review (scores, top fixes, slide notes, thumbnails),
re-uploads a new version, hits the dedupe, the failure paths (a blank deck, a non-PDF, a missing
file) and the role guards, and watches the Story signal and the reviewed-deck must-have move.

The job is drained in this process (as the worker would) with a CANNED language model, so the
numbers and notes below are the fake's, not a real model's: the live part is the API, the queue,
PDF text extraction, the layout check and thumbnail rendering. Responses go to
e2e/_captures/readiness_deck/ and are the verbatim source of the deck section of
docs/fe-integration-guide-investor-readiness.md.
"""

import io
import os

import httpx
import pytest

# This journey drives the app with an in-process canned LLM (`tests.*_helpers`), which cannot be
# injected into a DEPLOYED server, and those helpers import app code that builds `Settings()`
# (needs SECRET_KEY, DATABASE_URL, ...) which the remote gate runner does not have. So under
# E2E_REMOTE it is SKIPPED, visibly, BEFORE the helper imports: collection never builds
# Settings, and the staging gate does not claim to have verified this journey. It runs in
# the local/CI e2e job (scripts/e2e_run.sh), where the server is in-process-compatible.
if os.environ.get("E2E_REMOTE", "").strip().lower() in {"1", "true", "yes"}:
    pytest.skip(
        "needs an in-process canned LLM; not runnable against a deployed server",
        allow_module_level=True,
    )

from tests.deck_helpers import GOOD_SLIDES, CannedLLM, deck_pdf

R = "/api/v1/readiness"
G = "readiness_deck"


def _onboard(c: httpx.Client, auth: dict) -> None:
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada Obi"})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Placeholder Co"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "launch"},
    )
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 4, "goals": ["Raise funding"]})
    c.post("/api/v1/onboarding/complete", headers=auth)


def _join(c: httpx.Client, wh: dict, role: str, unique_email, mailbox) -> dict:
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
    auth = {"Authorization": f"Bearer {access}"}
    acc = c.post("/api/v1/invitations/accept", headers=auth, json={"token": token})
    assert acc.status_code == 200, acc.text
    return {**auth, "X-Workspace-Id": wh["X-Workspace-Id"]}


def _upload(c: httpx.Client, wh: dict, name: str, body: bytes, ctype="application/pdf") -> str:
    r = c.post(
        "/api/v1/documents/files",
        headers=wh,
        files={"file": (name, io.BytesIO(body), ctype)},
        data={"folder": "Pitch Deck"},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _drain(canned: CannedLLM) -> None:
    """Run the queued jobs here, as the worker process would, with the language model faked."""
    from app.db.session import SessionLocal
    from app.platform import llm_budget
    from app.services.notifications.registry import register as register_notifications
    from app.services.readiness.triggers import register as register_readiness
    from app.worker import runner
    from app.worker.handlers import readiness as _h  # noqa: F401  (registers the handler)

    register_notifications()
    register_readiness()
    llm_budget.get_llm_client = lambda: canned  # type: ignore[assignment]
    db = SessionLocal()
    try:
        for _ in range(100):
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


@pytest.fixture()
def canned_llm():
    from app.platform import llm_budget

    real = llm_budget.get_llm_client
    yield CannedLLM()
    llm_budget.get_llm_client = real


def _must(score: dict) -> dict:
    return next(m for m in score["must_haves"] if m["key"] == "pitch_deck_reviewed")


def test_deck_review_journey(
    base_url, make_verified_user, capture, unique_email, mailbox, canned_llm
):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        u = make_verified_user(c)
        token = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        _onboard(c, auth)
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wh = {**auth, "X-Workspace-Id": me["active_workspace_id"]}

        # 1. Before any review: empty list, the must-have is unmet and says what to do.
        empty = c.get(f"{R}/deck-reviews", headers=wh)
        assert empty.status_code == 200 and empty.json()["data"]["reviews"] == []
        capture(G, "list_empty", empty)
        before = c.get(f"{R}/score", headers=wh).json()["data"]
        assert _must(before)["met"] is False
        capture(G, "must_have_before", c.get(f"{R}/score", headers=wh))

        # 2. Upload through Documents, queue the review.
        pdf1 = deck_pdf(GOOD_SLIDES)
        file1 = _upload(c, wh, "Acme Pay seed deck.pdf", pdf1)
        queued = c.post(f"{R}/deck-reviews", headers=wh, json={"file_id": file1})
        assert queued.status_code == 202, queued.text
        q = queued.json()["data"]
        assert q["deduplicated"] is False and q["review"]["status"] == "queued"
        assert q["review"]["version"] == 1 and q["review"]["scores"] is None
        capture(G, "create_202", queued)
        rid, job_id = q["review"]["id"], q["review"]["job_id"]
        job = c.get(f"/api/v1/jobs/{job_id}", headers=wh)
        assert job.json()["data"]["status"] == "queued"
        capture(G, "job_queued", job)

        # 3. The same bytes again: 200, same review, nothing queued.
        dup = c.post(f"{R}/deck-reviews", headers=wh, json={"file_id": file1})
        assert dup.status_code == 200, dup.text
        assert dup.json()["data"]["deduplicated"] is True
        assert dup.json()["data"]["review"]["id"] == rid
        capture(G, "create_deduplicated_200", dup)

        # 4. The worker runs it.
        _drain(canned_llm)
        done = c.get(f"/api/v1/jobs/{job_id}", headers=wh)
        assert done.json()["data"]["status"] == "succeeded", done.text
        capture(G, "job_completed", done)
        detail = c.get(f"{R}/deck-reviews/{rid}", headers=wh)
        assert detail.status_code == 200, detail.text
        d = detail.json()["data"]
        assert d["status"] == "ready" and d["scores"]["narrative"] == 7
        assert d["scores"]["layout"]["slides"] == 8 and len(d["slides"]) == 8
        assert len(d["top_fixes"]) == 5
        capture(G, "review_ready", detail)
        thumb = d["slides"][0]["thumbnail_url"]
        img = c.get(thumb[thumb.index("/api/v1/") :])
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        assert img.content[:4] == b"\x89PNG"
        bad_thumb = c.get(f"{R}/deck-thumbnails", params={"token": "not-a-token"})
        assert bad_thumb.status_code == 404
        capture(G, "thumbnail_bad_token_404", bad_thumb)

        # 5. The score reacts: must-have met, Story lifted, history log has the review.
        after = c.get(f"{R}/score", headers=wh)
        a = after.json()["data"]
        assert _must(a)["met"] is True and _must(a)["reason"] is None
        story_before = next(x for x in before["dimensions"] if x["key"] == "story")["score"]
        story_after = next(x for x in a["dimensions"] if x["key"] == "story")["score"]
        assert story_after > story_before
        capture(G, "must_have_after", after)
        hist = c.get(f"{R}/history", params={"range": 30}, headers=wh)
        assert hist.json()["data"]["log"][0]["type"] == "deck_review"
        capture(G, "history_log", hist)

        # 6. A new version of the deck: review number 2, newest first.
        pdf2 = deck_pdf(GOOD_SLIDES + ["Appendix\nUnit economics by cohort"])
        file2 = _upload(c, wh, "Acme Pay seed deck v2.pdf", pdf2)
        v2 = c.post(f"{R}/deck-reviews", headers=wh, json={"file_id": file2})
        assert v2.status_code == 202 and v2.json()["data"]["review"]["version"] == 2
        _drain(canned_llm)
        lst = c.get(f"{R}/deck-reviews", headers=wh)
        versions = [r["version"] for r in lst.json()["data"]["reviews"]]
        assert versions == [2, 1]
        capture(G, "list_two_versions", lst)

        # 7. Failure paths. A deck with no extractable text fails honestly after the job runs.
        blank = _upload(c, wh, "scan.pdf", deck_pdf(["", ""]))
        qb = c.post(f"{R}/deck-reviews", headers=wh, json={"file_id": blank})
        assert qb.status_code == 202
        _drain(canned_llm)
        failed = c.get(f"{R}/deck-reviews/{qb.json()['data']['review']['id']}", headers=wh)
        assert failed.json()["data"]["status"] == "failed"
        assert failed.json()["data"]["error"] == "no_text"
        capture(G, "review_failed_no_text", failed)
        failed_job = c.get(f"/api/v1/jobs/{qb.json()['data']['review']['job_id']}", headers=wh)
        capture(G, "job_failed_no_text", failed_job)

        txt = _upload(c, wh, "notes.txt", b"just some notes", ctype="text/plain")
        not_pdf = c.post(f"{R}/deck-reviews", headers=wh, json={"file_id": txt})
        assert not_pdf.status_code == 422, not_pdf.text
        capture(G, "create_not_a_pdf_422", not_pdf)
        missing = c.post(
            f"{R}/deck-reviews",
            headers=wh,
            json={"file_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert missing.status_code == 404, missing.text
        capture(G, "create_file_not_found_404", missing)
        invalid = c.post(f"{R}/deck-reviews", headers=wh, json={"file_id": "nope"})
        assert invalid.status_code == 422, invalid.text
        capture(G, "create_invalid_body_422", invalid)
        unknown = c.get(f"{R}/deck-reviews/00000000-0000-0000-0000-000000000000", headers=wh)
        assert unknown.status_code == 404
        capture(G, "review_not_found_404", unknown)

        # 8. Roles: a team member reads reviews but cannot start one; a mentor sees nothing.
        tm = _join(c, wh, "team_member", unique_email, mailbox)
        assert c.get(f"{R}/deck-reviews/{rid}", headers=tm).status_code == 200
        tm_post = c.post(f"{R}/deck-reviews", headers=tm, json={"file_id": file1})
        assert tm_post.status_code == 403, tm_post.text
        capture(G, "team_member_create_403", tm_post)
        mentor = _join(c, wh, "mentor", unique_email, mailbox)
        mentor_get = c.get(f"{R}/deck-reviews", headers=mentor)
        assert mentor_get.status_code == 403, mentor_get.text
        capture(G, "mentor_list_403", mentor_get)

        # 9. Someone else's workspace cannot read this review.
        stranger = make_verified_user(c)
        stoken = c.post("/api/v1/auth/login", json=stranger).json()["data"]["access_token"]
        sauth = {"Authorization": f"Bearer {stoken}"}
        _onboard(c, sauth)
        sme = c.get("/api/v1/auth/me", headers=sauth).json()["data"]
        swh = {**sauth, "X-Workspace-Id": sme["active_workspace_id"]}
        cross = c.get(f"{R}/deck-reviews/{rid}", headers=swh)
        assert cross.status_code == 404, cross.text
        capture(G, "other_workspace_review_404", cross)
