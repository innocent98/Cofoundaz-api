import uuid
from datetime import UTC, date, datetime, timedelta

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.db.models.job import Job
from app.db.models.journal import JournalEntry
from tests.factories import create_membership, create_startup, create_user

TODAY = date.today()
PLAINTEXT = "shipped the onboarding flow today"


def _founder(db):
    u = create_user(
        db,
        email=f"journal-{uuid.uuid4().hex[:8]}@example.com",
        email_verified_at=datetime.now(UTC),
    )
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }
    return u, s, h


def _body(**kw):
    body = {
        "date": TODAY.isoformat(),
        "content": PLAINTEXT,
        "mood": "good",
        "stress": 4,
    }
    body.update(kw)
    return body


def test_create_entry_returns_the_decrypted_entry(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.post("/api/v1/journal/entries", json=_body(), headers=h)
    assert r.status_code == 200, r.text

    data = r.json()["data"]
    assert data["content"] == PLAINTEXT
    assert data["mood"] == "good"
    assert data["stress"] == 4
    assert data["date"] == TODAY.isoformat()


def test_content_is_encrypted_at_rest(client, db):
    _u, s, h = _founder(db)
    db.commit()

    r = client.post("/api/v1/journal/entries", json=_body(), headers=h)
    assert r.status_code == 200, r.text

    row = db.query(JournalEntry).filter_by(startup_id=s.id).one()
    assert row.content_encrypted != PLAINTEXT
    assert PLAINTEXT not in row.content_encrypted


def test_get_entry_returns_what_was_written(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    created = client.post("/api/v1/journal/entries", json=_body(), headers=h)
    entry_id = created.json()["data"]["id"]

    r = client.get(f"/api/v1/journal/entries/{entry_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["content"] == PLAINTEXT


def test_saving_the_same_day_twice_updates_instead_of_duplicating(client, db):
    _u, s, h = _founder(db)
    db.commit()

    first = client.post("/api/v1/journal/entries", json=_body(), headers=h)
    assert first.status_code == 200, first.text

    second = client.post(
        "/api/v1/journal/entries",
        json=_body(content="corrected text", mood="great", stress=2),
        headers=h,
    )
    assert second.status_code == 200, second.text

    assert db.query(JournalEntry).filter_by(startup_id=s.id).count() == 1
    data = second.json()["data"]
    assert data["content"] == "corrected text"
    assert data["mood"] == "great"
    assert data["stress"] == 2


def test_future_dated_entry_is_rejected(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    r = client.post("/api/v1/journal/entries", json=_body(date=tomorrow), headers=h)
    assert r.status_code == 422, r.text


def test_blank_content_is_rejected(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.post("/api/v1/journal/entries", json=_body(content="   "), headers=h)
    assert r.status_code == 422, r.text


def test_stress_outside_one_to_ten_is_rejected(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    low = client.post("/api/v1/journal/entries", json=_body(stress=0), headers=h)
    high = client.post("/api/v1/journal/entries", json=_body(stress=11), headers=h)
    assert low.status_code == 422, low.text
    assert high.status_code == 422, high.text


def test_delete_removes_the_entry(client, db):
    _u, s, h = _founder(db)
    db.commit()

    created = client.post("/api/v1/journal/entries", json=_body(), headers=h)
    entry_id = created.json()["data"]["id"]

    r = client.delete(f"/api/v1/journal/entries/{entry_id}", headers=h)
    assert r.status_code in (200, 204), r.text
    assert db.query(JournalEntry).filter_by(startup_id=s.id).count() == 0


def test_prompt_today_shape_unchanged_and_enqueues(client, db):
    _u, s, h = _founder(db)
    db.commit()

    first = client.get("/api/v1/journal/prompts/today", headers=h)
    assert first.status_code == 200, first.text
    assert set(first.json()["data"].keys()) == {"prompt"}
    assert isinstance(first.json()["data"]["prompt"], str)
    assert first.json()["data"]["prompt"]

    second = client.get("/api/v1/journal/prompts/today", headers=h)
    assert second.status_code == 200, second.text
    assert second.json()["data"] == first.json()["data"]

    jobs = db.query(Job).filter_by(type="ai.journal.prompt", startup_id=s.id).all()
    assert len(jobs) == 1
