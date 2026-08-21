from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import RoadmapStatus, TaskEffort
from app.db.models.roadmap import RoadmapPhase, RoadmapTask
from tests.factories import (
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def test_roadmap_tree_persists_and_cascades(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap, name="Validation", order=0)
    milestone = create_milestone(db, phase, title="Validate demand", due_on=date(2026, 9, 1))
    task = create_task(db, milestone, title="Interviews", effort=TaskEffort.medium)

    assert roadmap.startup_id == startup.id
    assert roadmap.template_version == 1
    assert phase.roadmap_id == roadmap.id
    assert milestone.phase_id == phase.id
    assert milestone.status == RoadmapStatus.todo
    assert milestone.progress == 0
    assert task.milestone_id == milestone.id
    assert task.effort == TaskEffort.medium

    # deleting the roadmap cascades to phases -> milestones -> tasks
    db.delete(roadmap)
    db.flush()

    assert db.query(RoadmapPhase).count() == 0
    assert db.query(RoadmapTask).count() == 0


def test_one_roadmap_per_startup(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    create_roadmap(db, startup)
    db.flush()
    with pytest.raises(IntegrityError):
        create_roadmap(db, startup)
