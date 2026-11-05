from datetime import UTC, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import READINESS_READ_ROLES, READINESS_WRITE_ROLES, MembershipRole
from app.db.models.job import Job
from app.platform import llm_budget
from app.services.readiness import deck
from tests.deck_helpers import CannedLLM, deck_pdf, store_deck
from tests.factories import create_membership, create_startup, create_user

BASE = "/api/v1/readiness/deck-reviews"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def team(db, monkeypatch):
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: CannedLLM())
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    db.flush()
    members = {role: _member(db, s, role) for role in MembershipRole}
    f, _ = store_deck(db, s, members[MembershipRole.founder][0])
    db.commit()
    return s, {r: h for r, (_u, h) in members.items()}, f


@pytest.mark.parametrize("role", list(MembershipRole))
def test_create_by_role(client, team, role):
    _s, heads, f = team
    r = client.post(BASE, headers=heads[role], json={"file_id": str(f.id)})
    if role in READINESS_WRITE_ROLES:
        assert r.status_code == 202, r.text
    else:
        assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", list(MembershipRole))
@pytest.mark.parametrize("path", ["", "/00000000-0000-0000-0000-000000000000"])
def test_reads_by_role(client, team, role, path):
    _s, heads, _f = team
    r = client.get(BASE + path, headers=heads[role])
    if role in READINESS_READ_ROLES:
        assert r.status_code == (200 if path == "" else 404), r.text
    else:
        assert r.status_code == 403, r.text


def _drain(db):
    from app.worker import runner
    from app.worker.handlers import readiness as handlers

    # Other tests clear the registry; the import only registers once per process.
    runner.register_handler(deck.JOB_TYPE, handlers.handle_deck_review)
    for _ in range(20):
        if runner.run_once(db) == 0:
            break


def test_full_flow_create_dedupe_poll_detail_thumbnail(client, db, team):
    s, heads, f = team
    fh = heads[MembershipRole.founder]
    created = client.post(BASE, headers=fh, json={"file_id": str(f.id)})
    assert created.status_code == 202, created.text
    body = created.json()["data"]
    assert body["deduplicated"] is False and body["review"]["status"] == "queued"
    rid, job_id = body["review"]["id"], body["review"]["job_id"]
    queued = client.get(f"/api/v1/jobs/{job_id}", headers=fh)
    assert queued.json()["data"]["status"] == "queued"

    again = client.post(BASE, headers=fh, json={"file_id": str(f.id)})
    assert again.status_code == 200
    assert again.json()["data"]["deduplicated"] is True
    assert again.json()["data"]["review"]["id"] == rid

    _drain(db)
    db.expire_all()
    detail = client.get(f"{BASE}/{rid}", headers=heads[MembershipRole.team_member])
    assert detail.status_code == 200, detail.text
    d = detail.json()["data"]
    assert d["status"] == "ready" and d["scores"]["narrative"] == 7
    assert len(d["slides"]) == 8 and len(d["top_fixes"]) == 5
    url = d["slides"][0]["thumbnail_url"]
    assert "thumbnail_key" not in d["slides"][0]
    path = url[url.index("/api/v1/") :]
    img = client.get(path)  # the signed link needs no login
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert img.headers["x-content-type-options"] == "nosniff"
    listed = client.get(BASE, headers=fh).json()["data"]["reviews"]
    assert [r["version"] for r in listed] == [1]
    assert "slides" not in listed[0]
    score = client.get("/api/v1/readiness/score", headers=fh).json()["data"]
    deck_must = next(m for m in score["must_haves"] if m["key"] == "pitch_deck_reviewed")
    assert deck_must["met"] is True
    hist = client.get("/api/v1/readiness/history?range=30", headers=fh).json()["data"]
    assert hist["log"][0]["type"] == "deck_review"


def test_thumbnail_link_rejects_tampered_foreign_and_missing(client, db, team):
    s, heads, f = team
    fh = heads[MembershipRole.founder]
    rid = client.post(BASE, headers=fh, json={"file_id": str(f.id)}).json()["data"]["review"]["id"]
    _drain(db)
    db.expire_all()
    url = client.get(f"{BASE}/{rid}", headers=fh).json()["data"]["slides"][0]["thumbnail_url"]
    token = url.split("token=")[1]
    assert client.get("/api/v1/readiness/deck-thumbnails?token=" + token + "x").status_code == 404
    assert client.get("/api/v1/readiness/deck-thumbnails?token=garbage").status_code == 404
    assert client.get("/api/v1/readiness/deck-thumbnails").status_code == 422
    from app.platform.signed_urls import sign_key

    other, _ = sign_key("documents/x/secret.pdf", startup_id=str(s.id), ttl_seconds=60)
    assert client.get("/api/v1/readiness/deck-thumbnails?token=" + other).status_code == 404
    expired, _ = sign_key(f"readiness/{s.id}/r/1.png", startup_id=str(s.id), ttl_seconds=-5)
    assert client.get("/api/v1/readiness/deck-thumbnails?token=" + expired).status_code == 404


def test_create_validation_and_tenancy(client, db, team):
    s, heads, f = team
    fh = heads[MembershipRole.founder]
    assert client.post(BASE, headers=fh, json={}).status_code == 422
    assert client.post(BASE, headers=fh, json={"file_id": "nope"}).status_code == 422
    assert client.post(BASE, headers=fh, json={"file_id": str(f.id), "x": 1}).status_code == 422
    missing = client.post(
        BASE, headers=fh, json={"file_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert missing.status_code == 404
    other = create_startup(db, owner=create_user(db, email_verified_at=datetime.now(UTC)))
    foreign, _ = store_deck(db, other, create_user(db), ["foreign deck"])
    db.commit()
    assert client.post(BASE, headers=fh, json={"file_id": str(foreign.id)}).status_code == 404
    unclean, _ = store_deck(
        db, s, create_user(db), name="u.pdf", content=deck_pdf(["u"]), clean=False
    )
    db.commit()
    r = client.post(BASE, headers=fh, json={"file_id": str(unclean.id)})
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_NOT_CLEAN"


def test_budget_exhausted_is_a_clean_429(client, db, team, monkeypatch):
    from app.core.config import settings

    s, heads, f = team
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 5)
    llm_budget.debit(db, s.id, 5)
    db.commit()
    r = client.post(BASE, headers=heads[MembershipRole.founder], json={"file_id": str(f.id)})
    assert r.status_code == 429 and r.json()["error"]["code"] == "AI_BUDGET_EXHAUSTED"
    assert db.query(Job).filter_by(type=deck.JOB_TYPE).count() == 0
