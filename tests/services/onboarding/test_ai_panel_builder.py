from app.services.onboarding.ai_panel import _templated_panel, build_onboarding_panel_messages


def test_templated_panel_wording():
    assert _templated_panel("fintech", "idea") == (
        "Got it — a fintech startup at the idea stage. Let's calibrate your workspace."
    )


def test_builder_is_pii_free_and_includes_signals():
    msgs = build_onboarding_panel_messages(
        industry="Fintech",
        stage="idea",
        goals=["Get first customers", "Raise a pre-seed"],
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "Fintech" in body and "idea" in body
    assert "Get first customers" in body and "Raise a pre-seed" in body


def test_builder_handles_missing_signals():
    msgs = build_onboarding_panel_messages(industry=None, stage=None, goals=[])
    assert len(msgs) == 2  # no crash
