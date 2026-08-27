from datetime import date

import pytest
import sqlalchemy

from app.db.models.enums import MissionStatus, MissionTaskStatus
from app.db.models.mission import MissionTask
from tests.factories import (
    create_mission,
    create_mission_settings,
    create_mission_task,
    create_startup,
    create_user,
)


def test_mission_tree_and_settings(db):
    s = create_startup(db, owner=create_user(db))
    m = create_mission(db, s, mission_date=date.today())
    t = create_mission_task(db, m, title="Interviews")
    st = create_mission_settings(db, s, mission_size=2)
    assert m.startup_id == s.id and m.status == MissionStatus.pending
    assert t.mission_id == m.id and t.status == MissionTaskStatus.todo
    assert t.roadmap_task_id is None
    assert st.mission_size == 2
    db.delete(m)
    db.flush()
    assert db.query(MissionTask).count() == 0  # cascade


def test_one_mission_per_day(db):
    s = create_startup(db, owner=create_user(db))
    create_mission(db, s, mission_date=date.today())
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        create_mission(db, s, mission_date=date.today())
        db.flush()
