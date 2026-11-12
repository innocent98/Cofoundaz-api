"""Analytics & Reports slice A: catalog, per-metric authorisation (D-ANA-4), Overview, By hub,
batch resolve, custom metrics (D-ANA-2), periods, cache, nightly snapshot."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.enums import (
    MarketingMetricName,
    MembershipRole,
    TransactionDirection,
    TransactionSource,
)
from app.db.models.finance import Transaction
from app.db.models.kpi_snapshot import KpiSnapshot
from app.db.models.marketing import MarketingMetric
from app.db.models.startup import Startup
from app.services.analytics import periods as per
from app.services.analytics import service as svc
from app.services.analytics.snapshots import capture
from tests.factories import (
    create_health_score,
    create_membership,
    create_mission,
    create_mission_task,
    create_startup,
    create_user,
)

BASE = "/api/v1/analytics"
ALLOWED = {MembershipRole.founder, MembershipRole.team_member, MembershipRole.accountant}


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def team(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    db.flush()
    members = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, {r: m[1] for r, m in members.items()}, {r: m[0] for r, m in members.items()}


@pytest.fixture(autouse=True)
def _clean_cache(team):
    svc.invalidate(team[0].id)
    yield
    svc.invalidate(team[0].id)


def _tx(db, s, d, amount, direction, category=None):
    db.add(
        Transaction(
            startup_id=s.id,
            date=d,
            description="x",
            category=category,
            amount_minor=amount,
            currency="NGN",
            direction=direction,
            source=TransactionSource.manual,
        )
    )


def _seed_finance(db, s):
    today = date.today()
    _tx(db, s, today - timedelta(days=3), 500_000, TransactionDirection.inflow)
    _tx(db, s, today - timedelta(days=4), 200_000, TransactionDirection.outflow)
    _tx(db, s, today - timedelta(days=40), 100_000, TransactionDirection.inflow)
    _tx(db, s, today - timedelta(days=41), 300_000, TransactionDirection.outflow)
    _tx(db, s, today - timedelta(days=2), 9_000_000, TransactionDirection.inflow, "Funding")
    db.commit()


def _get(client, hdr, path, **params):
    return client.get(f"{BASE}{path}", headers=hdr, params=params)


# ---- roles ----
def test_roles_matrix(client, team):
    _s, hdr, _u = team
    for role, h in hdr.items():
        for path in ("/metrics-catalog", "/overview", "/by-hub", "/custom-metrics"):
            r = _get(client, h, path)
            if path == "/custom-metrics":
                ok = role in {MembershipRole.founder, MembershipRole.team_member}
            else:
                ok = role in ALLOWED
            assert r.status_code == (200 if ok else 403), (role, path, r.status_code)


def test_unauthenticated_is_rejected(client):
    assert client.get(f"{BASE}/overview").status_code in (401, 403)


# ---- per-metric authorisation ----
def test_accountant_sees_finance_only(client, db, team):
    s, hdr, _ = team
    _seed_finance(db, s)
    ac = hdr[MembershipRole.accountant]
    cat = _get(client, ac, "/metrics-catalog").json()["data"]["hubs"]
    open_hubs = {h["hub"] for h in cat if not h["locked"]}
    assert open_hubs == {"finance"}
    assert all(h["metrics"] == [] for h in cat if h["locked"])
    ov = _get(client, ac, "/overview").json()["data"]
    assert {c["hub"] for c in ov["cards"]} == {"finance"}
    assert [h["hub"] for h in ov["hubs"]] == ["finance"]
    r = _get(client, ac, "/by-hub", hub="sales")
    assert r.status_code == 403


def test_locked_metric_never_returns_a_value_or_hits_cache(client, db, team):
    s, hdr, _ = team
    _seed_finance(db, s)
    body = {"metrics": [{"key": "sales.pipeline_value"}, {"key": "finance.revenue"}]}
    fo = client.post(f"{BASE}/metrics/resolve", headers=hdr[MembershipRole.founder], json=body)
    assert fo.status_code == 200
    # warm the cache as the founder, then ask as the accountant
    ac = client.post(f"{BASE}/metrics/resolve", headers=hdr[MembershipRole.accountant], json=body)
    m = {x["key"]: x for x in ac.json()["data"]["metrics"]}
    assert m["sales.pipeline_value"] == {
        "key": "sales.pipeline_value",
        "label": "Open pipeline",
        "hub": "sales",
        "status": "locked",
        "needs": "sales",
        "needs_label": "Sales",
    }
    assert m["finance.revenue"]["status"] == "ok"


def test_team_member_matches_founder_hubs(client, team):
    _s, hdr, _ = team
    f = _get(client, hdr[MembershipRole.founder], "/metrics-catalog").json()["data"]
    t = _get(client, hdr[MembershipRole.team_member], "/metrics-catalog").json()["data"]
    assert f == t


# ---- numbers ----
def test_revenue_excludes_funding_and_compares_previous_period(client, db, team):
    s, hdr, _ = team
    _seed_finance(db, s)
    r = client.post(
        f"{BASE}/metrics/resolve",
        headers=hdr[MembershipRole.founder],
        json={
            "metrics": [
                {"key": "finance.revenue", "range": "30d", "compare": "previous", "series": True}
            ]
        },
    )
    m = r.json()["data"]["metrics"][0]
    assert m["status"] == "ok" and m["value"] == 500_000  # funding 9,000,000 is not revenue
    assert m["currency"] == "NGN" and m["unit"] == "money"
    assert m["compare"]["value"] == 100_000
    assert m["compare"]["delta"] == 400_000 and m["compare"]["delta_pct"] == 400.0
    assert len(m["series"]) <= 12 and sum(x["value"] or 0 for x in m["series"]) == 500_000


def test_empty_workspace_is_no_data_not_zero(client, team):
    _s, hdr, _ = team
    ov = _get(client, hdr[MembershipRole.founder], "/overview").json()["data"]
    assert ov["cards"], "headline cards are present even when empty"
    assert all(c["status"] == "no_data" and c["value"] is None for c in ov["cards"])
    assert ov["insights"] == []


def test_overview_cards_chart_and_insights(client, db, team):
    s, hdr, _ = team
    _seed_finance(db, s)
    ov = _get(client, hdr[MembershipRole.founder], "/overview", range="30d").json()["data"]
    keys = [c["key"] for c in ov["cards"]]
    assert keys[:3] == ["finance.revenue", "finance.net_burn", "finance.runway_months"]
    assert ov["chart"]["series"][0]["key"] == "finance.revenue"
    assert any("Revenue grew" in n["text"] for n in ov["insights"])
    assert ov["period"]["to"] == date.today().isoformat()
    assert ov["compare_period"] is not None


def test_marketing_ctr_and_undefined_without_impressions(client, db, team):
    s, hdr, _ = team
    d = date.today() - timedelta(days=2)
    db.add_all(
        [
            MarketingMetric(
                startup_id=s.id, ts=d, metric=MarketingMetricName.impressions, value=1000
            ),
            MarketingMetric(startup_id=s.id, ts=d, metric=MarketingMetricName.clicks, value=50),
        ]
    )
    db.commit()
    body = {"metrics": [{"key": "marketing.ctr"}, {"key": "marketing.clicks"}]}
    m = client.post(f"{BASE}/metrics/resolve", headers=hdr[MembershipRole.founder], json=body)
    got = {x["key"]: x for x in m.json()["data"]["metrics"]}
    assert got["marketing.ctr"]["value"] == 5.0
    assert got["marketing.clicks"]["value"] == 50


def test_mission_tasks_and_health(client, db, team):
    s, hdr, _ = team
    create_health_score(db, s, score=72)
    mis = create_mission(db, s)
    create_mission_task(
        db,
        mis,
        status=__import__("app.db.models.enums", fromlist=["x"]).MissionTaskStatus.done,
        completed_at=datetime.now(UTC),
    )
    db.commit()
    body = {"metrics": [{"key": "health.score"}, {"key": "execution.tasks_done"}]}
    got = {
        x["key"]: x
        for x in client.post(
            f"{BASE}/metrics/resolve", headers=hdr[MembershipRole.founder], json=body
        ).json()["data"]["metrics"]
    }
    assert got["health.score"]["value"] == 72
    assert got["execution.tasks_done"]["value"] == 1


def test_by_hub_lists_visible_hubs_only(client, db, team):
    s, hdr, _ = team
    _seed_finance(db, s)
    f = _get(client, hdr[MembershipRole.founder], "/by-hub").json()["data"]
    assert [h["hub"] for h in f["hubs"]][:3] == ["finance", "sales", "marketing"]
    one = _get(client, hdr[MembershipRole.founder], "/by-hub", hub="finance").json()["data"]
    assert len(one["hubs"]) == 1 and len(one["hubs"][0]["tiles"]) == 3
    assert _get(client, hdr[MembershipRole.founder], "/by-hub", hub="nope").status_code == 422


# ---- periods ----
def test_period_validation(client, team):
    _s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    assert _get(client, h, "/overview", range="bogus").status_code == 422
    assert _get(client, h, "/overview", compare="bogus").status_code == 422
    r = _get(client, h, "/overview", range="custom", **{"from": "2026-01-01", "to": "2026-12-31"})
    assert r.status_code == 200
    r = _get(client, h, "/overview", range="custom", **{"from": "2026-06-01", "to": "2026-01-01"})
    assert r.status_code == 422
    r = _get(client, h, "/overview", range="custom", **{"from": "2020-01-01", "to": "2026-01-01"})
    assert r.status_code == 422
    assert _get(client, h, "/overview", range="custom").status_code == 422


def test_period_helpers():
    t = date(2026, 3, 31)
    assert per.resolve("this_month", today=t) == per.Period(date(2026, 3, 1), t)
    assert per.resolve("last_month", today=t) == per.Period(date(2026, 2, 1), date(2026, 2, 28))
    assert per.resolve("12m", today=t).start == date(2025, 4, 1)
    p = per.Period(date(2026, 3, 1), date(2026, 3, 30))
    assert per.comparison(p, "previous") == per.Period(date(2026, 1, 30), date(2026, 2, 28))
    assert per.comparison(p, "none") is None
    assert len(per.buckets(per.Period(date(2026, 1, 1), date(2026, 12, 31)))) == 12
    assert len(per.buckets(per.Period(date(2026, 3, 1), date(2026, 3, 7)))) == 7
    assert len(per.buckets(per.Period(date(2026, 3, 1), date(2026, 3, 30)))) <= 12


def test_batch_limit_and_unknown_metric(client, team):
    _s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    too_many = {"metrics": [{"key": "finance.revenue"}] * 25}
    r = client.post(f"{BASE}/metrics/resolve", headers=h, json=too_many)
    assert r.status_code == 422
    got = client.post(f"{BASE}/metrics/resolve", headers=h, json={"metrics": [{"key": "x.y"}]})
    assert got.json()["data"]["metrics"][0]["status"] == "unknown"
    extra = {"metrics": [{"key": "finance.revenue", "evil": 1}]}
    r = client.post(f"{BASE}/metrics/resolve", headers=h, json=extra)
    assert r.status_code == 422


# ---- cache ----
def test_resolved_numbers_are_cached_five_minutes(client, db, team):
    s, hdr, _ = team
    _seed_finance(db, s)
    h = hdr[MembershipRole.founder]
    body = {"metrics": [{"key": "finance.revenue"}]}
    first = client.post(f"{BASE}/metrics/resolve", headers=h, json=body).json()["data"]
    _tx(db, s, date.today(), 1_000_000, TransactionDirection.inflow)
    db.commit()
    second = client.post(f"{BASE}/metrics/resolve", headers=h, json=body).json()["data"]
    assert second == first  # served from the cache
    svc.invalidate(s.id)
    third = client.post(f"{BASE}/metrics/resolve", headers=h, json=body).json()["data"]
    assert third["metrics"][0]["value"] == 1_500_000


# ---- custom metrics ----
def test_custom_metric_lifecycle_and_catalog(client, team):
    s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    r = client.post(
        f"{BASE}/custom-metrics", headers=h, json={"label": "Active users", "unit": "count"}
    )
    assert r.status_code == 201
    m = r.json()["data"]
    assert m["key"] == "custom.active_users" and m["points"] == []
    dup = client.post(
        f"{BASE}/custom-metrics", headers=h, json={"label": "Active users", "unit": "count"}
    )
    assert dup.json()["data"]["key"] == "custom.active_users_2"
    pts = {"points": [{"period": "2026-09", "value": 120}, {"period": "2026-10", "value": 180}]}
    r = client.put(f"{BASE}/custom-metrics/{m['id']}/points", headers=h, json=pts)
    assert r.status_code == 200 and [p["value"] for p in r.json()["data"]["points"]] == [120, 180]
    pts2 = {"points": [{"period": "2026-10", "value": 200}]}
    r = client.put(f"{BASE}/custom-metrics/{m['id']}/points", headers=h, json=pts2)
    assert [p["value"] for p in r.json()["data"]["points"]] == [120, 200]  # upsert, not append
    cat = _get(client, h, "/metrics-catalog").json()["data"]["hubs"]
    custom = next(x for x in cat if x["hub"] == "custom")
    assert custom["metrics"][0]["entered_by_you"] is True
    res = client.post(
        f"{BASE}/metrics/resolve",
        headers=h,
        json={
            "metrics": [
                {
                    "key": "custom.active_users",
                    "range": "custom",
                    "from": "2026-10-01",
                    "to": "2026-10-31",
                    "compare": "previous",
                }
            ]
        },
    ).json()["data"]["metrics"][0]
    assert res["value"] == 200 and res["compare"]["value"] == 120
    assert res["source_note"] == "Entered by you"
    deleted = client.delete(f"{BASE}/custom-metrics/{m['id']}", headers=h)
    assert deleted.status_code == 200
    gone = client.post(
        f"{BASE}/metrics/resolve", headers=h, json={"metrics": [{"key": "custom.active_users"}]}
    ).json()["data"]["metrics"][0]
    assert gone["status"] == "unknown"  # a retired key renders, never crashes


def test_custom_metric_validation_and_isolation(client, db, team):
    s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    mk = client.post(
        f"{BASE}/custom-metrics", headers=h, json={"label": "MRR", "unit": "money"}
    ).json()["data"]
    url = f"{BASE}/custom-metrics/{mk['id']}/points"
    bad = [
        {"points": [{"period": "2026-13", "value": 1}]},
        {"points": [{"period": "2099-01", "value": 1}]},
        {"points": [{"period": "2026-01", "value": "abc"}]},
        {"points": [{"period": "2026-01", "value": -5}]},
        {"points": [{"period": "2026-01", "value": 1.5}]},  # money is whole kobo
        {"points": [{"period": "2026-01", "value": 1}, {"period": "2026-01-15", "value": 2}]},
    ]
    for body in bad:
        rejected = client.put(url, headers=h, json=body)
        assert rejected.status_code == 422, body
    blank = client.post(f"{BASE}/custom-metrics", headers=h, json={"label": " ", "unit": "count"})
    bogus = client.post(f"{BASE}/custom-metrics", headers=h, json={"label": "x", "unit": "bogus"})
    assert blank.status_code == 422
    assert bogus.status_code == 422
    # another workspace cannot touch it: uniform 404
    owner2 = create_user(db, email_verified_at=datetime.now(UTC))
    s2 = create_startup(db, owner=owner2, name="Other")
    db.flush()
    _u2, h2 = _member(db, s2, MembershipRole.founder)
    db.commit()
    ok = {"points": [{"period": "2026-01", "value": 1}]}
    put_other = client.put(url, headers=h2, json=ok)
    delete_other = client.delete(f"{BASE}/custom-metrics/{mk['id']}", headers=h2)
    assert put_other.status_code == 404
    assert delete_other.status_code == 404
    assert _get(client, h2, "/custom-metrics").json()["data"]["metrics"] == []


def test_accountant_cannot_write_or_read_custom(client, team):
    _s, hdr, _ = team
    ac = hdr[MembershipRole.accountant]
    denied = client.post(f"{BASE}/custom-metrics", headers=ac, json={"label": "u", "unit": "count"})
    assert denied.status_code == 403
    assert _get(client, ac, "/custom-metrics").status_code == 403


def test_custom_metric_cap(client, team):
    _s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    for i in range(20):
        made = client.post(
            f"{BASE}/custom-metrics", headers=h, json={"label": f"m{i}", "unit": "count"}
        )
        assert made.status_code == 201
    over = client.post(
        f"{BASE}/custom-metrics", headers=h, json={"label": "one more", "unit": "count"}
    )
    assert over.status_code == 422


# ---- snapshots ----
def test_nightly_snapshot_stores_point_metrics_and_feeds_compare(db, team, client):
    s, hdr, _ = team
    create_health_score(db, s, score=64)
    db.commit()
    startup = db.get(Startup, s.id)
    stored = capture(db, startup)
    db.commit()
    assert "health.score" in stored and "finance.revenue" not in stored  # flows are not stored
    row = db.query(KpiSnapshot).filter_by(startup_id=s.id, metric="health.score").one()
    assert float(row.value) == 64 and row.period == date.today()
    capture(db, startup)  # idempotent per day
    db.commit()
    assert db.query(KpiSnapshot).filter_by(startup_id=s.id, metric="health.score").count() == 1
    # yesterday's snapshot becomes the comparison value for the point metric
    db.add(
        KpiSnapshot(
            startup_id=s.id,
            metric="health.score",
            period=date.today() - timedelta(days=30),
            value=50,
        )
    )
    db.commit()
    got = client.post(
        f"{BASE}/metrics/resolve",
        headers=hdr[MembershipRole.founder],
        json={
            "metrics": [
                {"key": "health.score", "range": "30d", "compare": "previous", "series": True}
            ]
        },
    ).json()["data"]["metrics"][0]
    assert got["value"] == 64
    assert got["compare"]["value"] == 50 and got["compare"]["delta"] == 14
    assert got["series"] and got["series"][-1]["value"] == 64


def test_runway_states_are_honest(client, db, team):
    s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    today = date.today()
    body = {"metrics": [{"key": "finance.runway_months"}]}

    def runway():
        svc.invalidate(s.id)
        return client.post(f"{BASE}/metrics/resolve", headers=h, json=body).json()["data"][
            "metrics"
        ][0]

    assert runway()["status"] == "no_data"  # no ledger at all
    _tx(db, s, today - timedelta(days=5), 5_000_000, TransactionDirection.inflow)
    _tx(db, s, today - timedelta(days=5), 1_000_000, TransactionDirection.outflow)
    db.commit()
    got = runway()
    assert got["status"] == "not_burning" and got["value"] is None  # profitable
    _tx(db, s, today - timedelta(days=5), 20_000_000, TransactionDirection.outflow)
    db.commit()
    got = runway()
    assert got["status"] == "out_of_cash" and got["value"] == 0  # burning, cash below zero


def test_no_data_is_cached_briefly_and_expires(client, db, team):
    from app.core.redis import get_redis

    s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    body = {"metrics": [{"key": "finance.revenue"}]}

    def first():
        return client.post(f"{BASE}/metrics/resolve", headers=h, json=body).json()["data"][
            "metrics"
        ][0]

    assert first()["status"] == "no_data"
    _seed_finance(db, s)
    assert first()["status"] == "no_data"  # served from the short cache
    keys = list(get_redis().scan_iter(match=f"analytics:v1:{s.id}:finance.revenue:*"))
    assert keys and 0 < get_redis().ttl(keys[0]) <= svc.NO_DATA_CACHE_SECONDS
    get_redis().delete(*keys)  # what 30 seconds of expiry does
    assert first()["value"] == 500_000


def test_custom_metric_key_is_not_an_existence_oracle(client, team):
    _s, hdr, _ = team
    fo, ac = hdr[MembershipRole.founder], hdr[MembershipRole.accountant]
    client.post(f"{BASE}/custom-metrics", headers=fo, json={"label": "Secret ARR", "unit": "money"})
    ask = {"metrics": [{"key": "custom.secret_arr"}, {"key": "custom.not_a_thing"}]}
    got = client.post(f"{BASE}/metrics/resolve", headers=ac, json=ask).json()["data"]["metrics"]
    assert got[0] == {
        "key": "custom.secret_arr",
        "status": "unknown",
        "message": "Metric no longer available.",
    }
    assert got[1]["status"] == "unknown" and "label" not in got[1]
    assert "Secret ARR" not in str(got)
    # the founder still resolves it
    ok = client.post(f"{BASE}/metrics/resolve", headers=fo, json=ask).json()["data"]["metrics"]
    assert ok[0]["label"] == "Secret ARR"


@pytest.mark.parametrize(
    "params",
    [
        {"range": "custom", "from": "0001-01-01", "to": "0001-06-01", "compare": "previous"},
        {"range": "custom", "from": "1999-12-31", "to": "2026-01-01"},
        {"range": "custom", "from": "2026-01-01", "to": "9999-12-31"},
        {"range": "custom", "from": "9998-01-01", "to": "9999-12-31", "compare": "year"},
        {"range": "custom", "from": "2026-01-01", "to": "2028-01-01"},
    ],
)
def test_extreme_dates_are_422_not_500(client, team, params):
    _s, hdr, _ = team
    h = hdr[MembershipRole.founder]
    assert _get(client, h, "/overview", **params).status_code == 422
    assert _get(client, h, "/by-hub", **params).status_code == 422
    spec = {"key": "finance.revenue", "range": "custom", "from": params["from"], "to": params["to"]}
    body = {"metrics": [spec | {"compare": params.get("compare", "none"), "series": True}]}
    r = client.post(f"{BASE}/metrics/resolve", headers=h, json=body)
    assert r.status_code == 422


def test_period_bounds_and_compare_stay_inside_range():
    t = date(2026, 3, 31)
    edge = per.resolve("custom", date(2000, 1, 1), date(2000, 1, 31), today=t)
    assert per.comparison(edge, "previous") is None  # would start in 1999: nothing to compare
    assert per.comparison(edge, "year") is None
    ahead = per.resolve("custom", date(2026, 1, 1), t + timedelta(days=365), today=t)
    assert len(per.buckets(ahead)) <= 12
    longest = per.resolve("custom", date(2024, 4, 1), date(2026, 3, 31), today=t)  # 24 months
    b = per.buckets(longest)
    assert len(b) <= 12 and b[0].start == longest.start and b[-1].end == longest.end
    with pytest.raises(Exception, match="Pick an end date"):
        per.resolve("custom", date(2026, 1, 1), t + timedelta(days=367), today=t)


def test_heavy_reads_are_rate_limited_per_workspace(client, db, team, monkeypatch):
    s, hdr, _ = team
    monkeypatch.setattr(svc, "RATE_LIMIT_PER_MINUTE", 3)
    fo = hdr[MembershipRole.founder]
    codes = [_get(client, fo, "/overview").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    body = _get(client, fo, "/by-hub").json()
    assert body["error"]["code"] == "RATE_LIMITED"
    # another workspace is unaffected, and the catalog (a cheap read) is never limited
    owner2 = create_user(db, email_verified_at=datetime.now(UTC))
    s2 = create_startup(db, owner=owner2, name="Other")
    db.flush()
    _u2, h2 = _member(db, s2, MembershipRole.founder)
    db.commit()
    assert _get(client, h2, "/overview").status_code == 200
    assert _get(client, fo, "/metrics-catalog").status_code == 200
