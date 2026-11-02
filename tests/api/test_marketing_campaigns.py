from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/marketing"

NON_MARKETING_ROLES = [
    MembershipRole.mentor,
    MembershipRole.investor,
]


def _headers(user, startup):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _member(db, *, role=MembershipRole.founder, verified=True, startup=None):
    u = create_user(db, email_verified_at=datetime.now(UTC) if verified else None)
    if startup is None:
        startup = create_startup(db, owner=u, stage=StartupStage.validation)
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, startup, _headers(u, startup)


def test_segment_and_campaign_crud(client, db):
    _u, _s, h = _member(db)

    seg = client.post(f"{BASE}/segments", json={"name": "SMB", "est_size": 100}, headers=h)
    assert seg.status_code == 200, seg.text
    sid = seg.json()["data"]["id"]

    camp = client.post(
        f"{BASE}/campaigns",
        json={
            "name": "Q4",
            "objective": "launch",
            "budget": 50000,
            "channel_mix": {"email": 60, "search": 40},
            "segment_ids": [sid],
        },
        headers=h,
    )
    assert camp.status_code == 200, camp.text
    cid = camp.json()["data"]["id"]

    get_r = client.get(f"{BASE}/campaigns/{cid}", headers=h)
    assert get_r.json()["data"]["segment_ids"] == [sid]

    launched = client.patch(f"{BASE}/campaigns/{cid}", json={"status": "active"}, headers=h)
    assert launched.status_code == 200, launched.text
    assert launched.json()["data"]["launched_at"] is not None

    used = client.get(f"{BASE}/segments/{sid}/campaigns", headers=h)
    assert used.status_code == 200, used.text
    assert any(c["id"] == cid for c in used.json()["data"]["campaigns"])

    list_segments = client.get(f"{BASE}/segments", headers=h)
    assert list_segments.status_code == 200
    assert any(s["id"] == sid for s in list_segments.json()["data"]["segments"])

    list_campaigns = client.get(f"{BASE}/campaigns", headers=h)
    assert list_campaigns.status_code == 200
    assert any(c["id"] == cid for c in list_campaigns.json()["data"]["campaigns"])

    updated_seg = client.patch(f"{BASE}/segments/{sid}", json={"est_size": 200}, headers=h)
    assert updated_seg.status_code == 200, updated_seg.text
    assert updated_seg.json()["data"]["est_size"] == 200

    delete_camp = client.delete(f"{BASE}/campaigns/{cid}", headers=h)
    assert delete_camp.status_code == 200, delete_camp.text

    delete_seg = client.delete(f"{BASE}/segments/{sid}", headers=h)
    assert delete_seg.status_code == 200, delete_seg.text


def test_illegal_transition_422(client, db):
    _u, _s, h = _member(db)
    c = client.post(f"{BASE}/campaigns", json={"name": "C", "objective": "leads"}, headers=h)
    cid = c.json()["data"]["id"]
    r = client.patch(f"{BASE}/campaigns/{cid}", json={"status": "completed"}, headers=h)
    assert r.status_code == 422, r.text


def test_bad_channel_mix_422(client, db):
    _u, _s, h = _member(db)
    r = client.post(
        f"{BASE}/campaigns",
        json={"name": "C", "objective": "leads", "channel_mix": {"email": 150}},
        headers=h,
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("role", NON_MARKETING_ROLES)
def test_rbac_non_marketing_role_forbidden(client, db, role):
    _founder, startup, _fh = _member(db)
    _u, _s, h = _member(db, role=role, startup=startup)

    get_segments = client.get(f"{BASE}/segments", headers=h)
    assert get_segments.status_code == 403

    get_campaigns = client.get(f"{BASE}/campaigns", headers=h)
    assert get_campaigns.status_code == 403

    post_segment = client.post(f"{BASE}/segments", json={"name": "X"}, headers=h)
    assert post_segment.status_code == 403

    post_campaign = client.post(
        f"{BASE}/campaigns", json={"name": "C", "objective": "leads"}, headers=h
    )
    assert post_campaign.status_code == 403
