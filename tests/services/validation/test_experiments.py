import uuid

import pytest

from app.core.errors import AppError, NotFound
from app.db.models.enums import ExperimentStatus, ExperimentType, RiskLevel
from app.services.validation.service import (
    create_assumption,
    create_experiment,
    get_experiment,
    list_experiments,
    serialize_experiment,
    smoke_test_stats,
    update_experiment,
)
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, s, **kw):
    kw.setdefault("name", "Fake door")
    kw.setdefault("type", ExperimentType.smoke_test)
    return create_experiment(db, s.id, **kw)


def test_new_experiments_start_draft_and_empty(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    assert row.status == ExperimentStatus.draft
    assert row.config == {} and row.metrics == {} and row.assumption_ids == []


def test_links_must_belong_to_this_workspace(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    mine = create_assumption(db, s.id, statement="Mine", risk=RiskLevel.low)
    theirs = create_assumption(db, other.id, statement="Theirs", risk=RiskLevel.low)
    row = _new(db, s, assumption_ids=[str(mine.id)])
    assert row.assumption_ids == [str(mine.id)]
    with pytest.raises(AppError) as exc:
        _new(db, s, assumption_ids=[str(theirs.id)])
    assert exc.value.http_status == 422
    with pytest.raises(AppError):
        _new(db, s, assumption_ids=[str(uuid.uuid4())])


def test_list_filters_and_cross_workspace_404(db):
    _u, s = _ctx(db)
    _other_u, other = _ctx(db)
    _new(db, s, name="Smoke")
    _new(db, s, name="Advert", type=ExperimentType.ad_test, status=ExperimentStatus.live)
    assert len(list_experiments(db, s.id)) == 2
    assert len(list_experiments(db, s.id, type=ExperimentType.ad_test)) == 1
    assert len(list_experiments(db, s.id, status=ExperimentStatus.live)) == 1
    mine = _new(db, s)
    with pytest.raises(NotFound):
        get_experiment(db, other.id, mine.id)


def test_update_replaces_only_what_is_given(db):
    _u, s = _ctx(db)
    row = _new(db, s, config={"headline": "Old"}, metrics={"visits": 10})
    update_experiment(db, row, status=ExperimentStatus.live, metrics={"visits": 40, "signups": 4})
    assert row.status == ExperimentStatus.live
    assert row.metrics == {"visits": 40, "signups": 4}
    assert row.config == {"headline": "Old"} and row.name == "Fake door"


def test_smoke_stats_derive_conversion(db):
    _u, s = _ctx(db)
    row = _new(db, s, metrics={"visits": 200, "signups": 13})
    stats = smoke_test_stats(db, s.id, row.id)
    assert stats["visits"] == 200 and stats["signups"] == 13
    assert stats["conversion"] == 6.5


def test_smoke_stats_never_divide_by_zero(db):
    _u, s = _ctx(db)
    row = _new(db, s)
    stats = smoke_test_stats(db, s.id, row.id)
    assert stats == {
        "experiment_id": str(row.id),
        "name": "Fake door",
        "status": "draft",
        "visits": 0,
        "signups": 0,
        "conversion": 0.0,
    }


def test_stats_are_404_for_an_experiment_that_is_not_a_smoke_test(db):
    _u, s = _ctx(db)
    row = _new(db, s, type=ExperimentType.ad_test)
    with pytest.raises(NotFound):
        smoke_test_stats(db, s.id, row.id)


def test_serialize_shape(db):
    _u, s = _ctx(db)
    row = _new(db, s, config={"headline": "Try it"}, metrics={"visits": 1})
    out = serialize_experiment(row)
    assert out["id"] == str(row.id) and out["name"] == "Fake door"
    assert out["type"] == "smoke_test" and out["status"] == "draft"
    assert out["config"] == {"headline": "Try it"} and out["metrics"] == {"visits": 1}
    assert out["assumption_ids"] == []
