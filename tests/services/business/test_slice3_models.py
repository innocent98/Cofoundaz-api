from app.db.models.business import BusinessPositioningMap, BusinessSuggestion
from app.db.models.enums import SuggestionOp, SuggestionStatus
from tests.factories import create_startup, create_user


def test_suggestion_defaults_to_pending(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = BusinessSuggestion(
        startup_id=s.id,
        author_id=u.id,
        op=SuggestionOp.canvas_update,
        target={"canvas_type": "business_model"},
        payload={"blocks": {}},
        base_version=1,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.status == SuggestionStatus.pending
    assert row.resolved_at is None


def test_positioning_map_stores_axes(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = BusinessPositioningMap(startup_id=s.id, axes={"x": {"label": "Price"}})
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.axes["x"]["label"] == "Price"
