import pytest

from app.core.errors import AppError
from app.db.models.enums import RecordKind
from app.services.business.records import (
    create_record,
    delete_record,
    list_records,
    update_record,
    validate,
)
from app.services.business.service import overview
from tests.factories import create_startup, create_user


def _startup(db):
    u = create_user(db)
    return create_startup(db, owner=u)


def test_validate_rejects_bad_data(db):
    with pytest.raises(AppError) as e:
        validate(RecordKind.persona, {"goals": "not-a-list"})  # missing name + wrong type
    assert e.value.code == "VALIDATION_ERROR"
    assert e.value.http_status == 422


def test_create_appends_position_and_lists_ordered(db):
    s = _startup(db)
    a = create_record(db, s, RecordKind.persona, {"name": "A"})
    b = create_record(db, s, RecordKind.persona, {"name": "B"})
    assert a.position == 0 and b.position == 1
    rows = list_records(db, s, RecordKind.persona)
    assert [r.data["name"] for r in rows] == ["A", "B"]


def test_update_full_replaces_data(db):
    s = _startup(db)
    r = create_record(db, s, RecordKind.persona, {"name": "A", "quote": "hi"})
    update_record(db, r, {"name": "A2"})
    assert r.data == {
        "name": "A2",
        "demographics": "",
        "goals": [],
        "frustrations": [],
        "watering_holes": [],
        "quote": "",
    }  # quote reset -> full replace


def test_delete_removes(db):
    s = _startup(db)
    r = create_record(db, s, RecordKind.persona, {"name": "A"})
    delete_record(db, r)
    assert list_records(db, s, RecordKind.persona) == []


def test_overview_includes_record_rows(db):
    s = _startup(db)
    rows = {r["type"]: r for r in overview(db, s)}
    assert rows["persona"]["status"] == "start" and rows["persona"]["count"] == 0
    create_record(db, s, RecordKind.persona, {"name": "A"})
    rows = {r["type"]: r for r in overview(db, s)}
    assert rows["persona"]["status"] == "complete" and rows["persona"]["count"] == 1
    assert rows["persona"]["completion_pct"] == 100
