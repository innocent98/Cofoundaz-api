import uuid
from datetime import UTC, date, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.enums import JournalMood, MembershipRole
from app.db.models.journal import JournalEntry
from app.services.journal.encryption import encrypt_content
from app.services.journal.service import JournalService
from tests.factories import create_membership, create_startup, create_user

PLAINTEXT = "private words the reviewer must never see"

NON_FOUNDER_ROLES = [
    MembershipRole.team_member,
    MembershipRole.mentor,
    MembershipRole.accountant,
    MembershipRole.legal_advisor,
    MembershipRole.business_consultant,
    MembershipRole.investor,
]

# The plan lists an 8th route, POST /journal/mood/support-card/dismiss. It is
# absent here because it belongs to Task 10, which is gated on senior sign-off.
# Add it to this list when that route lands; the matrix must not weaken.
ROUTES = [
    ("POST", "/api/v1/journal/entries"),
    ("GET", "/api/v1/journal/entries"),
    ("GET", "/api/v1/journal/entries/{id}"),
    ("PATCH", "/api/v1/journal/entries/{id}"),
    ("DELETE", "/api/v1/journal/entries/{id}"),
    ("GET", "/api/v1/journal/mood"),
    ("GET", "/api/v1/journal/prompts/today"),
]

BODY = {"date": "2026-09-01", "content": "text", "mood": "okay", "stress": 5}


def _member(db, *, role=MembershipRole.founder, verified=True, startup=None):
    u = create_user(
        db,
        email=f"journal-{uuid.uuid4().hex[:8]}@example.com",
        email_verified_at=datetime.now(UTC) if verified else None,
    )
    s = startup if startup is not None else create_startup(db, owner=u)
    create_membership(db, u, s, role=role)
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(s.id),
    }
    return u, s, h


def _make_entry(db, *, startup, founder, entry_date=date(2026, 9, 1)):
    entry = JournalEntry(
        startup_id=startup.id,
        founder_id=founder.id,
        date=entry_date,
        content_encrypted=encrypt_content(PLAINTEXT),
        mood=JournalService.mood_to_score(JournalMood.okay),
        stress=5,
    )
    db.add(entry)
    db.flush()
    return entry


def _call(client, method, path, headers=None):
    fn = getattr(client, method.lower())
    if method in ("POST", "PATCH"):
        return fn(path, json=BODY, headers=headers)
    return fn(path, headers=headers)


@pytest.mark.parametrize("role", NON_FOUNDER_ROLES)
@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_non_founder_roles_are_forbidden(client, db, role, method, path):
    founder, startup, _fh = _member(db)
    entry = _make_entry(db, startup=startup, founder=founder)
    _u, _s, h = _member(db, role=role, startup=startup)
    db.commit()

    r = _call(client, method, path.format(id=entry.id), headers=h)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_unauthenticated_is_rejected(client, method, path):
    r = _call(client, method, path.format(id=uuid.uuid4()))
    assert r.status_code == 401, r.text


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_unverified_founder_is_forbidden(client, db, method, path):
    _u, _s, h = _member(db, verified=False)
    db.commit()

    r = _call(client, method, path.format(id=uuid.uuid4()), headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_second_founder_same_workspace_cannot_reach_entry(client, db, method):
    founder, startup, _fh = _member(db)
    entry = _make_entry(db, startup=startup, founder=founder)
    _other, _s, h = _member(db, role=MembershipRole.founder, startup=startup)
    db.commit()

    r = _call(client, method, f"/api/v1/journal/entries/{entry.id}", headers=h)
    assert r.status_code == 404, r.text


def test_second_founder_same_workspace_sees_empty_list(client, db):
    founder, startup, _fh = _member(db)
    _make_entry(db, startup=startup, founder=founder)
    _other, _s, h = _member(db, role=MembershipRole.founder, startup=startup)
    db.commit()

    r = client.get("/api/v1/journal/entries", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["entries"] == []
    assert r.json()["data"]["total"] == 0


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_founder_of_another_workspace_cannot_reach_entry(client, db, method):
    founder, startup, _fh = _member(db)
    entry = _make_entry(db, startup=startup, founder=founder)
    _outsider, _other_startup, h = _member(db)
    db.commit()

    r = _call(client, method, f"/api/v1/journal/entries/{entry.id}", headers=h)
    assert r.status_code == 404, r.text


def test_denial_bodies_leak_neither_plaintext_nor_ciphertext(client, db):
    founder, startup, _fh = _member(db)
    entry = _make_entry(db, startup=startup, founder=founder)
    ciphertext = entry.content_encrypted
    _other, _s, h = _member(db, role=MembershipRole.founder, startup=startup)
    db.commit()

    r = client.get(f"/api/v1/journal/entries/{entry.id}", headers=h)
    assert r.status_code == 404, r.text
    assert PLAINTEXT not in r.text
    assert ciphertext not in r.text


def test_uniform_404_gives_no_enumeration_oracle(client, db):
    founder, startup, _fh = _member(db)
    entry = _make_entry(db, startup=startup, founder=founder)
    _other, _s, h = _member(db, role=MembershipRole.founder, startup=startup)
    db.commit()

    real = client.get(f"/api/v1/journal/entries/{entry.id}", headers=h)
    absent = client.get(f"/api/v1/journal/entries/{uuid.uuid4()}", headers=h)

    assert real.status_code == absent.status_code == 404
    assert real.content == absent.content
