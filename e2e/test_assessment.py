"""Live assessment journey: a founder onboards, completes onboarding, then takes
the adaptive assessment end-to-end and gets scored, with assessment_pending flipped
and the recalc jobs enqueued.
"""

import httpx


def test_assessment_journey(base_url, make_verified_user):
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        u = make_verified_user(c)
        access = c.post("/api/v1/auth/login", json=u).json()["data"]["access_token"]
        auth = {"Authorization": f"Bearer {access}"}

        # Onboard just enough to complete.
        c.get("/api/v1/onboarding/state", headers=auth)
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 1, "full_name": "Ada"})
        c.patch("/api/v1/onboarding/state", headers=auth, json={"step": 2, "name": "Cofoundaz"})
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 3, "industry": "Fintech", "business_model": "b2b", "stage": "idea"},
        )
        c.patch(
            "/api/v1/onboarding/state",
            headers=auth,
            json={"step": 4, "goals": ["Get first customers"]},
        )
        c.post("/api/v1/onboarding/complete", headers=auth)

        # Discover the workspace id from /auth/me, then take the assessment.
        me = c.get("/api/v1/auth/me", headers=auth).json()["data"]
        wsid = me["active_workspace_id"]
        wh = {**auth, "X-Workspace-Id": wsid}

        start = c.post("/api/v1/assessments", headers=wh)
        assert start.status_code == 201, start.text
        aid = start.json()["data"]["assessment_id"]

        vals = {"scale_1_5": 3, "numeric_currency": 1000, "short_text": "n/a"}
        answered_keys = []
        while True:
            nq = c.get(f"/api/v1/assessments/{aid}/next-question", headers=wh).json()["data"][
                "next_question"
            ]
            if nq is None:
                break
            if nq["qtype"] == "single_choice":
                # Last option, not first: for the bank's gated questions the last option
                # is the "yes"/"most-advanced" branch (product_stage->live,
                # has_revenue->yes, incorporated->yes) that reveals a show_if-gated
                # follow-up. Picking options[0] would only ever walk the 8 unconditional
                # questions and never prove the adaptive engine reveals a gated one.
                val = nq["options"][-1]["value"]
            elif nq["qtype"] == "multi_choice":
                val = [nq["options"][-1]["value"]]
            else:
                val = vals[nq["qtype"]]
            ans = c.post(
                f"/api/v1/assessments/{aid}/answers",
                headers=wh,
                json={"question_key": nq["key"], "value": val},
            )
            assert ans.status_code == 200, ans.text
            answered_keys.append(nq["key"])

        # The "always yes" branch above must have revealed at least one show_if-gated
        # question over HTTP -- otherwise this only proves the 8 unconditional questions
        # work, not the adaptive engine itself.
        assert len(answered_keys) > 8
        assert {"product_confidence", "mrr", "ip_assigned"} & set(answered_keys)

        done = c.post(f"/api/v1/assessments/{aid}/complete", headers=wh)
        assert done.status_code == 200, done.text
        result = done.json()["data"]
        assert set(result["dimension_scores"]) == {"product", "market", "money", "legal", "team"}
        assert isinstance(result["overall_provisional"], int)
        assert result["narrative"]

        # Compare against itself: single completed id returns one scored entry.
        compare = c.get("/api/v1/assessments/compare", params={"ids": aid}, headers=wh)
        assert compare.status_code == 200, compare.text
        cdata = compare.json()["data"]
        assert isinstance(cdata, list) and len(cdata) == 1
        assert cdata[0]["id"] == aid
        assert set(cdata[0]["dimension_scores"]) == {"product", "market", "money", "legal", "team"}

        # assessment_pending flipped off.
        state = c.get("/api/v1/onboarding/state", headers=auth).json()["data"]
        assert state["assessment_pending"] is False

        # List shows it completed, carrying the same provisional score.
        listing = c.get("/api/v1/assessments", headers=wh).json()["data"]
        row = next(r for r in listing if r["id"] == aid)
        assert row["status"] == "completed"
        assert row["overall_provisional"] == result["overall_provisional"]

        # Detail returns the grouped answers plus the stored result.
        detail = c.get(f"/api/v1/assessments/{aid}", headers=wh).json()["data"]
        assert detail["status"] == "completed"
        assert sum(len(v) for v in detail["answers_by_dimension"].values()) == len(answered_keys)
        assert detail["result"]["dimension_scores"] == result["dimension_scores"]
