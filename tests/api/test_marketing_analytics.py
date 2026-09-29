from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.security import create_access_token
from app.db.models.enums import CampaignObjective, CampaignStatus, MembershipRole, StartupStage
from app.db.models.marketing import Campaign
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"
NON_MARKETING_ROLES = [MembershipRole.mentor, MembershipRole.investor]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def test_ingest_metrics_bulk(client, db):
    _u, _s, h = _member(db)
    body = {
        "points": [
            {"ts": "2026-09-01", "channel": "email", "metric": "visits", "value": 1200},
            {"ts": "2026-09-01", "channel": "email", "metric": "conversions", "value": 30},
        ]
    }
    resp = client.post(f"{BASE}/metrics", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["created"] == 2


def test_ingest_rejects_negative_value(client, db):
    _u, _s, h = _member(db)
    resp = client.post(
        f"{BASE}/metrics",
        json={"points": [{"ts": "2026-09-01", "metric": "visits", "value": -5}]},
        headers=h,
    )
    assert resp.status_code == 422, resp.text


def test_ingest_rejects_value_over_int32(client, db):
    # A value above Postgres int32 max must 422 at validation, not 500 at flush.
    _u, _s, h = _member(db)
    resp = client.post(
        f"{BASE}/metrics",
        json={"points": [{"ts": "2026-09-01", "metric": "visits", "value": 3_000_000_000}]},
        headers=h,
    )
    assert resp.status_code == 422, resp.text


def test_ingest_rejects_foreign_campaign(client, db):
    _u, _s, h = _member(db)
    _u2, s2, _h2 = _member(db)  # a different startup; build a campaign there
    other = Campaign(
        startup_id=s2.id, name="X", objective=CampaignObjective.leads, status=CampaignStatus.draft
    )
    db.add(other)
    db.flush()
    resp = client.post(
        f"{BASE}/metrics",
        json={
            "points": [
                {"ts": "2026-09-01", "campaign_id": str(other.id), "metric": "clicks", "value": 10}
            ]
        },
        headers=h,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_ingest_rbac_forbidden(client, db, role):
    _f, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)
    resp = client.post(f"{BASE}/metrics", json={"points": []}, headers=h)
    assert resp.status_code == 403, resp.text


def _seed(client, db, h, points):
    resp = client.post(f"{BASE}/metrics", json={"points": points}, headers=h)
    assert resp.status_code == 200, resp.text


def test_analytics_empty_is_zeroed(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/analytics", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["traffic_by_week"] == []
    assert data["cac_by_channel"] == []
    assert data["leaderboard"] == []
    assert data["funnel"] == {
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "click_through_rate": 0.0,
        "conversion_rate": 0.0,
    }


def test_analytics_cac_and_funnel(client, db):
    _u, _s, h = _member(db)
    today = date.today().isoformat()
    _seed(
        client,
        db,
        h,
        [
            {"ts": today, "channel": "paid_social", "metric": "spend", "value": 64000},
            {"ts": today, "channel": "paid_social", "metric": "conversions", "value": 20},
            {"ts": today, "metric": "impressions", "value": 1000},
            {"ts": today, "metric": "clicks", "value": 100},
            {"ts": today, "metric": "conversions", "value": 20},
        ],
    )
    data = client.get(f"{BASE}/analytics?range=30d", headers=h).json()["data"]
    paid = next(c for c in data["cac_by_channel"] if c["channel"] == "paid_social")
    assert paid["spend"] == 64000 and paid["conversions"] == 20 and paid["cac"] == 3200
    assert data["funnel"]["clicks"] == 100 and data["funnel"]["click_through_rate"] == 10.0


def test_analytics_cac_null_when_no_conversions(client, db):
    _u, _s, h = _member(db)
    today = date.today().isoformat()
    _seed(client, db, h, [{"ts": today, "channel": "search", "metric": "spend", "value": 500}])
    data = client.get(f"{BASE}/analytics", headers=h).json()["data"]
    search = next(c for c in data["cac_by_channel"] if c["channel"] == "search")
    assert search["cac"] is None


def test_analytics_bad_range_422(client, db):
    _u, _s, h = _member(db)
    resp = client.get(f"{BASE}/analytics?range=nope", headers=h)
    assert resp.status_code == 422, resp.text


def _today() -> date:
    return datetime.now(UTC).date()


def _campaign(db, startup, name):
    c = Campaign(
        startup_id=startup.id,
        name=name,
        objective=CampaignObjective.leads,
        status=CampaignStatus.draft,
    )
    db.add(c)
    db.flush()
    return c


def test_analytics_traffic_by_week(client, db):
    _u, _s, h = _member(db)
    # Anchor to the Monday of the current week so the two points are in distinct ISO weeks
    # regardless of the weekday the test runs on, and both are within 30d.
    this_monday = _today() - timedelta(days=_today().weekday())
    prev_monday = this_monday - timedelta(days=7)
    _seed(
        client,
        db,
        h,
        [
            {"ts": (prev_monday + timedelta(days=2)).isoformat(), "metric": "visits", "value": 100},
            {"ts": (prev_monday + timedelta(days=3)).isoformat(), "metric": "visits", "value": 50},
            {"ts": this_monday.isoformat(), "metric": "visits", "value": 400},
        ],
    )
    resp = client.get(f"{BASE}/analytics?range=30d", headers=h)
    assert resp.status_code == 200, resp.text
    weeks = resp.json()["data"]["traffic_by_week"]
    assert len(weeks) == 2
    for w in weeks:
        assert date.fromisoformat(w["week_start"]).weekday() == 0
    assert [(w["week_start"], w["visits"]) for w in weeks] == [
        (prev_monday.isoformat(), 150),
        (this_monday.isoformat(), 400),
    ]


def test_analytics_leaderboard_orders_by_conversions(client, db):
    _u, s, h = _member(db)
    low = _campaign(db, s, "Low performer")
    high = _campaign(db, s, "High performer")
    today = _today().isoformat()
    _seed(
        client,
        db,
        h,
        [
            {"ts": today, "campaign_id": str(low.id), "metric": "clicks", "value": 50},
            {"ts": today, "campaign_id": str(low.id), "metric": "conversions", "value": 5},
            {"ts": today, "campaign_id": str(low.id), "metric": "spend", "value": 1000},
            {"ts": today, "campaign_id": str(high.id), "metric": "clicks", "value": 200},
            {"ts": today, "campaign_id": str(high.id), "metric": "conversions", "value": 30},
            {"ts": today, "campaign_id": str(high.id), "metric": "spend", "value": 9000},
        ],
    )
    resp = client.get(f"{BASE}/analytics", headers=h)
    assert resp.status_code == 200, resp.text
    board = resp.json()["data"]["leaderboard"]
    assert [r["campaign_id"] for r in board] == [str(high.id), str(low.id)]
    assert [r["conversions"] for r in board] == [30, 5]
    assert board[0]["clicks"] == 200 and board[0]["spend"] == 9000
    assert board[0]["cac"] == round(9000 / 30)
    assert board[1]["cac"] == round(1000 / 5)
    assert board[0]["name"] == "High performer"


def test_analytics_excludes_out_of_range(client, db):
    _u, _s, h = _member(db)
    old = (_today() - timedelta(days=40)).isoformat()
    today = _today().isoformat()
    _seed(
        client,
        db,
        h,
        [
            {"ts": old, "metric": "visits", "value": 9999},
            {"ts": old, "metric": "clicks", "value": 9999},
            {"ts": today, "metric": "visits", "value": 7},
            {"ts": today, "metric": "clicks", "value": 3},
        ],
    )
    resp = client.get(f"{BASE}/analytics", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert sum(w["visits"] for w in data["traffic_by_week"]) == 7
    assert data["funnel"]["clicks"] == 3
    # A wider window picks up the old point too.
    wide = client.get(f"{BASE}/analytics?range=90d", headers=h)
    assert wide.status_code == 200, wide.text
    assert wide.json()["data"]["funnel"]["clicks"] == 10002


def test_analytics_is_tenant_scoped(client, db):
    _u, _s, h = _member(db)
    _u2, s2, h2 = _member(db)
    other_campaign = _campaign(db, s2, "Other tenant campaign")
    today = _today().isoformat()
    _seed(
        client,
        db,
        h,
        [
            {"ts": today, "metric": "visits", "value": 10},
            {"ts": today, "channel": "email", "metric": "spend", "value": 100},
            {"ts": today, "channel": "email", "metric": "conversions", "value": 2},
        ],
    )
    _seed(
        client,
        db,
        h2,
        [
            {"ts": today, "metric": "visits", "value": 1_000_000},
            {"ts": today, "channel": "search", "metric": "spend", "value": 900_000},
            {"ts": today, "channel": "search", "metric": "conversions", "value": 5000},
            {"ts": today, "campaign_id": str(other_campaign.id), "metric": "clicks", "value": 777},
            {
                "ts": today,
                "campaign_id": str(other_campaign.id),
                "metric": "conversions",
                "value": 88,
            },
        ],
    )
    resp = client.get(f"{BASE}/analytics", headers=h)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert sum(w["visits"] for w in data["traffic_by_week"]) == 10
    assert [c["channel"] for c in data["cac_by_channel"]] == ["email"]
    assert data["cac_by_channel"][0]["cac"] == 50
    assert data["leaderboard"] == []
    assert data["funnel"]["clicks"] == 0
    assert data["funnel"]["conversions"] == 2
