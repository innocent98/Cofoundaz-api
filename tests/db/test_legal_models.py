from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.enums import (
    LEGAL_ROLES,
    ComplianceStatus,
    IpAssetStatus,
    IpAssetType,
    MembershipRole,
    Recurrence,
)
from app.db.models.legal import ComplianceItem, IpAsset, LegalFormationProgress
from tests.factories import create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    return u, s


def _progress(db, startup, **kw):
    kw.setdefault("jurisdiction", "NG")
    kw.setdefault("step_key", "reserve_name")
    row = LegalFormationProgress(startup_id=startup.id, **kw)
    db.add(row)
    db.flush()
    return row


def _asset(db, startup, **kw):
    kw.setdefault("type", IpAssetType.trademark)
    kw.setdefault("name", "Kolo (wordmark)")
    kw.setdefault("jurisdiction", "Nigeria")
    kw.setdefault("status", IpAssetStatus.filed)
    row = IpAsset(startup_id=startup.id, **kw)
    db.add(row)
    db.flush()
    return row


def _item(db, startup, **kw):
    kw.setdefault("title", "VAT filing")
    kw.setdefault("due_on", date(2026, 11, 30))
    kw.setdefault("anchor_day", 30)
    row = ComplianceItem(startup_id=startup.id, **kw)
    db.add(row)
    db.flush()
    return row


# --- enums and the role set -------------------------------------------------------------------


def test_enum_values():
    assert {t.value for t in IpAssetType} == {"trademark", "patent", "copyright", "domain"}
    # Three stored statuses, not four: `expired` is derived from renewal_on (spec D9).
    assert {s.value for s in IpAssetStatus} == {"drafting", "filed", "registered"}
    assert {s.value for s in ComplianceStatus} == {"open", "done"}
    assert {r.value for r in Recurrence} == {"none", "monthly", "quarterly", "annually"}


def test_legal_roles_is_the_reader_set():
    # One definition shared by the routes and the notification audience (spec D1/D4).
    assert LEGAL_ROLES == frozenset(
        {MembershipRole.founder, MembershipRole.team_member, MembershipRole.legal_advisor}
    )
    assert MembershipRole.accountant not in LEGAL_ROLES
    assert MembershipRole.mentor not in LEGAL_ROLES


# --- formation progress -----------------------------------------------------------------------


def test_a_progress_row_is_the_tick(db):
    _u, s = _ctx(db)
    row = _progress(db, s)
    db.refresh(row)
    # The row's existence IS the tick (spec D7) — there is no nullable completed flag.
    assert isinstance(row.completed_at, datetime)
    assert row.completed_by is None


def test_the_same_step_key_under_two_jurisdictions_is_two_rows(db):
    _u, s = _ctx(db)
    _progress(db, s, jurisdiction="NG", step_key="bank_account")
    _progress(db, s, jurisdiction="US-DE", step_key="bank_account")
    assert db.query(LegalFormationProgress).filter_by(startup_id=s.id).count() == 2


def test_a_duplicate_tick_violates_the_unique_constraint(db):
    _u, s = _ctx(db)
    _progress(db, s)
    db.add(LegalFormationProgress(startup_id=s.id, jurisdiction="NG", step_key="reserve_name"))
    with pytest.raises(IntegrityError):
        db.flush()


# --- ip assets --------------------------------------------------------------------------------


def test_ip_asset_defaults(db):
    _u, s = _ctx(db)
    row = _asset(db, s)
    db.refresh(row)
    assert row.renewal_on is None
    assert row.created_by is None


def test_ip_jurisdiction_holds_global(db):
    _u, s = _ctx(db)
    # A free string, not a closed set of codes: it has to hold "Global" (spec D10).
    row = _asset(db, s, type=IpAssetType.domain, name="kolo.africa", jurisdiction="Global")
    db.refresh(row)
    assert row.jurisdiction == "Global"


def test_ip_enums_store_their_values(db):
    u, s = _ctx(db)
    row = _asset(
        db,
        s,
        status=IpAssetStatus.registered,
        created_by=u.id,
        renewal_on=date(2027, 2, 1),
    )
    stored = db.execute(
        text("SELECT type, status FROM ip_assets WHERE id = :i"), {"i": str(row.id)}
    ).one()
    assert stored.type == "trademark" and stored.status == "registered"  # VALUES, not names


# --- compliance items -------------------------------------------------------------------------


def test_compliance_defaults(db):
    _u, s = _ctx(db)
    row = _item(db, s)
    db.refresh(row)
    assert row.status == ComplianceStatus.open
    assert row.recurrence == Recurrence.none
    assert row.done_at is None
    assert row.proof_url is None and row.proof_key is None
    assert row.previous_item_id is None
    assert row.authority is None  # nullable (spec D15)
    assert row.created_by is None


def test_compliance_enums_store_their_values(db):
    _u, s = _ctx(db)
    row = _item(db, s, recurrence=Recurrence.monthly, authority="FIRS")
    stored = db.execute(
        text("SELECT status, recurrence FROM compliance_items WHERE id = :i"),
        {"i": str(row.id)},
    ).one()
    assert stored.status == "open" and stored.recurrence == "monthly"


def test_previous_item_id_is_unique(db):
    _u, s = _ctx(db)
    first = _item(db, s, title="January VAT")
    _item(db, s, title="February VAT", previous_item_id=first.id)
    db.add(
        ComplianceItem(
            startup_id=s.id,
            title="February VAT again",
            due_on=date(2026, 12, 31),
            anchor_day=31,
            previous_item_id=first.id,
        )
    )
    # This uniqueness is what makes a second spawn a no-op (spec D16).
    with pytest.raises(IntegrityError):
        db.flush()


# --- tenancy ----------------------------------------------------------------------------------


def test_all_three_tables_die_with_their_startup(db):
    _u, s = _ctx(db)
    _progress(db, s)
    _asset(db, s)
    _item(db, s)
    db.delete(s)
    db.flush()
    # Queried, not db.get(): get() answers from the session's identity map and would hand back
    # the cached object even though the row is gone.
    assert db.query(LegalFormationProgress).filter_by(startup_id=s.id).count() == 0
    assert db.query(IpAsset).filter_by(startup_id=s.id).count() == 0
    assert db.query(ComplianceItem).filter_by(startup_id=s.id).count() == 0


def test_author_columns_are_nullable_so_a_departing_user_keeps_the_records():
    # SET NULL, not CASCADE: these records belong to the workspace, not to whoever typed them.
    # Asserted on the schema rather than by deleting a user — nothing in this repo deletes users.
    assert LegalFormationProgress.__table__.columns["completed_by"].nullable is True
    assert IpAsset.__table__.columns["created_by"].nullable is True
    assert ComplianceItem.__table__.columns["created_by"].nullable is True
