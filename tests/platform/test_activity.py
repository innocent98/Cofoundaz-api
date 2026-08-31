import uuid

from app.db.models.activity import ActivityLog
from app.platform.activity import write_activity
from tests.factories import create_startup, create_user


def test_write_activity_persists_row_in_caller_transaction(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)

    row = write_activity(
        db,
        startup_id=startup.id,
        actor_user_id=owner.id,
        action="mission.task.completed",
        summary="Ada completed 'Draft pricing options'",
        entity_type="mission_task",
        entity_id=uuid.uuid4(),
        meta={"streak": 3},
    )

    assert row.id is not None
    fetched = db.query(ActivityLog).filter(ActivityLog.id == row.id).one()
    assert fetched.startup_id == startup.id
    assert fetched.actor_user_id == owner.id
    assert fetched.action == "mission.task.completed"
    assert fetched.summary == "Ada completed 'Draft pricing options'"
    assert fetched.entity_type == "mission_task"
    assert fetched.meta == {"streak": 3}
    assert fetched.created_at is not None


def test_write_activity_allows_null_actor_for_system_rows(db):
    owner = create_user(db)
    startup = create_startup(db, owner=owner)
    row = write_activity(
        db, startup_id=startup.id, action="mission.generated", summary="Today's mission is ready"
    )
    assert row.actor_user_id is None
    assert row.entity_id is None
    assert row.meta is None
