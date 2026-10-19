"""Live Validation Hub PRD slice (c) journey: the MVP feedback inbox (an interview quote and an open
survey answer mirrored in, manual entries tagged in-app / email), editing and dismissing, AI theme
clustering ("{theme} mentioned {n}x across {m} sources"), Send to roadmap (and the 409 on a second
send), the synthesizer's `feedback` source, open survey answers, share-link rotation and linked
experiments on assumption cards.

Every body is captured to `e2e/_captures/validation_prd_c/*.json`, the verbatim source for
`docs/fe-integration-guide-validation-prd-c.md`. AI jobs drain in-process with the stub model, so
AI text reads "[stub-llm] ..."; the `_real_model` captures come from the same calls with the real
model.
"""

import httpx
from test_validation_prd_b import V, _drain_ai, _onboard

G = "validation_prd_c"


def test_validation_prd_c_journey(base_url, make_verified_user, capture):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {access}"}
        _onboard(c, auth)
        ws = c.get("/api/v1/auth/me", headers=auth).json()["data"]["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": ws}
        c.post("/api/v1/roadmap/generate", headers=wh)

        # 0. Nothing yet.
        empty = c.post(f"{V}/feedback/cluster", headers=wh)
        assert empty.status_code == 422, empty.text
        capture(G, "cluster_nothing_422", empty)
        capture(G, "themes_none", c.get(f"{V}/feedback/themes", headers=wh))

        # 1. Sources: an interview quote, an open survey answer, two manual entries.
        pay = c.post(
            f"{V}/assumptions",
            headers=wh,
            json={"statement": "Traders will pay 0.5% for same-day settlement", "risk": "high"},
        ).json()["data"]
        c.post(
            f"{V}/interviews",
            headers=wh,
            json={
                "interviewee": "Placeholder Name",
                "segment": "Market traders",
                "held_on": "2026-10-15",
                "verdict": "supports",
                "key_quotes": ["I wait three days for my money"],
                "assumption_ids": [pay["id"]],
            },
        )
        survey = c.post(
            f"{V}/surveys",
            headers=wh,
            json={
                "title": "Payouts",
                "questions": [{"type": "open", "prompt": "What frustrates you about payouts?"}],
            },
        ).json()["data"]
        opened = c.patch(f"{V}/surveys/{survey['id']}", headers=wh, json={"status": "open"})
        token = opened.json()["data"]["public_token"]
        qid = opened.json()["data"]["survey"]["questions"][0]["id"]
        c.post(f"{V}/surveys/{token}/responses", json={"answers": {qid: "Payouts take forever"}})

        manual = c.post(
            f"{V}/feedback",
            headers=wh,
            json={"text": "The app froze when I tried to withdraw", "source": "in_app"},
        )
        assert manual.status_code == 201, manual.text
        capture(G, "feedback_created", manual)
        email = c.post(
            f"{V}/feedback",
            headers=wh,
            json={"text": "Payout came late again", "source": "email", "received_on": "2026-10-18"},
        ).json()["data"]
        bad = c.post(f"{V}/feedback", headers=wh, json={"text": "x", "source": "survey"})
        assert bad.status_code == 422
        capture(G, "feedback_bad_source_422", bad)

        inbox = c.get(f"{V}/feedback", headers=wh)
        assert inbox.status_code == 200, inbox.text
        items = inbox.json()["data"]["items"]
        assert len(items) == 4
        assert "Placeholder Name" not in inbox.text
        capture(G, "feedback_inbox", inbox)
        capture(G, "feedback_inbox_email", c.get(f"{V}/feedback?source=email", headers=wh))

        edited = c.patch(
            f"{V}/feedback/{manual.json()['data']['id']}",
            headers=wh,
            json={"text": "The app froze at withdrawal", "source": "in_app"},
        )
        assert edited.status_code == 200, edited.text
        capture(G, "feedback_edited", edited)
        imported = next(i for i in items if i["imported"])
        locked = c.patch(f"{V}/feedback/{imported['id']}", headers=wh, json={"text": "nope"})
        assert locked.status_code == 422
        capture(G, "feedback_imported_edit_422", locked)
        dismissed = c.delete(f"{V}/feedback/{imported['id']}", headers=wh)
        assert dismissed.json()["data"] == {"dismissed": True}
        capture(G, "feedback_dismissed", dismissed)
        restored = c.post(f"{V}/feedback/{imported['id']}/restore", headers=wh)
        assert restored.json()["data"]["dismissed"] is False
        capture(G, "feedback_restored", restored)

        # 2. Cluster into themes.
        q = c.post(f"{V}/feedback/cluster", headers=wh)
        assert q.status_code == 202, q.text
        capture(G, "cluster_queued", q)
        _drain_ai()
        job = c.get(f"/api/v1/jobs/{q.json()['data']['job_id']}", headers=wh)
        assert job.json()["data"]["result"]["status"] == "ready", job.text
        capture(G, "cluster_job", job)
        themes = c.get(f"{V}/feedback/themes", headers=wh)
        data = themes.json()["data"]
        assert data["run"]["item_count"] == 4 and data["themes"], themes.text
        capture(G, "themes", themes)

        # 3. Send to roadmap, once.
        theme = data["themes"][0]
        url = f"{V}/feedback/themes/{theme['id']}/send-to-roadmap"
        sent = c.post(url, headers=wh)
        assert sent.status_code == 201, sent.text
        capture(G, "theme_sent_to_roadmap", sent)
        task_id = sent.json()["data"]["task"]["id"]
        tree = c.get("/api/v1/roadmap", headers=wh).json()["data"]
        tasks = [t for p in tree["phases"] for m in p["milestones"] for t in m["tasks"]]
        assert any(t["id"] == task_id for t in tasks)
        again = c.post(url, headers=wh)
        assert again.status_code == 409
        capture(G, "theme_sent_again_409", again)

        # 4. The synthesizer can read manual feedback.
        sq = c.post(f"{V}/synthesize", headers=wh, json={"source": "feedback"})
        assert sq.status_code == 202, sq.text
        _drain_ai()
        sjob = c.get(f"/api/v1/jobs/{sq.json()['data']['job_id']}", headers=wh).json()["data"]
        report = c.get(f"{V}/insights/{sjob['result']['insight_id']}", headers=wh)
        assert report.json()["data"]["inputs"] == {
            "interviews": 0,
            "open_answers": 0,
            "feedback": 2,
        }
        capture(G, "insight_from_feedback", report)

        # 5. Row 9: open answers and a fresh share link.
        answers = c.get(f"{V}/surveys/{survey['id']}/answers", headers=wh)
        assert answers.status_code == 200, answers.text
        capture(G, "survey_open_answers", answers)
        rotated = c.post(f"{V}/surveys/{survey['id']}/share-link", headers=wh)
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["data"]["public_token"]
        capture(G, "survey_share_link_rotated", rotated)
        old = c.get(f"{V}/surveys/{token}")
        assert old.status_code == 404
        capture(G, "survey_old_link_404", old)
        assert c.get(f"{V}/surveys/{new_token}").status_code == 200

        # 6. Row 10: linked experiments on assumption cards.
        c.post(
            f"{V}/experiments",
            headers=wh,
            json={"name": "Fee fake door", "type": "smoke_test", "assumption_ids": [pay["id"]]},
        )
        capture(G, "assumptions_with_experiments", c.get(f"{V}/assumptions", headers=wh))

        # 7. Delete a manual entry.
        capture(G, "feedback_deleted", c.delete(f"{V}/feedback/{email['id']}", headers=wh))
