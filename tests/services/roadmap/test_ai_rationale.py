from app.services.roadmap.ai_rationale import build_roadmap_rationale_messages


def test_builder_is_pii_free_and_lists_changes():
    changes = [
        {
            "milestone_id": "x",
            "title": "Validate demand",
            "old_due": "2026-08-17",
            "new_due": "2026-09-03",
            "reason": "10 days overdue and not yet done.",
        },
    ]
    msgs = build_roadmap_rationale_messages(
        stage="idea",
        name="Cofoundaz",
        industry="Fintech",
        changes=changes,
    )
    assert [m.role for m in msgs] == ["system", "user"]
    body = msgs[1].content
    assert "Validate demand" in body
    assert "2026-08-17" in body and "2026-09-03" in body
    assert "Cofoundaz" in body and "Fintech" in body and "idea" in body


def test_builder_handles_empty_changes():
    msgs = build_roadmap_rationale_messages(stage=None, name=None, industry=None, changes=[])
    assert len(msgs) == 2  # no crash on empty
