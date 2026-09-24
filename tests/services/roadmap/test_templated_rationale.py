from app.services.roadmap.replan import _templated_rationale


def test_templated_rationale_empty_snapshot():
    """Empty snapshot returns only the summary."""
    summary = "Re-planned 1 milestone"
    result = _templated_rationale(summary, [])
    assert result == summary


def test_templated_rationale_three_or_fewer():
    """Snapshot with ≤3 items lists all titles, no 'more' suffix."""
    summary = "Re-planned 2 milestones"
    snapshot = [
        {"title": "M1", "milestone_id": "abc"},
        {"title": "M2", "milestone_id": "def"},
    ]
    result = _templated_rationale(summary, snapshot)
    assert "M1, M2" in result
    assert "more" not in result


def test_templated_rationale_more_than_three():
    """Snapshot with >3 items lists first 3 titles plus 'and N more'."""
    summary = "Re-planned 4 milestones"
    snapshot = [
        {"title": "M1", "milestone_id": "id1"},
        {"title": "M2", "milestone_id": "id2"},
        {"title": "M3", "milestone_id": "id3"},
        {"title": "M4", "milestone_id": "id4"},
    ]
    result = _templated_rationale(summary, snapshot)
    assert "M1, M2, M3" in result
    assert "and 1 more" in result
    assert "M4" not in result  # M4 should not be listed explicitly


def test_templated_rationale_five_items():
    """Snapshot with 5 items lists first 3 plus 'and 2 more'."""
    summary = "Re-planned 5 milestones"
    snapshot = [
        {"title": "Alpha", "milestone_id": "id1"},
        {"title": "Beta", "milestone_id": "id2"},
        {"title": "Gamma", "milestone_id": "id3"},
        {"title": "Delta", "milestone_id": "id4"},
        {"title": "Epsilon", "milestone_id": "id5"},
    ]
    result = _templated_rationale(summary, snapshot)
    assert "Alpha, Beta, Gamma" in result
    assert "and 2 more" in result
    assert "Delta" not in result
    assert "Epsilon" not in result
