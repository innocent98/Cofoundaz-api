import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.db.models.activity import ActivityLog
from app.db.models.enums import StageKind
from app.db.models.sales import Deal, PipelineStage
from app.schemas.sales import SettingsUpdate, StageIn
from app.services.sales import stages as svc
from tests.factories import create_startup, create_user

DEFAULTS = [
    ("New", StageKind.open, 10),
    ("Contacted", StageKind.open, 20),
    ("Qualified", StageKind.open, 40),
    ("Proposal", StageKind.open, 60),
    ("Negotiation", StageKind.open, 80),
    ("Closed Won", StageKind.won, 100),
    ("Closed Lost", StageKind.lost, 0),
]


def _startup(db):
    return create_startup(db, owner=create_user(db))


def _as_in(stages):
    return [
        StageIn(id=s.id, name=s.name, kind=s.kind, win_probability=s.win_probability)
        for s in stages
    ]


def _deal(db, startup, stage):
    d = Deal(startup_id=startup.id, name="Acme", stage_id=stage.id)
    db.add(d)
    db.flush()
    return d


def _field(exc: AppError) -> list[str]:
    return [e["field"] for e in exc.field_errors]


def test_ensure_stages_seeds_defaults_once(db):
    s = _startup(db)
    first = svc.list_stages(db, s.id)
    second = svc.list_stages(db, s.id)
    assert [(x.name, x.kind, x.win_probability) for x in first] == DEFAULTS
    assert [x.position for x in first] == list(range(7))
    assert [x.id for x in first] == [x.id for x in second]
    count = db.scalar(select(func.count()).select_from(PipelineStage))
    assert count == 7


def test_rename_and_reorder_keep_ids(db):
    s = _startup(db)
    st = svc.list_stages(db, s.id)
    ids = [x.id for x in st]
    items = _as_in(st)
    items[0] = items[0].model_copy(update={"name": "Lead In"})
    items[1], items[2] = items[2], items[1]
    out = svc.replace_stages(db, s.id, items)
    assert [x.id for x in out] == [ids[0], ids[2], ids[1], *ids[3:]]
    assert out[0].name == "Lead In"
    assert [x.position for x in out] == list(range(7))


def test_add_eighth_stage(db):
    s = _startup(db)
    items = _as_in(svc.list_stages(db, s.id))
    items.insert(2, StageIn(id=None, name="Demo", kind=StageKind.open, win_probability=30))
    out = svc.replace_stages(db, s.id, items)
    assert len(out) == 8
    assert out[2].name == "Demo" and out[2].id is not None


def test_omit_empty_stage_soft_deletes(db):
    s = _startup(db)
    st = svc.list_stages(db, s.id)
    gone = st[1]
    out = svc.replace_stages(db, s.id, _as_in([x for x in st if x.id != gone.id]))
    assert gone.id not in [x.id for x in out]
    db.expire_all()
    row = db.get(PipelineStage, gone.id)
    assert row is not None and row.archived_at is not None
    assert [x.id for x in svc.list_stages(db, s.id)] == [x.id for x in out]


def test_omit_stage_with_deal_is_422(db):
    s = _startup(db)
    st = svc.list_stages(db, s.id)
    _deal(db, s, st[1])
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, s.id, _as_in([x for x in st if x.id != st[1].id]))
    assert e.value.http_status == 422
    assert _field(e.value) == ["stages"]
    assert e.value.field_errors[0]["message"] == "Move the deals out of Contacted first."
    assert db.get(PipelineStage, st[1].id).archived_at is None


def test_omit_won_stage_is_422(db):
    s = _startup(db)
    st = svc.list_stages(db, s.id)
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, s.id, _as_in([x for x in st if x.kind != StageKind.won]))
    assert e.value.http_status == 422


def test_two_won_stages_is_422(db):
    s = _startup(db)
    items = _as_in(svc.list_stages(db, s.id))
    items.insert(1, StageIn(id=None, name="Also Won", kind=StageKind.won, win_probability=100))
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, s.id, items)
    assert e.value.http_status == 422


def test_more_than_ten_stages_is_422(db):
    s = _startup(db)
    items = _as_in(svc.list_stages(db, s.id))
    for i in range(4):
        items.insert(1, StageIn(id=None, name=f"Extra {i}", kind=StageKind.open, win_probability=5))
    assert len(items) == 11
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, s.id, items)
    assert e.value.http_status == 422


def test_duplicate_name_case_insensitive_is_422(db):
    s = _startup(db)
    items = _as_in(svc.list_stages(db, s.id))
    items[1] = items[1].model_copy(update={"name": "new"})
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, s.id, items)
    assert e.value.http_status == 422


def test_kind_change_on_existing_stage_is_422(db):
    s = _startup(db)
    items = _as_in(svc.list_stages(db, s.id))
    items[0] = items[0].model_copy(update={"kind": StageKind.won})
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, s.id, items)
    assert e.value.http_status == 422


def test_foreign_stage_id_is_422_and_not_touched(db):
    a, b = _startup(db), _startup(db)
    svc.list_stages(db, a.id)
    b_stages = svc.list_stages(db, b.id)
    items = _as_in(svc.list_stages(db, a.id))
    items[0] = StageIn(id=b_stages[0].id, name="Hijack", kind=StageKind.open, win_probability=1)
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, a.id, items)
    assert e.value.http_status == 422
    db.expire_all()
    assert db.get(PipelineStage, b_stages[0].id).name == "New"


def test_unknown_stage_id_is_422(db):
    s = _startup(db)
    items = _as_in(svc.list_stages(db, s.id))
    items[0] = items[0].model_copy(update={"id": uuid.uuid4()})
    with pytest.raises(AppError) as e:
        svc.replace_stages(db, s.id, items)
    assert e.value.http_status == 422


def test_stages_scoped_per_workspace(db):
    a, b = _startup(db), _startup(db)
    sa, sb = svc.list_stages(db, a.id), svc.list_stages(db, b.id)
    assert not {x.id for x in sa} & {x.id for x in sb}


def test_replace_writes_activity(db):
    s = _startup(db)
    u = create_user(db)
    items = _as_in(svc.list_stages(db, s.id))
    svc.replace_stages(db, s.id, items, actor_id=u.id)
    row = db.scalar(select(ActivityLog).where(ActivityLog.action == "sales.stages.updated"))
    assert row is not None and row.actor_user_id == u.id and row.startup_id == s.id


def test_settings_lazy_create_and_update(db):
    s = _startup(db)
    u = create_user(db)
    row = svc.get_settings(db, s.id)
    assert row.stall_days == 14 and row.outreach_postal_address is None
    assert svc.get_settings(db, s.id).startup_id == row.startup_id
    out = svc.update_settings(
        db,
        s.id,
        SettingsUpdate(stall_days=30, outreach_postal_address="1 Broad St, Lagos"),
        actor_id=u.id,
    )
    assert out.stall_days == 30 and out.outreach_postal_address == "1 Broad St, Lagos"
    act = db.scalar(select(ActivityLog).where(ActivityLog.action == "sales.settings.updated"))
    assert act is not None and act.actor_user_id == u.id


@pytest.mark.parametrize("bad", [2, 91])
def test_stall_days_bounds(bad):
    with pytest.raises(ValueError):  # noqa: PT011
        SettingsUpdate(stall_days=bad)


def test_blank_address_clears(db):
    s = _startup(db)
    svc.update_settings(db, s.id, SettingsUpdate(outreach_postal_address="x"), actor_id=None)
    out = svc.update_settings(db, s.id, SettingsUpdate(outreach_postal_address="  "), actor_id=None)
    assert out.outreach_postal_address is None
