from datetime import UTC, datetime

from app.db.models.roadmap import RoadmapReplan
from tests.factories import (
    create_milestone,
    create_phase,
    create_replan,
    create_roadmap,
    create_startup,
    create_user,
)


def test_replan_and_markers_persist(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    r = create_replan(
        db,
        roadmap,
        change_count=2,
        changes=[
            {
                "milestone_id": "x",
                "title": "M",
                "old_due": "2026-01-01",
                "new_due": "2026-01-08",
                "reason": "…",
            }
        ],
        summary="Re-planned 2 milestones",
    )
    db.flush()
    assert r.roadmap_id == roadmap.id
    assert r.change_count == 2
    assert r.changes[0]["new_due"] == "2026-01-08"

    m = create_milestone(db, create_phase(db, roadmap))
    m.last_replanned_at = datetime.now(UTC)
    m.last_replan_reason = "Shifts 7 days with its dependency 'X'."
    db.flush()
    db.refresh(m)
    assert m.last_replan_reason.startswith("Shifts")

    db.delete(roadmap)
    db.flush()
    assert db.query(RoadmapReplan).count() == 0  # cascade
