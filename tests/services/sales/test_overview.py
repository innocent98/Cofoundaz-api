from datetime import UTC, datetime

import pytest

from app.db.models.enums import MembershipRole, StageKind
from app.db.models.sales import Deal
from app.services.dashboard.kpis import kpi_metrics
from app.services.sales import stages as stages_svc
from app.services.sales.overview import overview, pipeline_kpi
from tests.factories import create_membership, create_startup, create_user


@pytest.fixture
def ws(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    stages = {st.name: st for st in stages_svc.ensure_stages(db, s.id)}
    return u, s, stages


def _deal(db, s, stage, value, name="D"):
    d = Deal(
        startup_id=s.id,
        name=name,
        value_minor=value,
        currency="NGN",
        stage_id=stage.id,
        last_activity_at=datetime.now(UTC),
    )
    db.add(d)
    db.flush()
    return d


def test_fresh_workspace_seeds_default_stages_with_zeros(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.flush()
    out = overview(db, s.id)
    assert out["total_pipeline_minor"] == 0
    assert out["weighted_forecast_minor"] == 0
    assert out["win_rate"] is None
    assert out["currency"] == "NGN"
    assert [r["name"] for r in out["stages"]] == [
        "New",
        "Contacted",
        "Qualified",
        "Proposal",
        "Negotiation",
        "Closed Won",
        "Closed Lost",
    ]
    assert all(r["deal_count"] == 0 and r["value_minor"] == 0 for r in out["stages"])
    assert set(out["stages"][0]) == {"stage_id", "name", "kind", "deal_count", "value_minor"}


def test_pipeline_and_weighted_forecast_use_open_stages_only(db, ws):
    _u, s, st = ws
    _deal(db, s, st["New"], 1_000)  # 10%
    _deal(db, s, st["Proposal"], 2_001)  # 60% -> 1200.6
    _deal(db, s, st["Proposal"], 1_000)
    _deal(db, s, st["Closed Won"], 9_999)
    _deal(db, s, st["Closed Lost"], 7_777)
    out = overview(db, s.id)
    assert out["total_pipeline_minor"] == 4_001
    assert out["weighted_forecast_minor"] == round(100 + 1200.6 + 600)
    by = {r["name"]: r for r in out["stages"]}
    assert by["Proposal"]["deal_count"] == 2 and by["Proposal"]["value_minor"] == 3_001
    assert by["Closed Won"]["kind"] == StageKind.won.value
    assert by["Contacted"]["deal_count"] == 0


def test_win_rate_is_won_over_closed(db, ws):
    _u, s, st = ws
    _deal(db, s, st["New"], 1)
    assert overview(db, s.id)["win_rate"] is None  # open deals alone: no closed deals
    _deal(db, s, st["Closed Won"], 1)
    _deal(db, s, st["Closed Won"], 1)
    _deal(db, s, st["Closed Lost"], 1)
    assert overview(db, s.id)["win_rate"] == pytest.approx(66.7, abs=0.1)


def test_archived_stage_is_excluded_and_tenants_isolated(db, ws):
    _u, s, st = ws
    _deal(db, s, st["New"], 500)
    other_owner = create_user(db)
    other = create_startup(db, owner=other_owner)
    db.flush()
    other_stages = {x.name: x for x in stages_svc.ensure_stages(db, other.id)}
    _deal(db, other, other_stages["New"], 9_000)
    st["Contacted"].archived_at = datetime.now(UTC)
    db.flush()
    out = overview(db, s.id)
    assert "Contacted" not in [r["name"] for r in out["stages"]]
    assert out["total_pipeline_minor"] == 500


def test_pipeline_kpi_matches_overview(db, ws):
    _u, s, st = ws
    _deal(db, s, st["New"], 1_234)
    assert pipeline_kpi(db, s.id) == 1_234
    assert overview(db, s.id)["total_pipeline_minor"] == 1_234


def test_kpi_statuses(db, ws):
    _u, s, st = ws
    founder = kpi_metrics(db, s, MembershipRole.founder)["pipeline"]
    assert founder["status"] == "no_data" and founder["value_minor"] is None
    assert "trend" in founder
    for role in (MembershipRole.mentor, MembershipRole.accountant, MembershipRole.investor):
        tile = kpi_metrics(db, s, role)["pipeline"]
        assert tile == {"status": "restricted", "value_minor": None, "currency": None}
    _deal(db, s, st["New"], 2_500)
    tile = kpi_metrics(db, s, MembershipRole.team_member)["pipeline"]
    assert tile["status"] == "ok"
    assert tile["value_minor"] == 2_500
    assert tile["currency"] == "NGN"
    assert tile["trend"] == {"delta": None, "series": []}


def test_kpi_with_only_closed_deals_is_ok_zero(db, ws):
    _u, s, st = ws
    _deal(db, s, st["Closed Won"], 5_000)
    tile = kpi_metrics(db, s, MembershipRole.founder)["pipeline"]
    assert tile["status"] == "ok" and tile["value_minor"] == 0
