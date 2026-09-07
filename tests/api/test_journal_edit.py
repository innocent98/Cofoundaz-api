import uuid
from datetime import UTC, date, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.db.models.journal import JournalEntry
from tests.factories import create_membership, create_startup, create_user

TODAY = date.today()
ORIGINAL = "first draft of today's thoughts"


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


def _create(client, headers):
    r = client.post(
        "/api/v1/journal/entries",
        json={
            "date": TODAY.isoformat(),
            "content": ORIGINAL,
            "mood": "okay",
            "stress": 5,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_editing_content_leaves_mood_and_stress_alone(client, db):
    _u, _s, h = _founder(db)
    db.commit()
    entry_id = _create(client, h)

    r = client.patch(
        f"/api/v1/journal/entries/{entry_id}",
        json={"content": "second draft"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    data = r.json()["data"]
    assert data["content"] == "second draft"
    assert data["mood"] == "okay"
    assert data["stress"] == 5


def test_editing_mood_leaves_content_alone(client, db):
    _u, _s, h = _founder(db)
    db.commit()
    entry_id = _create(client, h)

    r = client.patch(
        f"/api/v1/journal/entries/{entry_id}",
        json={"mood": "great"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    data = r.json()["data"]
    assert data["mood"] == "great"
    assert data["content"] == ORIGINAL


def test_editing_stress_only(client, db):
    _u, _s, h = _founder(db)
    db.commit()
    entry_id = _create(client, h)

    r = client.patch(
        f"/api/v1/journal/entries/{entry_id}",
        json={"stress": 9},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["stress"] == 9
    assert r.json()["data"]["content"] == ORIGINAL


def test_edited_content_is_re_encrypted(client, db):
    _u, s, h = _founder(db)
    db.commit()
    entry_id = _create(client, h)

    client.patch(
        f"/api/v1/journal/entries/{entry_id}",
        json={"content": "revised private words"},
        headers=h,
    )

    row = db.query(JournalEntry).filter_by(startup_id=s.id).one()
    assert "revised private words" not in row.content_encrypted
    assert ORIGINAL not in row.content_encrypted


def test_editing_an_unknown_entry_is_not_found(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.patch(
        f"/api/v1/journal/entries/{uuid.uuid4()}",
        json={"content": "nothing here"},
        headers=h,
    )
    assert r.status_code == 404, r.text


def test_blank_content_is_rejected_on_edit(client, db):
    _u, _s, h = _founder(db)
    db.commit()
    entry_id = _create(client, h)

    r = client.patch(
        f"/api/v1/journal/entries/{entry_id}",
        json={"content": "   "},
        headers=h,
    )
    assert r.status_code == 422, r.text


def test_out_of_range_stress_is_rejected_on_edit(client, db):
    _u, _s, h = _founder(db)
    db.commit()
    entry_id = _create(client, h)

    r = client.patch(
        f"/api/v1/journal/entries/{entry_id}",
        json={"stress": 42},
        headers=h,
    )
    assert r.status_code == 422, r.text


def test_deleted_entry_can_no_longer_be_read(client, db):
    _u, _s, h = _founder(db)
    db.commit()
    entry_id = _create(client, h)

    deleted = client.delete(f"/api/v1/journal/entries/{entry_id}", headers=h)
    assert deleted.status_code in (200, 204), deleted.text

    r = client.get(f"/api/v1/journal/entries/{entry_id}", headers=h)
    assert r.status_code == 404, r.text


def test_deleting_an_unknown_entry_is_not_found(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.delete(f"/api/v1/journal/entries/{uuid.uuid4()}", headers=h)
    assert r.status_code == 404, r.text
