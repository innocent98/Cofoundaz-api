"""Every mutating sales route writes at least one `sales.*` activity row (feed visibility is
SALES_ROLES only, see platform/activity.py)."""

import io
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.security import create_access_token
from app.db.models.activity import ActivityLog
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/sales"


def test_every_mutating_route_writes_a_sales_activity(client, db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}

    def count():
        return db.scalar(
            select(func.count())
            .select_from(ActivityLog)
            .where(ActivityLog.startup_id == s.id, ActivityLog.action.like("sales.%"))
        )

    def step(label, resp):
        before = state["n"]
        assert resp.status_code == 200, (label, resp.text)
        now = count()
        assert now > before, f"{label} wrote no sales.* activity"
        state["n"] = now
        return resp.json()["data"]

    state = {"n": count()}
    stages = client.get(f"{BASE}/stages", headers=h).json()["data"]
    state["n"] = count()
    step(
        "PUT /stages",
        client.put(
            f"{BASE}/stages",
            json={
                "stages": [
                    {
                        "id": x["id"],
                        "name": x["name"] + ("!" if i == 0 else ""),
                        "kind": x["kind"],
                        "win_probability": x["win_probability"],
                    }
                    for i, x in enumerate(stages)
                ]
            },
            headers=h,
        ),
    )
    step("PATCH /settings", client.patch(f"{BASE}/settings", json={"stall_days": 10}, headers=h))
    acc = step("POST /accounts", client.post(f"{BASE}/accounts", json={"name": "Acme"}, headers=h))
    step(
        "PATCH /accounts",
        client.patch(f"{BASE}/accounts/{acc['id']}", json={"industry": "Fintech"}, headers=h),
    )
    con = step(
        "POST /contacts",
        client.post(
            f"{BASE}/contacts", json={"first_name": "Ada", "account_id": acc["id"]}, headers=h
        ),
    )
    step(
        "PATCH /contacts",
        client.patch(f"{BASE}/contacts/{con['id']}", json={"title": "CEO"}, headers=h),
    )
    lead = step(
        "POST /leads",
        client.post(f"{BASE}/leads", json={"name": "Lee", "company": "Co"}, headers=h),
    )
    step(
        "PATCH /leads",
        client.patch(f"{BASE}/leads/{lead['id']}", json={"company": "Co2"}, headers=h),
    )
    step("convert", client.post(f"{BASE}/leads/{lead['id']}/convert", json={}, headers=h))
    prev = client.post(
        f"{BASE}/leads/import/preview",
        files={"file": ("l.csv", io.BytesIO(b"Name,Email\nZed,zed@x.test\n"), "text/csv")},
        headers=h,
    ).json()["data"]
    step(
        "import",
        client.post(
            f"{BASE}/leads/import",
            json={"upload_id": prev["upload_id"], "mapping": prev["suggested_mapping"]},
            headers=h,
        ),
    )
    lead2 = step("POST /leads 2", client.post(f"{BASE}/leads", json={"name": "Bin"}, headers=h))
    step("DELETE /leads", client.delete(f"{BASE}/leads/{lead2['id']}", headers=h))
    deal = step("POST /deals", client.post(f"{BASE}/deals", json={"name": "D1"}, headers=h))
    step(
        "PATCH /deals", client.patch(f"{BASE}/deals/{deal['id']}", json={"name": "D1b"}, headers=h)
    )
    won = next(
        x for x in client.get(f"{BASE}/stages", headers=h).json()["data"] if x["kind"] == "won"
    )
    step(
        "POST /deals/stage",
        client.post(f"{BASE}/deals/{deal['id']}/stage", json={"stage_id": won["id"]}, headers=h),
    )
    act = step(
        "POST /activities",
        client.post(
            f"{BASE}/activities",
            json={"kind": "note", "body": "hi", "deal_id": deal["id"]},
            headers=h,
        ),
    )
    step(
        "PATCH /activities",
        client.patch(f"{BASE}/activities/{act['id']}", json={"body": "hi2"}, headers=h),
    )
    step("DELETE /activities", client.delete(f"{BASE}/activities/{act['id']}", headers=h))
    step("DELETE /deals", client.delete(f"{BASE}/deals/{deal['id']}", headers=h))
    step("DELETE /contacts", client.delete(f"{BASE}/contacts/{con['id']}", headers=h))
    step("DELETE /accounts", client.delete(f"{BASE}/accounts/{acc['id']}", headers=h))
