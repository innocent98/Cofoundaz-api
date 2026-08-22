from app.services.roadmap.dependencies import add_dependency, dependency_map, would_create_cycle
from tests.factories import (
    create_milestone,
    create_phase,
    create_roadmap,
    create_startup,
    create_task,
    create_user,
)


def _roadmap_with_tasks(db, n=4):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    phase = create_phase(db, roadmap)
    ms = create_milestone(db, phase)
    tasks = [create_task(db, ms, title=f"T{i}") for i in range(n)]
    return roadmap, tasks


def test_direct_cycle_detected(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[0].id, t[1].id)  # T0 depends on T1
    db.flush()
    # adding T1 depends on T0 would loop
    assert would_create_cycle(db, roadmap.id, t[1].id, t[0].id) is True


def test_transitive_cycle_detected(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[0].id, t[1].id)  # T0 -> T1
    add_dependency(db, t[1].id, t[2].id)  # T1 -> T2
    db.flush()
    assert would_create_cycle(db, roadmap.id, t[2].id, t[0].id) is True  # T2 -> T0 closes loop


def test_valid_dag_no_cycle(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[1].id, t[0].id)  # diamond: B->A
    add_dependency(db, t[2].id, t[0].id)  # C->A
    db.flush()
    assert would_create_cycle(db, roadmap.id, t[3].id, t[1].id) is False  # D->B ok
    assert would_create_cycle(db, roadmap.id, t[3].id, t[2].id) is False  # D->C ok


def test_add_dependency_idempotent(db):
    roadmap, t = _roadmap_with_tasks(db)
    row1, created1 = add_dependency(db, t[0].id, t[1].id)
    row2, created2 = add_dependency(db, t[0].id, t[1].id)
    db.flush()
    assert created1 is True and created2 is False


def test_dependency_map(db):
    roadmap, t = _roadmap_with_tasks(db)
    add_dependency(db, t[0].id, t[1].id)
    add_dependency(db, t[0].id, t[2].id)
    db.flush()
    m = dependency_map(db, roadmap.id)
    assert set(m[t[0].id]) == {t[1].id, t[2].id}
