from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.core.errors import NotFound
from app.db.models.enums import IpAssetStatus, IpAssetType
from app.schemas.legal import IpAssetCreate, IpAssetUpdate
from app.services.legal.ip import (
    create_asset,
    delete_asset,
    derived_ip_status,
    get_asset,
    list_assets,
    serialize_asset,
    update_asset,
)
from tests.factories import create_startup, create_user

TODAY = date(2026, 7, 15)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _new(db, u, s, **kw):
    kw.setdefault("type", IpAssetType.trademark)
    kw.setdefault("name", "Kolo (wordmark)")
    kw.setdefault("jurisdiction", "Nigeria")
    kw.setdefault("status", IpAssetStatus.filed)
    return create_asset(db, startup_id=s.id, created_by=u.id, data=IpAssetCreate(**kw))


# --- the derivation, as a pure function -------------------------------------------------------


@pytest.mark.parametrize("status", list(IpAssetStatus))
def test_no_renewal_date_means_the_raw_status(status):
    # "No renewal" in the mock means renewal_on is null, not a state of its own.
    assert derived_ip_status(status, None, TODAY) == status.value


@pytest.mark.parametrize("status", list(IpAssetStatus))
def test_a_past_renewal_is_expired_whatever_the_stored_status(status):
    # `expired` is derived, never stored: the FE's add drawer cannot produce it (spec D9).
    assert derived_ip_status(status, YESTERDAY, TODAY) == "expired"


@pytest.mark.parametrize("status", list(IpAssetStatus))
def test_a_renewal_due_today_is_not_yet_expired(status):
    # The boundary that catches an off-by-one: today has not passed.
    assert derived_ip_status(status, TODAY, TODAY) == status.value


@pytest.mark.parametrize("status", list(IpAssetStatus))
def test_a_future_renewal_is_the_raw_status(status):
    assert derived_ip_status(status, TOMORROW, TODAY) == status.value


# --- create -----------------------------------------------------------------------------------


def test_create_with_the_add_drawer_fields(db):
    u, s = _ctx(db)
    row = _new(db, u, s, renewal_on=date(2027, 2, 1))
    assert row.startup_id == s.id and row.created_by == u.id
    assert row.type == IpAssetType.trademark
    assert row.status == IpAssetStatus.filed
    assert row.renewal_on == date(2027, 2, 1)


def test_renewal_date_is_optional(db):
    u, s = _ctx(db)
    row = _new(db, u, s, type=IpAssetType.copyright, name="App source code")
    assert row.renewal_on is None


def test_jurisdiction_is_a_bounded_free_string(db):
    u, s = _ctx(db)
    # It has to hold "Global" and "EU" as well as a country, so it is not a closed set — but it
    # is bounded, trimmed, 1-100 characters (spec D10).
    row = _new(db, u, s, type=IpAssetType.domain, name="kolo.africa", jurisdiction="  Global  ")
    assert row.jurisdiction == "Global"
    with pytest.raises(ValidationError):
        IpAssetCreate(
            type=IpAssetType.domain, name="x", jurisdiction="", status=IpAssetStatus.filed
        )
    with pytest.raises(ValidationError):
        IpAssetCreate(
            type=IpAssetType.domain,
            name="x",
            jurisdiction="j" * 101,
            status=IpAssetStatus.filed,
        )


def test_name_is_bounded(db):
    with pytest.raises(ValidationError):
        IpAssetCreate(
            type=IpAssetType.patent, name="", jurisdiction="Nigeria", status=IpAssetStatus.filed
        )
    with pytest.raises(ValidationError):
        IpAssetCreate(
            type=IpAssetType.patent,
            name="n" * 201,
            jurisdiction="Nigeria",
            status=IpAssetStatus.filed,
        )


def test_expired_is_not_an_acceptable_status_on_create():
    # Three stored values only (spec D9).
    with pytest.raises(ValidationError):
        IpAssetCreate(
            type=IpAssetType.trademark, name="Logo", jurisdiction="Nigeria", status="expired"
        )


def test_serialize_returns_both_statuses(db):
    u, s = _ctx(db)
    row = _new(db, u, s, status=IpAssetStatus.registered, renewal_on=YESTERDAY)
    out = serialize_asset(row, today=TODAY)
    assert out["status"] == "registered"  # raw, straight from the row
    assert out["derived_status"] == "expired"  # derived for the badge
    assert out["renewal_on"] == YESTERDAY.isoformat()  # ISO, never "Renews Feb 2027"


# --- list / get / update / delete --------------------------------------------------------------


def test_list_filters_and_never_crosses_workspaces(db):
    u, mine = _ctx(db)
    other_u, theirs = _ctx(db)
    _new(db, u, mine, name="Mine A")
    _new(db, u, mine, name="Mine B", type=IpAssetType.domain, status=IpAssetStatus.registered)
    _new(db, other_u, theirs, name="Theirs")

    assert len(list_assets(db, startup_id=mine.id)) == 2
    by_type = list_assets(db, startup_id=mine.id, type=IpAssetType.domain)
    assert [r.name for r in by_type] == ["Mine B"]
    by_status = list_assets(db, startup_id=mine.id, status=IpAssetStatus.registered)
    assert [r.name for r in by_status] == ["Mine B"]


def test_get_another_workspaces_asset_is_a_404(db):
    u, mine = _ctx(db)
    other_u, theirs = _ctx(db)
    row = _new(db, other_u, theirs, name="Theirs")
    with pytest.raises(NotFound):
        get_asset(db, startup_id=mine.id, asset_id=row.id)


def test_update_changes_only_what_was_sent(db):
    u, s = _ctx(db)
    row = _new(db, u, s, renewal_on=date(2027, 2, 1))
    updated = update_asset(
        db, startup_id=s.id, asset_id=row.id, data=IpAssetUpdate(status=IpAssetStatus.registered)
    )
    assert updated.status == IpAssetStatus.registered
    assert updated.name == "Kolo (wordmark)"
    assert updated.renewal_on == date(2027, 2, 1)


def test_an_explicit_null_clears_the_renewal_date(db):
    u, s = _ctx(db)
    row = _new(db, u, s, renewal_on=date(2027, 2, 1))
    updated = update_asset(
        db, startup_id=s.id, asset_id=row.id, data=IpAssetUpdate(renewal_on=None)
    )
    assert updated.renewal_on is None


def test_an_explicit_null_on_a_required_field_is_rejected():
    for field in ("type", "name", "jurisdiction", "status"):
        with pytest.raises(ValidationError):
            IpAssetUpdate(**{field: None})


def test_delete_then_delete_again(db):
    u, s = _ctx(db)
    row = _new(db, u, s)
    delete_asset(db, startup_id=s.id, asset_id=row.id)
    assert list_assets(db, startup_id=s.id) == []
    with pytest.raises(NotFound):
        delete_asset(db, startup_id=s.id, asset_id=row.id)
