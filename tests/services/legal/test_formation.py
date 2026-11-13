import pytest

from app.core.errors import NotFound
from app.db.models.legal import LegalFormationProgress
from app.services.legal.catalog import STEPS
from app.services.legal.formation import checklist, set_step
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _tick(db, startup, actor, step_key, *, jurisdiction="NG", completed=True):
    set_step(
        db,
        startup_id=startup.id,
        jurisdiction=jurisdiction,
        step_key=step_key,
        completed=completed,
        actor_id=actor.id,
    )


def test_a_fresh_checklist_is_the_catalog_with_nothing_completed(db):
    _u, s = _ctx(db)
    out = checklist(db, startup_id=s.id, jurisdiction="NG")
    assert out["jurisdiction"] == {"code": "NG", "name": "Nigeria"}
    assert [step["key"] for step in out["steps"]] == [s_.key for s_ in STEPS["NG"]]
    assert all(step["completed"] is False for step in out["steps"])
    assert all(step["completed_at"] is None for step in out["steps"])
    # The figures are estimates, and the caller is told so (spec D6).
    assert out["estimates"] is True
    assert out["disclaimer"]


def test_the_catalog_fields_are_separate_not_one_meta_string(db):
    _u, s = _ctx(db)
    step = checklist(db, startup_id=s.id, jurisdiction="NG")["steps"][0]
    # The mock squashes these into "CAC · est. 1 day · ₦500"; the API returns them apart and the
    # FE formats (spec §FE cross-check 4).
    for field in ("authority", "est_days", "est_cost_minor", "est_cost_currency"):
        assert field in step
    assert "meta" not in step


def test_tick_then_untick_then_retick(db):
    u, s = _ctx(db)
    _tick(db, s, u, "reserve_name")
    first = checklist(db, startup_id=s.id, jurisdiction="NG")["steps"][0]
    assert first["completed"] is True and first["completed_at"] is not None

    _tick(db, s, u, "reserve_name", completed=False)
    # Unticking deletes the row; the row's existence IS the tick (spec D7).
    assert db.query(LegalFormationProgress).filter_by(startup_id=s.id).count() == 0
    assert checklist(db, startup_id=s.id, jurisdiction="NG")["steps"][0]["completed"] is False

    _tick(db, s, u, "reserve_name")
    assert checklist(db, startup_id=s.id, jurisdiction="NG")["steps"][0]["completed"] is True


def test_unticking_something_never_ticked_is_harmless(db):
    u, s = _ctx(db)
    _tick(db, s, u, "reserve_name", completed=False)
    assert db.query(LegalFormationProgress).filter_by(startup_id=s.id).count() == 0


def test_the_same_step_key_under_two_jurisdictions_is_independent(db):
    u, s = _ctx(db)
    # `bank_account` exists in both catalogs. Ticking it for Nigeria must not tick Delaware's.
    _tick(db, s, u, "bank_account", jurisdiction="NG")
    ng = {
        step["key"]: step["completed"]
        for step in checklist(db, startup_id=s.id, jurisdiction="NG")["steps"]
    }
    de = {
        step["key"]: step["completed"]
        for step in checklist(db, startup_id=s.id, jurisdiction="US-DE")["steps"]
    }
    assert ng["bank_account"] is True
    assert de["bank_account"] is False


def test_switching_jurisdiction_never_wipes_progress(db):
    u, s = _ctx(db)
    _tick(db, s, u, "reserve_name", jurisdiction="NG")
    # Read Delaware, then come back. The mock resets on switch; we must not (spec D8).
    checklist(db, startup_id=s.id, jurisdiction="US-DE")
    assert checklist(db, startup_id=s.id, jurisdiction="NG")["steps"][0]["completed"] is True


def test_a_duplicate_tick_is_a_no_op_and_leaves_the_session_usable(db):
    u, s = _ctx(db)
    _tick(db, s, u, "reserve_name")
    _tick(db, s, u, "reserve_name")
    assert db.query(LegalFormationProgress).filter_by(startup_id=s.id).count() == 1
    # The follow-up read is the point: the duplicate insert must have been rolled back inside a
    # SAVEPOINT, or this read would fail on a poisoned session.
    out = checklist(db, startup_id=s.id, jurisdiction="NG")
    assert out["steps"][0]["completed"] is True


def test_an_unsupported_jurisdiction_is_not_found(db):
    u, s = _ctx(db)
    with pytest.raises(NotFound):
        checklist(db, startup_id=s.id, jurisdiction="KE")
    with pytest.raises(NotFound):
        _tick(db, s, u, "reserve_name", jurisdiction="KE")


def test_a_step_key_from_another_jurisdiction_is_not_found(db):
    u, s = _ctx(db)
    # `reserve_name` is a Nigerian step; Delaware has no such key (spec D3).
    with pytest.raises(NotFound):
        _tick(db, s, u, "reserve_name", jurisdiction="US-DE")
    with pytest.raises(NotFound):
        _tick(db, s, u, "not_a_real_step", jurisdiction="NG")


def test_nothing_is_written_when_the_key_is_rejected(db):
    u, s = _ctx(db)
    with pytest.raises(NotFound):
        _tick(db, s, u, "not_a_real_step")
    # Validation happens before any write, so a rejected tick leaves no row behind.
    assert db.query(LegalFormationProgress).filter_by(startup_id=s.id).count() == 0


def test_one_workspace_never_sees_another_s_progress(db):
    u, mine = _ctx(db)
    other_u, theirs = _ctx(db)
    _tick(db, mine, u, "reserve_name")
    out = checklist(db, startup_id=theirs.id, jurisdiction="NG")
    assert all(step["completed"] is False for step in out["steps"])


def test_the_actor_is_recorded(db):
    u, s = _ctx(db)
    _tick(db, s, u, "reserve_name")
    row = db.query(LegalFormationProgress).filter_by(startup_id=s.id).one()
    assert row.completed_by == u.id
