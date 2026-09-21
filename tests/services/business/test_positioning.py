import pytest

from app.core.errors import AppError
from app.db.models.enums import RecordKind
from app.services.business.positioning import (
    assemble_map,
    get_or_create_map,
    update_axes,
)
from app.services.business.records import create_record, validate
from tests.factories import create_startup, create_user


def _startup(db):
    u = create_user(db)
    return create_startup(db, owner=u)


def test_competitor_accepts_coords(db):
    clean = validate(RecordKind.competitor, {"name": "Acme", "map_x": 0.25, "map_y": 0.8})
    assert clean["map_x"] == 0.25 and clean["map_y"] == 0.8


def test_competitor_rejects_out_of_range_coord(db):
    with pytest.raises(AppError) as e:
        validate(RecordKind.competitor, {"name": "Acme", "map_x": 1.5})
    assert e.value.http_status == 422


def test_get_or_create_map_returns_default_axes(db):
    s = _startup(db)
    row = get_or_create_map(db, s)
    assert row.axes["x"]["label"] == "Price" and row.axes["y"]["label"] == "Quality"
    # idempotent
    assert get_or_create_map(db, s).id == row.id


def test_update_axes_replaces(db):
    s = _startup(db)
    row = get_or_create_map(db, s)
    update_axes(
        db,
        row,
        {
            "x": {"label": "Reach", "low": "Niche", "high": "Mass"},
            "y": {"label": "Trust", "low": "New", "high": "Proven"},
        },
    )
    assert row.axes["x"]["label"] == "Reach"


def test_update_axes_bad_shape_422(db):
    s = _startup(db)
    row = get_or_create_map(db, s)
    with pytest.raises(AppError) as e:
        update_axes(db, row, {"x": "not-an-object"})
    assert e.value.http_status == 422


def test_assemble_lists_competitors_with_coords(db):
    s = _startup(db)
    create_record(db, s, RecordKind.competitor, {"name": "Acme", "map_x": 0.2, "map_y": 0.9})
    create_record(db, s, RecordKind.competitor, {"name": "Globex"})  # no coords
    out = assemble_map(db, s)
    by_name = {c["name"]: c for c in out["competitors"]}
    assert by_name["Acme"]["x"] == 0.2 and by_name["Acme"]["y"] == 0.9
    assert by_name["Globex"]["x"] is None and by_name["Globex"]["y"] is None
    assert out["axes"]["x"]["label"] == "Price"
