from app.services.assessment.narrative import build_narrative_messages


def test_build_narrative_messages_includes_context_and_no_pii():
    msgs = build_narrative_messages(
        dimension_scores={"team": 70, "market": 40},
        overall=55,
        industry="Fintech",
        stage="validation",
    )
    assert [m.role for m in msgs] == ["system", "user"]
    user = msgs[1].content
    assert "Fintech" in user and "validation" in user
    assert "55" in user and "team" in user and "market" in user
    # data minimization: the builder has no parameter for names/emails, and emits none
    blob = " ".join(m.content for m in msgs).lower()
    assert "@" not in blob


def test_build_narrative_messages_tolerates_missing_profile():
    msgs = build_narrative_messages(
        dimension_scores={"team": 10}, overall=10, industry=None, stage=None
    )
    assert "unspecified" in msgs[1].content
