from datetime import UTC, datetime

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
