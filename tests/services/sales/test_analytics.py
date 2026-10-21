from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.errors import AppError
from app.db.models.enums import ClosedReason, MembershipRole
from app.db.models.sales import Deal
from app.schemas.sales import ActivityCreate, DealCreate
from app.services.sales import activities as activities_svc
from app.services.sales import analytics as svc
from app.services.sales import deals as deals_svc
from app.services.sales import stages as stages_svc
from tests.factories import create_membership, create_startup, create_user

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
START, END = date(2026, 10, 1), date(2026, 10, 31)


def _ws(db, role=MembershipRole.founder):
    u = create_user(db, email_verified_at=NOW)
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=role)
    db.flush()
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    return u, s, stages


def _deal(db, u, s, stages, *, stage="New", created=NOW, name="D", value=100_00):
    d = deals_svc.create_deal(
        db,
        startup_id=s.id,
        actor_id=u.id,
        data=DealCreate(name=name, value_minor=value, stage_id=stages["New"].id),
    )
    db.execute(update(Deal).where(Deal.id == d.id).values(created_at=created))
    db.flush()
    db.refresh(d)
    if stage != "New":
        _move(db, u, s, d, stages[stage])
    return d


def _move(db, u, s, d, stage, *, reason=None, closed=None):
    deals_svc.move_stage(
        db,
        startup_id=s.id,
        deal_id=d.id,
        stage_id=stage.id,
        actor_id=u.id,
        closed_reason=reason,
    )
    if closed is not None:
        db.execute(update(Deal).where(Deal.id == d.id).values(closed_at=closed))
        db.flush()
        db.refresh(d)


def test_range_defaults_and_validation():
    assert svc.resolve_range(None, None, today=date(2026, 10, 21)) == (
        date(2026, 9, 22),
        date(2026, 10, 21),
    )
    assert svc.resolve_range(date(2026, 1, 1), None, today=date(2026, 1, 5)) == (
        date(2026, 1, 1),
        date(2026, 1, 5),
    )
    with pytest.raises(AppError) as e:
        svc.resolve_range(date(2026, 10, 2), date(2026, 10, 1))
    assert e.value.field_errors[0]["field"] == "from"
    with pytest.raises(AppError) as e:
        svc.resolve_range(date(2025, 1, 1), date(2026, 10, 1))
    assert e.value.field_errors[0]["field"] == "to"
    svc.resolve_range(date(2025, 10, 1), date(2026, 9, 30))  # 365 days is fine


def test_empty_workspace_is_all_zeros_not_errors(db):
    _u, s, _st = _ws(db)
    out = svc.analytics(db, s.id, START, END, now=NOW)
    assert out["funnel"]["deals_created"] == 0
    assert [r["reached"] for r in out["funnel"]["stages"]] == [0] * 6
    assert out["avg_cycle_days"] is None
    assert out["win_loss"] == {"won": 0, "lost": 0, "win_rate_pct": None, "loss_reasons": []}
    assert out["won"] == {"count": 0, "value_minor": 0}
    assert len(out["monthly_revenue"]) == 12
    assert all(m["value_minor"] == 0 for m in out["monthly_revenue"])
    assert out["leaderboard"][0]["total"] == 0 and out["currency"] == "NGN"


def test_funnel_counts_by_position_and_excludes_lost_rung(db):
    u, s, st = _ws(db)
    _deal(db, u, s, st, name="stays new")
    _deal(db, u, s, st, name="qualified", stage="Qualified")  # skipped Contacted: counts as reached
    d_won = _deal(db, u, s, st, name="won")
    _move(db, u, s, d_won, st["Proposal"])
    _move(db, u, s, d_won, st["Closed Won"])
    d_lost = _deal(db, u, s, st, name="lost late")
    _move(db, u, s, d_lost, st["Negotiation"])
    _move(db, u, s, d_lost, st["Closed Lost"], reason=ClosedReason.price)
    old = _deal(db, u, s, st, name="before range", created=NOW - timedelta(days=60))
    _move(db, u, s, old, st["Closed Won"])
    f = svc.analytics(db, s.id, START, END, now=NOW)["funnel"]
    by_name = {r["name"]: r for r in f["stages"]}
    assert f["deals_created"] == 4  # the out-of-range deal is not in the cohort
    assert [r["name"] for r in f["stages"]] == [
        "New",
        "Contacted",
        "Qualified",
        "Proposal",
        "Negotiation",
        "Closed Won",
    ]
    assert by_name["New"]["reached"] == 4
    assert by_name["Contacted"]["reached"] == 3  # qualified + won + lost-at-negotiation
    assert by_name["Qualified"]["reached"] == 3
    assert by_name["Proposal"]["reached"] == 2
    assert by_name["Negotiation"]["reached"] == 2  # a deal lost at Negotiation reached it
    assert by_name["Closed Won"]["reached"] == 1  # the lost deal does NOT count as reaching Won
    assert by_name["New"]["conversion_from_previous_pct"] is None
    assert by_name["Contacted"]["conversion_from_previous_pct"] == 75.0
    assert by_name["Closed Won"]["conversion_from_previous_pct"] == 50.0


def test_funnel_conversion_is_null_after_an_empty_stage(db):
    u, s, st = _ws(db)
    _deal(db, u, s, st)
    rows = svc.analytics(db, s.id, START, END, now=NOW)["funnel"]["stages"]
    assert rows[1]["reached"] == 0 and rows[1]["conversion_from_previous_pct"] == 0.0
    assert rows[2]["conversion_from_previous_pct"] is None  # 0 of 0


def test_cycle_days_and_won_totals_only_count_won_in_range(db):
    u, s, st = _ws(db)
    a = _deal(db, u, s, st, name="a", created=datetime(2026, 10, 1, tzinfo=UTC), value=200_00)
    _move(db, u, s, a, st["Closed Won"], closed=datetime(2026, 10, 11, tzinfo=UTC))
    b = _deal(db, u, s, st, name="b", created=datetime(2026, 10, 1, tzinfo=UTC), value=300_00)
    _move(db, u, s, b, st["Closed Won"], closed=datetime(2026, 10, 6, 12, tzinfo=UTC))
    c = _deal(db, u, s, st, name="c", created=datetime(2026, 9, 1, tzinfo=UTC))
    _move(
        db, u, s, c, st["Closed Won"], closed=datetime(2026, 9, 20, tzinfo=UTC)
    )  # won before the range
    open_deal = _deal(db, u, s, st, name="open")
    assert open_deal.closed_at is None
    out = svc.analytics(db, s.id, START, END, now=NOW)
    assert out["avg_cycle_days"] == 7.8  # (10 + 5.5) / 2
    assert out["won"] == {"count": 2, "value_minor": 500_00}


def test_range_edges_are_inclusive_whole_utc_days(db):
    u, s, st = _ws(db)
    a = _deal(db, u, s, st, created=datetime(2026, 10, 1, tzinfo=UTC))
    _move(db, u, s, a, st["Closed Won"], closed=datetime(2026, 10, 31, 23, 59, tzinfo=UTC))
    b = _deal(db, u, s, st, created=datetime(2026, 10, 1, tzinfo=UTC))
    _move(db, u, s, b, st["Closed Won"], closed=datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    assert svc.analytics(db, s.id, START, END, now=NOW)["won"]["count"] == 1


def test_win_loss_reasons_ranked_and_win_rate(db):
    u, s, st = _ws(db)
    for reason, n in ((ClosedReason.price, 2), (ClosedReason.timing, 1), (ClosedReason.ghosted, 2)):
        for _ in range(n):
            d = _deal(db, u, s, st, value=10_00)
            _move(
                db,
                u,
                s,
                d,
                st["Closed Lost"],
                reason=reason,
                closed=datetime(2026, 10, 5, tzinfo=UTC),
            )
    w = _deal(db, u, s, st)
    _move(db, u, s, w, st["Closed Won"], closed=datetime(2026, 10, 5, tzinfo=UTC))
    late = _deal(db, u, s, st)
    _move(
        db,
        u,
        s,
        late,
        st["Closed Lost"],
        reason=ClosedReason.other,
        closed=datetime(2026, 12, 1, tzinfo=UTC),
    )
    wl = svc.analytics(db, s.id, START, END, now=NOW)["win_loss"]
    assert wl["won"] == 1 and wl["lost"] == 5 and wl["win_rate_pct"] == 16.7
    assert wl["loss_reasons"] == [
        {"reason": "ghosted", "count": 2, "value_minor": 20_00},
        {"reason": "price", "count": 2, "value_minor": 20_00},
        {"reason": "timing", "count": 1, "value_minor": 10_00},
    ]


def test_leaderboard_lists_every_sales_member_and_ignores_others(db):
    u, s, st = _ws(db)
    tm = create_user(db, email_verified_at=NOW)
    create_membership(db, tm, s, role=MembershipRole.team_member)
    mentor = create_user(db, email_verified_at=NOW)
    create_membership(db, mentor, s, role=MembershipRole.mentor)
    other_u, other_s, _ = _ws(db)
    db.flush()
    d = _deal(db, u, s, st)

    def log(author, kind, when=datetime(2026, 10, 5, tzinfo=UTC), deal=d):
        activities_svc.create_activity(
            db,
            startup_id=s.id,
            author_id=author.id,
            data=ActivityCreate(kind=kind, body="x", deal_id=deal.id, occurred_at=when),
        )

    for kind in ("call", "call", "email", "note"):
        log(tm, kind)
    log(u, "meeting")
    log(tm, "call", when=datetime(2026, 9, 1, tzinfo=UTC))  # out of range
    rows = svc.analytics(db, s.id, START, END, now=NOW)["leaderboard"]
    assert [(r["user_id"], r["total"]) for r in rows] == [(str(tm.id), 4), (str(u.id), 1)]
    assert (rows[0]["calls"], rows[0]["emails"], rows[0]["meetings"], rows[0]["notes"]) == (
        2,
        1,
        0,
        1,
    )
    assert rows[1]["meetings"] == 1
    assert str(mentor.id) not in {r["user_id"] for r in rows}
    assert svc.leaderboard(db, other_s.id, *svc._bounds(START, END))[0]["total"] == 0


def test_monthly_revenue_is_12_months_zero_filled_oldest_first(db):
    u, s, st = _ws(db)
    for when, value in (
        (datetime(2026, 10, 3, tzinfo=UTC), 100_00),
        (datetime(2026, 10, 30, tzinfo=UTC), 50_00),
        (datetime(2025, 11, 1, tzinfo=UTC), 70_00),  # first month of the window
        (datetime(2025, 10, 31, 23, tzinfo=UTC), 999_00),  # just outside
    ):
        d = _deal(db, u, s, st, value=value)
        _move(db, u, s, d, st["Closed Won"], closed=when)
    reopened = _deal(db, u, s, st, value=5_000_00)
    _move(db, u, s, reopened, st["Closed Won"], closed=datetime(2026, 8, 1, tzinfo=UTC))
    _move(db, u, s, reopened, st["Negotiation"])  # reopened: no longer counts as won
    rev = svc.monthly_revenue(db, s.id, now=NOW)
    assert [m["month"] for m in rev][0] == "2025-11" and rev[-1]["month"] == "2026-10"
    assert len(rev) == 12
    by = {m["month"]: m for m in rev}
    assert by["2026-10"] == {"month": "2026-10", "value_minor": 150_00, "deal_count": 2}
    assert by["2025-11"]["value_minor"] == 70_00
    assert by["2026-08"]["value_minor"] == 0
    assert sum(m["value_minor"] for m in rev) == 220_00


def test_other_workspaces_deals_never_leak(db):
    u, s, st = _ws(db)
    u2, s2, st2 = _ws(db)
    d = _deal(db, u2, s2, st2, value=900_00)
    _move(db, u2, s2, d, st2["Closed Won"], closed=datetime(2026, 10, 5, tzinfo=UTC))
    out = svc.analytics(db, s.id, START, END, now=NOW)
    assert out["funnel"]["deals_created"] == 0 and out["won"]["count"] == 0
    assert sum(m["value_minor"] for m in out["monthly_revenue"]) == 0
