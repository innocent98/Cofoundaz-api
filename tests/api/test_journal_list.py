import uuid
from datetime import UTC, date, datetime, timedelta

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from tests.factories import create_membership, create_startup, create_user

TODAY = date.today()


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


def _post(client, headers, *, days_ago, content, mood="okay", stress=5):
    return client.post(
        "/api/v1/journal/entries",
        json={
            "date": (TODAY - timedelta(days=days_ago)).isoformat(),
            "content": content,
            "mood": mood,
            "stress": stress,
        },
        headers=headers,
    )


def test_entries_are_listed_newest_first(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    _post(client, h, days_ago=2, content="oldest")
    _post(client, h, days_ago=1, content="middle")
    _post(client, h, days_ago=0, content="newest")

    r = client.get("/api/v1/journal/entries", headers=h)
    assert r.status_code == 200, r.text

    data = r.json()["data"]
    assert data["total"] == 3
    assert [e["first_line"] for e in data["entries"]] == ["newest", "middle", "oldest"]


def test_total_counts_all_entries_not_just_the_page(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    for i in range(3):
        _post(client, h, days_ago=i, content=f"entry {i}")

    r = client.get("/api/v1/journal/entries?limit=2", headers=h)
    assert r.status_code == 200, r.text

    data = r.json()["data"]
    assert len(data["entries"]) == 2
    assert data["total"] == 3


def test_skip_moves_to_the_next_page(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    _post(client, h, days_ago=2, content="oldest")
    _post(client, h, days_ago=1, content="middle")
    _post(client, h, days_ago=0, content="newest")

    r = client.get("/api/v1/journal/entries?limit=1&skip=1", headers=h)
    assert r.status_code == 200, r.text
    assert [e["first_line"] for e in r.json()["data"]["entries"]] == ["middle"]


def test_first_line_is_only_the_first_line(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    _post(client, h, days_ago=0, content="headline\nand more detail below")

    r = client.get("/api/v1/journal/entries", headers=h)
    assert r.json()["data"]["entries"][0]["first_line"] == "headline"


def test_search_finds_matching_entries(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    _post(client, h, days_ago=1, content="talked to investors")
    _post(client, h, days_ago=0, content="fixed the deploy")

    r = client.get("/api/v1/journal/entries?search=investors", headers=h)
    assert r.status_code == 200, r.text

    data = r.json()["data"]
    assert data["total"] == 1
    assert data["entries"][0]["first_line"] == "talked to investors"


def test_search_with_no_match_returns_nothing(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    _post(client, h, days_ago=0, content="fixed the deploy")

    r = client.get("/api/v1/journal/entries?search=zzzznothing", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["entries"] == []
    assert r.json()["data"]["total"] == 0


def test_empty_journal_lists_nothing(client, db):
    _u, _s, h = _founder(db)
    db.commit()

    r = client.get("/api/v1/journal/entries", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["entries"] == []
    assert r.json()["data"]["total"] == 0
