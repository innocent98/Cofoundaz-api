from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.core.errors import NotFound
from app.db.models.enums import ComplianceStatus, Recurrence
from app.db.models.legal import ComplianceItem
from app.schemas.legal import ComplianceCreate, ComplianceUpdate
from app.services.legal.compliance import (
    DUE_SOON_DAYS,
    create_item,
    delete_item,
    derived_compliance_status,
    get_item,
    list_items,
    serialize_item,
    update_item,
)
from tests.factories import create_startup, create_user

TODAY = date(2026, 7, 15)


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, u, s, **kw):
    kw.setdefault("title", "VAT filing")
    kw.setdefault("due_on", date(2026, 11, 30))
    return create_item(db, startup_id=s.id, created_by=u.id, data=ComplianceCreate(**kw))


# --- the derivation, as a pure function -------------------------------------------------------


def test_the_due_soon_window_is_fourteen_days():
    # Not arbitrary: 14 is the first reminder threshold, so the badge the user sees and the first
    # notification they get agree (spec D12). The mock also badges "in 9 days" as Due soon.
    assert DUE_SOON_DAYS == 14


@pytest.mark.parametrize(
    "status,due_on,expected",
    [
        # Done beats everything: a completed filing is never overdue, however old.
        (ComplianceStatus.done, TODAY - timedelta(days=100), "done"),
        (ComplianceStatus.done, TODAY, "done"),
        (ComplianceStatus.done, TODAY + timedelta(days=90), "done"),
        # Overdue: strictly before today.
        (ComplianceStatus.open, TODAY - timedelta(days=1), "overdue"),
        (ComplianceStatus.open, TODAY - timedelta(days=365), "overdue"),
        # Due today is NOT overdue — today has not passed.
        (ComplianceStatus.open, TODAY, "due_soon"),
        # The window boundary: 14 days is in, 15 is out.
        (ComplianceStatus.open, TODAY + timedelta(days=13), "due_soon"),
        (ComplianceStatus.open, TODAY + timedelta(days=14), "due_soon"),
        (ComplianceStatus.open, TODAY + timedelta(days=15), "upcoming"),
        (ComplianceStatus.open, TODAY + timedelta(days=400), "upcoming"),
    ],
)
def test_derived_status_covers_every_boundary(status, due_on, expected):
    assert derived_compliance_status(status, due_on, TODAY) == expected


# --- create -----------------------------------------------------------------------------------


def test_create_sets_the_defaults(db):
    u, s = _ctx(db)
    row = _new(db, u, s)
    assert row.status == ComplianceStatus.open
    assert row.recurrence == Recurrence.none
    assert row.done_at is None
    assert row.authority is None  # nullable (spec D15)
    assert row.proof_url is None and row.proof_key is None
    assert row.previous_item_id is None
    assert row.created_by == u.id


def test_anchor_day_is_derived_from_the_due_date(db):
    u, s = _ctx(db)
    row = _new(db, u, s, due_on=date(2026, 1, 31))
    # Never client-settable: it is the series' anchor, and a caller could otherwise
    # desynchronise it from its own due_on (spec D24).
    assert row.anchor_day == 31
    assert "anchor_day" not in ComplianceCreate.model_fields


def test_title_and_authority_are_bounded():
    with pytest.raises(ValidationError):
        ComplianceCreate(title="", due_on=TODAY)
    with pytest.raises(ValidationError):
        ComplianceCreate(title="t" * 201, due_on=TODAY)
    with pytest.raises(ValidationError):
        ComplianceCreate(title="VAT", due_on=TODAY, authority="a" * 121)
    # Trimmed, so whitespace-only is rejected rather than stored blank.
    with pytest.raises(ValidationError):
        ComplianceCreate(title="   ", due_on=TODAY)


def test_due_on_is_required():
    with pytest.raises(ValidationError):
        ComplianceCreate(title="VAT")


def test_serialize_returns_both_statuses_and_an_iso_date(db):
    u, s = _ctx(db)
    row = _new(db, u, s, due_on=TODAY - timedelta(days=5), authority="FIRS")
    out = serialize_item(row, today=TODAY)
    assert out["status"] == "open"  # raw
    assert out["derived_status"] == "overdue"  # derived for the badge
    assert out["due_on"] == (TODAY - timedelta(days=5)).isoformat()  # never "Was due Jul 10"
    assert out["authority"] == "FIRS"
    assert out["recurrence"] == "none"


# --- list -------------------------------------------------------------------------------------


def test_range_filter_includes_both_boundaries(db):
    u, s = _ctx(db)
    _new(db, u, s, title="Before", due_on=date(2026, 6, 30))
    _new(db, u, s, title="On from", due_on=date(2026, 7, 1))
    _new(db, u, s, title="Inside", due_on=date(2026, 7, 15))
    _new(db, u, s, title="On to", due_on=date(2026, 7, 31))
    _new(db, u, s, title="After", due_on=date(2026, 8, 1))
    rows = list_items(db, startup_id=s.id, date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
    assert [r.title for r in rows] == ["On from", "Inside", "On to"]


def test_the_list_is_ordered_by_due_date(db):
    u, s = _ctx(db)
    _new(db, u, s, title="Later", due_on=date(2026, 9, 1))
    _new(db, u, s, title="Sooner", due_on=date(2026, 8, 1))
    assert [r.title for r in list_items(db, startup_id=s.id)] == ["Sooner", "Later"]


def test_status_filters_the_stored_value_not_the_derived_one(db):
    u, s = _ctx(db)
    open_row = _new(db, u, s, title="Still open", due_on=date(2026, 1, 1))
    done_row = _new(db, u, s, title="Finished", due_on=date(2026, 2, 1))
    update_item(
        db,
        startup_id=s.id,
        item_id=done_row.id,
        data=ComplianceUpdate(status=ComplianceStatus.done),
    )
    # Filtering on a derived value would need the same date maths in SQL and in Python, and the
    # two would drift. `open` and `done` only.
    assert [r.title for r in list_items(db, startup_id=s.id, status=ComplianceStatus.open)] == [
        "Still open"
    ]
    assert [r.title for r in list_items(db, startup_id=s.id, status=ComplianceStatus.done)] == [
        "Finished"
    ]
    assert open_row.id is not None


def test_a_backwards_range_returns_nothing(db):
    u, s = _ctx(db)
    _new(db, u, s)
    rows = list_items(db, startup_id=s.id, date_from=date(2026, 12, 1), date_to=date(2026, 1, 1))
    assert rows == []


def test_the_list_never_crosses_workspaces(db):
    u, mine = _ctx(db)
    other_u, theirs = _ctx(db)
    _new(db, u, mine, title="Mine")
    _new(db, other_u, theirs, title="Theirs")
    assert [r.title for r in list_items(db, startup_id=mine.id)] == ["Mine"]


# --- get / update / delete --------------------------------------------------------------------


def test_get_another_workspaces_item_is_a_404(db):
    u, mine = _ctx(db)
    other_u, theirs = _ctx(db)
    row = _new(db, other_u, theirs)
    with pytest.raises(NotFound):
        get_item(db, startup_id=mine.id, item_id=row.id)


def test_update_changes_only_what_was_sent(db):
    u, s = _ctx(db)
    row = _new(db, u, s, authority="FIRS")
    updated = update_item(
        db, startup_id=s.id, item_id=row.id, data=ComplianceUpdate(title="Renamed")
    )
    assert updated.title == "Renamed"
    assert updated.authority == "FIRS"
    assert updated.due_on == date(2026, 11, 30)


def test_an_explicit_null_clears_the_authority(db):
    u, s = _ctx(db)
    row = _new(db, u, s, authority="FIRS")
    updated = update_item(
        db, startup_id=s.id, item_id=row.id, data=ComplianceUpdate(authority=None)
    )
    assert updated.authority is None


def test_an_explicit_null_on_a_required_field_is_rejected():
    for field in ("title", "due_on", "recurrence", "status"):
        with pytest.raises(ValidationError):
            ComplianceUpdate(**{field: None})


def test_moving_the_due_date_resets_the_anchor(db):
    u, s = _ctx(db)
    row = _new(db, u, s, due_on=date(2026, 1, 31))
    assert row.anchor_day == 31
    updated = update_item(
        db, startup_id=s.id, item_id=row.id, data=ComplianceUpdate(due_on=date(2026, 2, 15))
    )
    # A founder moving the series date means the series moves (spec D24).
    assert updated.anchor_day == 15


def test_an_update_that_leaves_the_due_date_alone_keeps_the_anchor(db):
    u, s = _ctx(db)
    row = _new(db, u, s, due_on=date(2026, 1, 31))
    updated = update_item(
        db, startup_id=s.id, item_id=row.id, data=ComplianceUpdate(title="Renamed")
    )
    assert updated.anchor_day == 31


def test_marking_done_records_when_and_reopening_clears_it(db):
    u, s = _ctx(db)
    row = _new(db, u, s)
    done = update_item(
        db, startup_id=s.id, item_id=row.id, data=ComplianceUpdate(status=ComplianceStatus.done)
    )
    assert done.status == ComplianceStatus.done and done.done_at is not None

    reopened = update_item(
        db, startup_id=s.id, item_id=row.id, data=ComplianceUpdate(status=ComplianceStatus.open)
    )
    assert reopened.status == ComplianceStatus.open and reopened.done_at is None


def test_delete_then_delete_again(db):
    u, s = _ctx(db)
    row = _new(db, u, s)
    delete_item(db, startup_id=s.id, item_id=row.id)
    assert list_items(db, startup_id=s.id) == []
    with pytest.raises(NotFound):
        delete_item(db, startup_id=s.id, item_id=row.id)


# --- recurrence: the spawn --------------------------------------------------------------------


def _mark_done(db, s_, item):
    return update_item(
        db, startup_id=s_.id, item_id=item.id, data=ComplianceUpdate(status=ComplianceStatus.done)
    )


def _reopen(db, s_, item):
    return update_item(
        db, startup_id=s_.id, item_id=item.id, data=ComplianceUpdate(status=ComplianceStatus.open)
    )


def _successors(db, item):
    return db.query(ComplianceItem).filter_by(previous_item_id=item.id).all()


def test_a_non_recurring_item_spawns_nothing(db):
    u, s = _ctx(db)
    row = _new(db, u, s)
    _mark_done(db, s, row)
    assert _successors(db, row) == []
    assert db.query(ComplianceItem).filter_by(startup_id=s.id).count() == 1


def test_marking_a_monthly_item_done_spawns_the_next_occurrence(db):
    u, s = _ctx(db)
    row = _new(
        db,
        u,
        s,
        title="VAT filing",
        authority="FIRS",
        due_on=date(2026, 1, 31),
        recurrence=Recurrence.monthly,
    )
    _mark_done(db, s, row)
    successors = _successors(db, row)
    assert len(successors) == 1
    nxt = successors[0]
    # Anchored on the 31st, so February clamps (spec D16).
    assert nxt.due_on == date(2026, 2, 28)
    assert nxt.anchor_day == 31
    assert nxt.status == ComplianceStatus.open and nxt.done_at is None
    assert nxt.title == "VAT filing" and nxt.authority == "FIRS"
    assert nxt.recurrence == Recurrence.monthly
    assert nxt.proof_url is None and nxt.proof_key is None
    assert nxt.previous_item_id == row.id
    assert nxt.startup_id == s.id


def test_marking_done_twice_spawns_one_successor(db):
    u, s = _ctx(db)
    row = _new(db, u, s, recurrence=Recurrence.monthly, due_on=date(2026, 1, 15))
    _mark_done(db, s, row)
    _mark_done(db, s, row)
    assert len(_successors(db, row)) == 1
    # The follow-up read is the point: the duplicate insert must have rolled back inside a
    # SAVEPOINT, or this would fail on a poisoned session.
    assert list_items(db, startup_id=s.id)


def test_done_reopen_done_spawns_one_successor_and_keeps_it(db):
    u, s = _ctx(db)
    row = _new(db, u, s, recurrence=Recurrence.monthly, due_on=date(2026, 1, 15))
    _mark_done(db, s, row)
    first = _successors(db, row)[0]

    _reopen(db, s, row)
    # Reopening does NOT delete the occurrence already spawned: destroying someone's future
    # filing as a side effect of a correction is worse than a row they can delete themselves.
    assert _successors(db, row) == [first]

    _mark_done(db, s, row)
    assert _successors(db, row) == [first]


def test_after_the_successor_is_deleted_a_fresh_one_may_spawn(db):
    u, s = _ctx(db)
    row = _new(db, u, s, recurrence=Recurrence.monthly, due_on=date(2026, 1, 15))
    _mark_done(db, s, row)
    first = _successors(db, row)[0]
    delete_item(db, startup_id=s.id, item_id=first.id)
    assert _successors(db, row) == []

    _reopen(db, s, row)
    _mark_done(db, s, row)
    # Intended: the back-link is gone, so the guard has nothing to collide with and the series
    # continues after someone deletes an occurrence by mistake (spec D16).
    assert len(_successors(db, row)) == 1


def test_deleting_a_predecessor_leaves_its_successor_alive(db):
    u, s = _ctx(db)
    row = _new(db, u, s, recurrence=Recurrence.monthly, due_on=date(2026, 1, 15))
    _mark_done(db, s, row)
    nxt_id = _successors(db, row)[0].id

    delete_item(db, startup_id=s.id, item_id=row.id)
    survivor = db.query(ComplianceItem).filter_by(id=nxt_id).one()
    # ON DELETE SET NULL: the successor survives and simply loses the back-link.
    assert survivor.previous_item_id is None


def test_moving_the_due_date_then_finishing_moves_the_series(db):
    u, s = _ctx(db)
    row = _new(db, u, s, recurrence=Recurrence.monthly, due_on=date(2026, 1, 31))
    update_item(
        db, startup_id=s.id, item_id=row.id, data=ComplianceUpdate(due_on=date(2026, 1, 15))
    )
    _mark_done(db, s, row)
    nxt = _successors(db, row)[0]
    # A founder moving the series date moves the series (spec D24): the 15th, not the 31st.
    assert nxt.anchor_day == 15
    assert nxt.due_on == date(2026, 2, 15)


def test_a_quarterly_series_chains(db):
    u, s = _ctx(db)
    first = _new(db, u, s, recurrence=Recurrence.quarterly, due_on=date(2026, 11, 30))
    _mark_done(db, s, first)
    second = _successors(db, first)[0]
    assert second.due_on == date(2027, 2, 28)

    _mark_done(db, s, second)
    third = _successors(db, second)[0]
    # Anchored, so the series recovers to the 30th rather than staying on the 28th.
    assert third.due_on == date(2027, 5, 30)


def test_the_successor_belongs_to_the_same_workspace_only(db):
    u, mine = _ctx(db)
    other_u, theirs = _ctx(db)
    row = _new(db, u, mine, recurrence=Recurrence.monthly, due_on=date(2026, 1, 15))
    _mark_done(db, mine, row)
    assert db.query(ComplianceItem).filter_by(startup_id=theirs.id).count() == 0
    assert db.query(ComplianceItem).filter_by(startup_id=mine.id).count() == 2
