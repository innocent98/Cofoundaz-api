"""Live Learning Academy journey (Module 17), against the real content in `content/academy/`.

A founder onboards at the validation stage and walks the whole Academy: the Learn next shelf with
its hero reason (templated, then rewritten by the worker), the catalog and a course page, enrolment
(201 then 200), a saved playback position, private notes, finishing all three lessons (out of
order) for a certificate, the PDF (409 until the worker renders it, then a real PDF), the owner-only
rule against a second founder, the public verify route (and its rate limit), paths, articles
(search, tag, reader with related items), the notification and the global search.

Every response body is captured to `e2e/_captures/learning/*.json` -- those files are the verbatim
source for `docs/fe-integration-guide-academy.md`. The certificate PDF itself is saved beside them
as `certificate.pdf`.

The worker is drained in-process with `LLM_PROVIDER=stub` (same pattern as
e2e/test_dashboard_ai_briefing.py): `learning.certificate.generate` renders the PDF and
`ai.learning.recommendations` rewrites the hero reason.
"""

import httpx

COURSE = "build-scope-the-mvp"
LESSONS = [f"{COURSE}-{n}" for n in (1, 2, 3)]
HERO_FIRST = "validation-talk-to-customers"


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _drain() -> None:
    from app.db.session import SessionLocal
    from app.worker import runner
    from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.learning.recommendations)
    from app.worker.handlers import learning as _learning  # noqa: F401  (certificate PDF)

    db = SessionLocal()
    try:
        for _ in range(500):  # generous cap -- a real stall still fails loudly
            if runner.run_once(db) == 0:
                break
    finally:
        db.close()


def _onboard(c: httpx.Client, make_verified_user, *, full_name: str) -> tuple[dict, dict]:
    """A verified founder at the validation stage; returns (login creds, workspace headers)."""
    u = make_verified_user(c)
    access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
    auth = _auth_header(access)
    c.get("/api/v1/onboarding/state", headers=auth)
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": full_name})
    c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "validation"},
    )
    c.patch(
        "/api/v1/onboarding/state",
        headers=auth,
        json={"step": 4, "goals": ["Get first customers"]},
    )
    done = c.post("/api/v1/onboarding/complete", headers=auth)
    assert done.status_code == 200, done.text
    me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
    return u, {**auth, "X-Workspace-Id": me["active_workspace_id"]}


def test_learning_journey(base_url, make_verified_user, capture, capture_json):
    from pathlib import Path

    out_dir = Path(__file__).parent / "_captures" / "learning"
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        _u, wh = _onboard(c, make_verified_user, full_name="Ada Founder")

        # 1. Learn next: a hero course with its own reason, templated until the worker runs.
        recs = c.get("/api/v1/learning/recommendations", headers=wh)
        assert recs.status_code == 200, recs.text
        data = recs.json()["data"]
        assert data["stage"] == "validation"
        assert data["hero"]["course_id"] == data["recommended"][0]["id"] == HERO_FIRST
        assert data["hero"]["reason_source"] == "template"
        assert data["hero"]["matched_gaps"] == ["customers"]
        assert data["continue_watching"] == []
        capture("learning", "recommendations_before", recs)

        # 1b. The worker rewrites the hero's reason (stub LLM) and the read shows it as AI.
        _drain()
        upgraded = c.get("/api/v1/learning/recommendations", headers=wh)
        assert upgraded.status_code == 200, upgraded.text
        hero = upgraded.json()["data"]["hero"]
        assert hero["reason_source"] == "ai" and "[stub-llm]" in hero["reason"]
        capture("learning", "recommendations_reason", upgraded)

        # 2. The catalog and one course page. Every item is reviewed (Adebayo, 2026-10-25).
        courses = c.get("/api/v1/learning/courses", headers=wh)
        assert courses.status_code == 200, courses.text
        assert len(courses.json()["data"]["courses"]) == 8
        assert {x["review_status"] for x in courses.json()["data"]["courses"]} == {"reviewed"}
        capture("learning", "courses", courses)
        detail = c.get(f"/api/v1/learning/courses/{COURSE}", headers=wh)
        assert detail.status_code == 200, detail.text
        lessons = detail.json()["data"]["lessons"]
        assert [x["id"] for x in lessons] == LESSONS
        assert all(x["video"] is None for x in lessons)
        assert "[Placeholder]" not in detail.text and "placeholder://" not in detail.text
        capture("learning", "course_detail", detail)
        missing = c.get("/api/v1/learning/courses/nope", headers=wh)
        assert missing.status_code == 404
        capture("learning", "course_not_found", missing)

        # 3. Enrol: 201 the first time, 200 on a repeat.
        enrol = c.post("/api/v1/learning/enrollments", headers=wh, json={"course_id": COURSE})
        assert enrol.status_code == 201, enrol.text
        capture("learning", "enrollment_created", enrol)
        again = c.post("/api/v1/learning/enrollments", headers=wh, json={"course_id": COURSE})
        assert again.status_code == 200, again.text
        capture("learning", "enrollment_repeat", again)
        unknown_course = c.post("/api/v1/learning/enrollments", headers=wh, json={"course_id": "nope"})
        assert unknown_course.status_code == 404
        capture("learning", "enrollment_unknown_course", unknown_course)
        no_field = c.post("/api/v1/learning/enrollments", headers=wh, json={})
        assert no_field.status_code == 422
        capture("learning", "enrollment_invalid", no_field)

        # 4. Save where the learner paused, then read it back from the course.
        paused = c.patch(
            f"/api/v1/learning/lessons/{LESSONS[1]}/progress", headers=wh, json={"position_s": 95}
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["data"]["position_s"] == 95 and paused.json()["data"]["progress"] == 0
        capture("learning", "progress_position", paused)
        resumed = c.get(f"/api/v1/learning/courses/{COURSE}", headers=wh).json()["data"]
        assert resumed["next_lesson_id"] == LESSONS[1] and resumed["position_s"] == 95
        bad = c.patch(f"/api/v1/learning/lessons/{LESSONS[0]}/progress", headers=wh, json={})
        assert bad.status_code == 422
        capture("learning", "progress_invalid", bad)
        uncomplete = c.patch(
            f"/api/v1/learning/lessons/{LESSONS[0]}/progress", headers=wh, json={"completed": False}
        )
        assert uncomplete.status_code == 422

        watching = c.get("/api/v1/learning/recommendations", headers=wh).json()["data"]
        assert [x["id"] for x in watching["continue_watching"]] == [COURSE]

        # 5. Private notes: empty, saved, read back.
        note_url = f"/api/v1/learning/lessons/{LESSONS[1]}/notes"
        empty = c.get(note_url, headers=wh)
        assert empty.status_code == 200 and empty.json()["data"]["body"] == ""
        capture("learning", "note_empty", empty)
        saved = c.put(note_url, headers=wh, json={"body": "Ask the pilot customers about WhatsApp."})
        assert saved.status_code == 200, saved.text
        capture("learning", "note_saved", saved)
        assert c.get(note_url, headers=wh).json()["data"]["body"].startswith("Ask the pilot")
        too_long = c.put(note_url, headers=wh, json={"body": "x" * 20_001})
        assert too_long.status_code == 422
        capture("learning", "note_too_long", too_long)

        # 6. Complete the lessons out of order: 2, then 1, then 3 (the last issues the certificate).
        second = c.patch(
            f"/api/v1/learning/lessons/{LESSONS[1]}/progress", headers=wh, json={"completed": True}
        )
        assert second.status_code == 200, second.text
        assert second.json()["data"]["progress"] == 33
        capture("learning", "lesson_completed", second)
        c.patch(
            f"/api/v1/learning/lessons/{LESSONS[0]}/progress", headers=wh, json={"completed": True}
        )
        last = c.patch(
            f"/api/v1/learning/lessons/{LESSONS[2]}/progress",
            headers=wh,
            json={"completed": True, "position_s": 480},
        )
        assert last.status_code == 200, last.text
        finished = last.json()["data"]
        assert finished["progress"] == 100 and finished["certificate"] is not None
        cert = finished["certificate"]
        assert cert["holder_name"] == "Ada Founder" and cert["pdf_ready"] is False
        assert cert["issuer"] == "Cofoundaz" and cert["credential_id"].startswith("CFZ-")
        assert "organizationId=146743971" in cert["linkedin_url"]
        capture("learning", "course_completed", last)
        repeat = c.patch(
            f"/api/v1/learning/lessons/{LESSONS[2]}/progress", headers=wh, json={"completed": True}
        )
        assert repeat.json()["data"]["certificate"] is None  # nothing issued twice
        capture("learning", "lesson_completed_repeat", repeat)

        # 7. The PDF is not there until the worker has rendered it.
        pdf_url = f"/api/v1/learning/certificates/{cert['id']}/pdf"
        early = c.get(pdf_url, headers=wh)
        assert early.status_code == 409, early.text
        assert early.json()["error"]["code"] == "CERTIFICATE_NOT_READY"
        capture("learning", "certificate_pdf_not_ready", early)
        pending = c.get("/api/v1/learning/certificates", headers=wh)
        assert pending.json()["data"]["certificates"][0]["pdf_ready"] is False
        capture("learning", "certificates_pdf_pending", pending)

        _drain()

        ready = c.get("/api/v1/learning/certificates", headers=wh)
        listed = ready.json()["data"]["certificates"]
        assert [x["id"] for x in listed] == [cert["id"]]
        assert listed[0]["pdf_ready"] is True and listed[0]["pdf_url"] == pdf_url
        capture("learning", "certificates", ready)
        pdf = c.get(pdf_url, headers=wh)
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF")
        capture_json(
            "learning",
            "certificate_pdf_headers",
            {
                "status": pdf.status_code,
                "content-type": pdf.headers["content-type"],
                "content-disposition": pdf.headers["content-disposition"],
                "cache-control": pdf.headers["cache-control"],
                "bytes": len(pdf.content),
            },
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "certificate.pdf").write_bytes(pdf.content)

        # 8. Owner only: a second founder (own workspace) cannot reach it, however the id is sent.
        _u2, other = _onboard(c, make_verified_user, full_name="Other Founder")
        stranger = c.get(pdf_url, headers=other)
        assert stranger.status_code == 404, stranger.text
        capture("learning", "certificate_pdf_other_user", stranger)
        cross = c.get(pdf_url, headers={**other, "X-Workspace-Id": wh["X-Workspace-Id"]})
        assert cross.status_code == 403, cross.text
        capture("learning", "certificate_pdf_other_workspace", cross)
        anon = c.get(pdf_url)
        assert anon.status_code == 401
        capture("learning", "unauthenticated", anon)
        assert (
            c.get("/api/v1/learning/certificates", headers=other).json()["data"]["certificates"]
            == []
        )

        # 9. Public verify: no login, the minimum fields, and nothing for a made-up code.
        code = cert["credential_id"]
        verify = c.get(f"/api/v1/verify/{code}")
        assert verify.status_code == 200, verify.text
        assert set(verify.json()["data"]) == {
            "valid", "holder_name", "course_title", "issued_on", "issuer"
        }
        assert verify.json()["data"]["holder_name"] == "Ada Founder"
        capture("learning", "verify_valid", verify)
        for leak in (cert["id"], "startup", "user_id", "email"):
            assert leak not in verify.text
        unknown = c.get("/api/v1/verify/CFZ-ABCD-EF23-GH45")
        assert unknown.status_code == 404, unknown.text
        capture("learning", "verify_unknown", unknown)
        malformed = c.get("/api/v1/verify/not-a-code")
        assert malformed.status_code == 404
        capture("learning", "verify_malformed", malformed)

        # 10. Paths, articles, related items.
        paths = c.get("/api/v1/learning/paths", headers=wh)
        assert paths.status_code == 200, paths.text
        by_id = {p["id"]: p for p in paths.json()["data"]["paths"]}
        assert by_id["path-validation-foundations"]["progress"] == 25  # (0 + 0 + 0 + 100) / 4
        capture("learning", "paths", paths)
        articles = c.get("/api/v1/learning/articles", headers=wh)
        assert len(articles.json()["data"]["articles"]) == 10
        capture("learning", "articles", articles)
        found = c.get("/api/v1/learning/articles?q=runway&tag=money", headers=wh)
        assert [a["id"] for a in found.json()["data"]["articles"]] == ["article-read-your-runway"]
        capture("learning", "articles_search", found)
        reader = c.get("/api/v1/learning/articles/article-pricing-basics", headers=wh)
        assert reader.status_code == 200, reader.text
        assert reader.json()["data"]["related"]["courses"]
        capture("learning", "article_detail", reader)

        # 11. After the course: off the shelf, a notification, and global search finds the Academy.
        after = c.get("/api/v1/learning/recommendations", headers=wh)
        after_data = after.json()["data"]
        assert COURSE not in [x["id"] for x in after_data["recommended"]]
        assert after_data["continue_watching"] == []
        capture("learning", "recommendations_after", after)
        inbox = c.get("/api/v1/notifications", headers=wh)
        types = [n["type"] for n in inbox.json()["data"]["notifications"]]
        assert "learning.course.completed" in types, inbox.text
        capture("learning", "notifications", inbox)
        search = c.get("/api/v1/search", headers=wh, params={"q": "runway"})
        assert search.status_code == 200, search.text
        assert any(r["type"] == "article" for r in search.json()["data"]["results"])
        capture("learning", "search", search)

        # 12. Last, because it fills this caller's window: the verify route is rate limited.
        statuses = [c.get("/api/v1/verify/CFZ-ABCD-EF23-GH45").status_code for _ in range(35)]
        assert 429 in statuses, statuses
        limited = c.get("/api/v1/verify/CFZ-ABCD-EF23-GH45")
        assert limited.status_code == 429
        capture("learning", "verify_rate_limited", limited)
