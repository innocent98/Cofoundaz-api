from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.ai_chat import AiConversation, AiMessage
from app.db.models.enums import ChatMessageRole, ChatMessageStatus, MembershipRole
from app.db.models.job import Job
from tests.factories import create_membership, create_startup, create_user

C = "/api/v1/ai/conversations"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def _world(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    db.flush()
    h = {
        "Authorization": f"Bearer {create_access_token(str(owner.id))}",
        "X-Workspace-Id": str(s.id),
    }
    return owner, s, h


def _seed(db, s, user, title, reply=None, topic=None):
    conv = AiConversation(startup_id=s.id, created_by=user.id, title=title, topic=topic)
    db.add(conv)
    db.flush()
    db.add(
        AiMessage(
            conversation_id=conv.id,
            role=ChatMessageRole.user,
            status=ChatMessageStatus.ready,
            content=title,
        )
    )
    if reply:
        db.add(
            AiMessage(
                conversation_id=conv.id,
                role=ChatMessageRole.assistant,
                status=ChatMessageStatus.ready,
                content=reply,
            )
        )
    db.flush()
    return conv


def test_status_is_founder_and_team_member_only(client, db):
    _o, s, fh = _world(db)
    _u, tm = _member(db, s, MembershipRole.team_member)
    _u, mentor = _member(db, s, MembershipRole.mentor)
    _u, acct = _member(db, s, MembershipRole.accountant)
    db.commit()
    assert client.get("/api/v1/ai/status", headers=fh).status_code == 200
    assert client.get("/api/v1/ai/status", headers=tm).status_code == 200
    assert client.get("/api/v1/ai/status", headers=mentor).status_code == 403
    assert client.get("/api/v1/ai/status", headers=acct).status_code == 403


def test_search_matches_title_and_reply_with_snippet(client, db):
    o, s, h = _world(db)
    _seed(
        db,
        s,
        o,
        "NDA for a contractor",
        reply="Add a Confidentiality clause covering trade secrets.",
    )
    _seed(db, s, o, "Runway question", reply="Your runway is six months.")
    db.commit()

    r = client.get(C, headers=h, params={"query": "confidential"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [c["title"] for c in data] == ["NDA for a contractor"]
    assert "Confidentiality" in data[0]["match_snippet"]

    by_title = client.get(C, headers=h, params={"query": "runway"}).json()["data"]
    assert [c["title"] for c in by_title] == ["Runway question"]
    # no query: no snippet key at all
    assert all("match_snippet" not in c for c in client.get(C, headers=h).json()["data"])


def test_search_treats_wildcards_literally(client, db):
    o, s, h = _world(db)
    _seed(db, s, o, "100% growth plan")
    _seed(db, s, o, "Something else")
    db.commit()
    got = client.get(C, headers=h, params={"query": "%"})
    assert got.status_code == 422  # below the 2-char minimum
    got = client.get(C, headers=h, params={"query": "0%"}).json()["data"]
    assert [c["title"] for c in got] == ["100% growth plan"]
    got = client.get(C, headers=h, params={"query": "_ _"}).json()["data"]
    assert got == []


def test_search_ignores_unready_and_removed_messages(client, db):
    from datetime import UTC as _UTC

    o, s, h = _world(db)
    conv = _seed(db, s, o, "Topic one", reply="secret-word lives here")
    msg = (
        db.query(AiMessage).filter_by(conversation_id=conv.id, role=ChatMessageRole.assistant).one()
    )
    msg.removed_by_moderation_at = datetime.now(_UTC)
    db.commit()
    assert client.get(C, headers=h, params={"query": "secret-word"}).json()["data"] == []


def test_topic_filter_and_validation(client, db):
    o, s, h = _world(db)
    _seed(db, s, o, "A", topic="finance")
    _seed(db, s, o, "B", topic="legal")
    _seed(db, s, o, "C")
    db.commit()
    got = client.get(C, headers=h, params={"topic": "finance"}).json()["data"]
    assert [c["title"] for c in got] == ["A"]
    assert got[0]["topic"] == "finance"
    assert client.get(C, headers=h, params={"topic": "nope"}).status_code == 422


def test_conversations_are_private_in_search_and_filters(client, db):
    o, s, h = _world(db)
    _u, tm = _member(db, s, MembershipRole.team_member)
    _seed(db, s, o, "founder only chat", reply="unique-needle")
    db.commit()
    assert client.get(C, headers=tm, params={"query": "unique-needle"}).json()["data"] == []
    assert client.get(C, headers=tm, params={"archived": "true"}).json()["data"] == []


def test_archive_hides_from_list_and_search_and_unarchive_restores(client, db):
    o, s, h = _world(db)
    a = _seed(db, s, o, "Archive me", reply="findable text")
    b = _seed(db, s, o, "Keep me")
    db.commit()

    r = client.post(f"{C}/archive", headers=h, json={"ids": [str(a.id)]})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"archived": 1}
    assert client.post(f"{C}/archive", headers=h, json={"ids": [str(a.id)]}).json()["data"] == {
        "archived": 0
    }

    assert [c["title"] for c in client.get(C, headers=h).json()["data"]] == ["Keep me"]
    assert client.get(C, headers=h, params={"query": "findable"}).json()["data"] == []
    listed = client.get(C, headers=h, params={"archived": "true"}).json()["data"]
    assert [c["title"] for c in listed] == ["Archive me"]
    assert listed[0]["archived_at"] is not None
    # archived conversations are not searchable, even behind the filter
    both = client.get(C, headers=h, params={"archived": "true", "query": "findable"})
    assert both.status_code == 422

    # global search (⌘K) skips it too
    g = client.get("/api/v1/search", headers=h, params={"q": "Archive me"})
    assert g.status_code == 200
    assert "ai_conversation" not in g.text and str(a.id) not in g.text

    # posting to an archived conversation is refused
    p = client.post(f"{C}/{a.id}/messages", headers=h, json={"content": "hi"})
    assert p.status_code == 409 and p.json()["error"]["code"] == "CONVERSATION_ARCHIVED"

    assert client.post(f"{C}/unarchive", headers=h, json={"ids": [str(a.id)]}).json()["data"] == {
        "unarchived": 1
    }
    assert {c["title"] for c in client.get(C, headers=h).json()["data"]} == {
        "Archive me",
        "Keep me",
    }
    back = client.get("/api/v1/search", headers=h, params={"q": "Archive me"})
    assert str(a.id) in back.text  # positive control for the archived assertion above
    assert b.id


def test_bulk_archive_is_all_or_nothing_and_never_touches_others(client, db):
    o, s, h = _world(db)
    _u, tm = _member(db, s, MembershipRole.team_member)
    mine = _seed(db, s, o, "mine")
    theirs = _seed(db, s, _u, "theirs")
    db.commit()
    r = client.post(f"{C}/archive", headers=h, json={"ids": [str(mine.id), str(theirs.id)]})
    assert r.status_code == 404
    db.expire_all()
    assert db.get(AiConversation, mine.id).archived_at is None
    assert db.get(AiConversation, theirs.id).archived_at is None
    # validation
    resp1 = client.post(f"{C}/archive", headers=h, json={"ids": []})
    assert resp1.status_code == 422
    resp2 = client.post(f"{C}/archive", headers=h, json={"ids": ["nope"]})
    assert resp2.status_code == 422
    resp3 = client.post(f"{C}/archive", headers=tm, json={"ids": [str(mine.id)]})
    assert resp3.status_code == 404


def test_delete_forever_removes_conversation_and_messages(client, db):
    o, s, h = _world(db)
    _u, tm = _member(db, s, MembershipRole.team_member)
    conv = _seed(db, s, o, "Delete me", reply="bye")
    cid = conv.id
    db.commit()

    resp4 = client.delete(f"{C}/{cid}", headers=tm)
    assert resp4.status_code == 404  # not theirs
    r = client.delete(f"{C}/{cid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"deleted": True}
    db.expire_all()
    assert db.get(AiConversation, cid) is None
    assert db.query(AiMessage).filter_by(conversation_id=cid).count() == 0
    assert client.get(f"{C}/{cid}", headers=h).status_code == 404
    resp5 = client.delete(f"{C}/{cid}", headers=h)
    assert resp5.status_code == 404


def test_delete_works_on_an_archived_conversation(client, db):
    o, s, h = _world(db)
    conv = _seed(db, s, o, "old")
    client.post(f"{C}/archive", headers=h, json={"ids": [str(conv.id)]})
    db.commit()
    resp6 = client.delete(f"{C}/{conv.id}", headers=h)
    assert resp6.status_code == 200


def test_roles_without_ai_access_get_403_on_new_routes(client, db):
    o, s, h = _world(db)
    _u, mentor = _member(db, s, MembershipRole.mentor)
    conv = _seed(db, s, o, "x")
    db.commit()
    assert (
        client.post(f"{C}/archive", headers=mentor, json={"ids": [str(conv.id)]}).status_code == 403
    )
    resp7 = client.delete(f"{C}/{conv.id}", headers=mentor)
    assert resp7.status_code == 403
    assert client.get(C, headers=mentor, params={"query": "xx"}).status_code == 403
    assert Job is not None
