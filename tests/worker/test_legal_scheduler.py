from datetime import UTC, date, datetime, timedelta

from app.db.models.enums import ComplianceStatus, IpAssetStatus, IpAssetType, Recurrence
from app.db.models.job import Job
from app.db.models.legal import ComplianceItem, IpAsset
from app.worker import scheduler
from app.worker.scheduler import SCHED_COMPLIANCE, SCHED_IP_RENEWAL, scheduler_tick
from tests.factories import create_membership, create_startup, create_user

NOW = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
TODAY = NOW.date()


def _ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    create_membership(db, u, s)
    db.flush()
    return u, s


def _item(db, s, *, days_out, **kw):
    kw.setdefault("title", "VAT filing")
    kw.setdefault("status", ComplianceStatus.open)
    due = TODAY + timedelta(days=days_out)
    row = ComplianceItem(startup_id=s.id, due_on=due, anchor_day=due.day, **kw)
    db.add(row)
    db.flush()
    return row


def _asset(db, s, *, days_out=None, status=IpAssetStatus.registered, **kw):
    kw.setdefault("type", IpAssetType.trademark)
    kw.setdefault("name", "Kolo (wordmark)")
    kw.setdefault("jurisdiction", "Nigeria")
    row = IpAsset(
        startup_id=s.id,
        status=status,
        renewal_on=None if days_out is None else TODAY + timedelta(days=days_out),
        **kw,
    )
    db.add(row)
    db.flush()
    return row


def _keys(due):
    return [(d.scope_key, d.period_key) for d in due]


def _legal_jobs(db, type_=None):
    """Only our jobs: the tick also fires missions, KPI snapshots and the health recompute
    for a freshly created workspace, so its return count is not ours to assert on."""
    types = [type_] if type_ else [SCHED_COMPLIANCE, SCHED_IP_RENEWAL]
    return db.query(Job).filter(Job.type.in_(types)).all()


# --- compliance selection ----------------------------------------------------------------------


def test_nothing_due_beyond_the_widest_window(db):
    _u, s = _ws(db)
    _item(db, s, days_out=20)
    assert scheduler._due_compliance(db, NOW) == []


def test_exactly_fourteen_days_out_yields_due14_only(db):
    _u, s = _ws(db)
    row = _item(db, s, days_out=14)
    due = scheduler._due_compliance(db, NOW)
    assert _keys(due) == [(str(row.id), "due14")]
    assert due[0].task_key == SCHED_COMPLIANCE
    assert due[0].also_claim == ()


def test_five_days_out_collapses_to_one_notification(db):
    _u, s = _ws(db)
    row = _item(db, s, days_out=5)
    due = scheduler._due_compliance(db, NOW)
    # Both windows qualify, but only the most urgent is enqueued (spec D21).
    assert _keys(due) == [(str(row.id), "due7")]
    # The looser one is still CLAIMED, so it cannot fire a day later.
    assert due[0].also_claim == ("due14",)


def test_already_overdue_when_created_collapses_to_one_overdue(db):
    _u, s = _ws(db)
    row = _item(db, s, days_out=-3)
    due = scheduler._due_compliance(db, NOW)
    assert _keys(due) == [(str(row.id), "overdue")]
    assert set(due[0].also_claim) == {"due1", "due7", "due14"}


def test_due_today_is_not_overdue(db):
    _u, s = _ws(db)
    row = _item(db, s, days_out=0)
    due = scheduler._due_compliance(db, NOW)
    # Today has not passed, so the most urgent qualifying window is due1, not overdue.
    assert _keys(due) == [(str(row.id), "due1")]


def test_a_done_item_is_never_due(db):
    _u, s = _ws(db)
    _item(db, s, days_out=3, status=ComplianceStatus.done)
    assert scheduler._due_compliance(db, NOW) == []


def test_a_soft_deleted_startup_is_skipped(db):
    _u, s = _ws(db)
    _item(db, s, days_out=3)
    s.deleted_at = NOW
    db.flush()
    assert scheduler._due_compliance(db, NOW) == []


def test_a_second_tick_enqueues_nothing(db):
    _u, s = _ws(db)
    _item(db, s, days_out=5)
    scheduler_tick(db, now=NOW)
    assert len(_legal_jobs(db)) == 1
    scheduler_tick(db, now=NOW)
    assert len(_legal_jobs(db)) == 1


def test_a_collapsed_threshold_does_not_fire_later(db):
    _u, s = _ws(db)
    row = _item(db, s, days_out=5)
    scheduler_tick(db, now=NOW)  # one due7, with due14 claimed alongside
    assert len(_legal_jobs(db)) == 1

    # Four days later the item is one day out: due1 is new, so it fires once more. due14 was
    # burned on the first pass, so it never appears.
    later = NOW + timedelta(days=4)
    scheduler_tick(db, now=later)
    jobs = _legal_jobs(db, SCHED_COMPLIANCE)
    assert len(jobs) == 2
    assert {j.payload["period_key"] for j in jobs} == {"due7", "due1"}
    assert str(row.id) in {j.payload["item_id"] for j in jobs}


# --- ip renewal selection ----------------------------------------------------------------------


def test_an_ip_renewal_inside_the_window_is_due(db):
    _u, s = _ws(db)
    row = _asset(db, s, days_out=7)
    due = scheduler._due_ip_renewals(db, NOW)
    assert _keys(due) == [(str(row.id), "due7")]
    assert due[0].task_key == SCHED_IP_RENEWAL
    assert due[0].also_claim == ("due14",)


def test_a_drafting_asset_never_fires(db):
    _u, s = _ws(db)
    _asset(db, s, days_out=3, status=IpAssetStatus.drafting)
    assert scheduler._due_ip_renewals(db, NOW) == []


def test_an_asset_with_no_renewal_date_never_fires(db):
    _u, s = _ws(db)
    _asset(db, s, days_out=None)
    assert scheduler._due_ip_renewals(db, NOW) == []


def test_a_passed_renewal_produces_nothing_at_all(db):
    _u, s = _ws(db)
    _asset(db, s, days_out=-30)
    # Already visible as derived_status "expired" with a "Renew now" action, so a notification
    # about something possibly expired for a year would be noise (spec D18). Note this needs a
    # LOWER bound of today: with plain `renewal_on <= today + N` windows, a long-past date would
    # match all three and fire a due1.
    assert scheduler._due_ip_renewals(db, NOW) == []


def test_a_renewal_due_today_still_fires(db):
    _u, s = _ws(db)
    row = _asset(db, s, days_out=0)
    due = scheduler._due_ip_renewals(db, NOW)
    assert _keys(due) == [(str(row.id), "due1")]


def test_scheduler_tick_includes_both_legal_sources(db):
    _u, s = _ws(db)
    _item(db, s, days_out=3)
    _asset(db, s, days_out=3)
    scheduler_tick(db, now=NOW)
    types = {j.type for j in _legal_jobs(db)}
    assert types == {SCHED_COMPLIANCE, SCHED_IP_RENEWAL}


def test_the_payload_carries_what_the_notification_needs(db):
    _u, s = _ws(db)
    row = _item(db, s, days_out=3, title="Annual return", authority="CAC")
    due = scheduler._due_compliance(db, NOW)
    payload = due[0].payload
    # Title and due date travel with the payload so a deleted row still renders (spec D19).
    assert payload["startup_id"] == str(s.id)
    assert payload["item_id"] == str(row.id)
    assert payload["title"] == "Annual return"
    assert payload["due_on"] == (TODAY + timedelta(days=3)).isoformat()
    assert payload["period_key"] == "due1" or payload["period_key"] == "due7"


def test_recurring_occurrences_get_their_own_claims(db):
    _u, s = _ws(db)
    first = _item(db, s, days_out=5, recurrence=Recurrence.monthly)
    second = _item(db, s, days_out=6, recurrence=Recurrence.monthly)
    # Each row is its own scope_key, so one occurrence firing never silences the next.
    due = scheduler._due_compliance(db, NOW)
    assert {d.scope_key for d in due} == {str(first.id), str(second.id)}
    scheduler_tick(db, now=NOW)
    assert len(_legal_jobs(db, SCHED_COMPLIANCE)) == 2


def test_today_comes_from_utc_not_the_scheduler_timezone(db):
    _u, s = _ws(db)
    _item(db, s, days_out=14)
    # The derived status and the reminders must agree on "today" (spec D4). `_due_compliance`
    # must therefore use the UTC date of `now`, not `_local(now)`.
    assert len(scheduler._due_compliance(db, NOW)) == 1
    assert scheduler._due_compliance(db, NOW)[0].period_key == "due14"
    assert date(2026, 7, 15) == NOW.astimezone(UTC).date()
