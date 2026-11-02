from datetime import UTC, datetime

from app.core.security import create_access_token
from app.db.models.enums import MembershipRole, StartupStage
from app.db.models.job import Job
from tests.factories import create_membership, create_startup, create_user


def _founder(db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u, industry="Fintech", stage=StartupStage.idea)
    s.profile.goals = ["Get first customers"]
    create_membership(db, u, s, role=MembershipRole.founder)
    db.flush()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    return u, s, h


def _team_member(db, startup):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=MembershipRole.team_member)
    db.flush()
    return {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


def test_create_conversation_with_message(client, db):
    u, s, h = _founder(db)
    db.commit()

    r = client.post(
        "/api/v1/ai/conversations", headers=h, json={"message": "How long is my runway?"}
    )

    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["title"] == "How long is my runway?"
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["messages"][0]["status"] == "ready"
    assert data["messages"][0]["content"] == "How long is my runway?"
    # the assistant turn is empty + generating until the worker fills it (Pattern B)
    assert data["messages"][1]["status"] == "generating"
    assert data["messages"][1]["content"] == ""
    # exactly one reply job enqueued
    assert db.query(Job).filter_by(type="ai.cofounder.reply").count() == 1


def test_create_empty_then_post_message(client, db):
    u, s, h = _founder(db)
    db.commit()

    conv = client.post("/api/v1/ai/conversations", headers=h, json={}).json()["data"]
    assert conv["messages"] == []
    assert conv["title"] is None

    r = client.post(
        f"/api/v1/ai/conversations/{conv['id']}/messages", headers=h, json={"content": "Hi there"}
    )
    assert r.status_code == 202, r.text
    assert r.json()["data"]["role"] == "assistant"
    assert r.json()["data"]["status"] == "generating"

    got = client.get(f"/api/v1/ai/conversations/{conv['id']}", headers=h).json()["data"]
    assert [m["role"] for m in got["messages"]] == ["user", "assistant"]
    assert got["title"] == "Hi there"


def test_list_conversations(client, db):
    u, s, h = _founder(db)
    db.commit()
    client.post("/api/v1/ai/conversations", headers=h, json={"message": "one"})
    client.post("/api/v1/ai/conversations", headers=h, json={"message": "two"})

    r = client.get("/api/v1/ai/conversations", headers=h)

    assert r.status_code == 200, r.text
    assert {c["title"] for c in r.json()["data"]} == {"one", "two"}


def test_post_message_empty_content_422(client, db):
    u, s, h = _founder(db)
    db.commit()
    conv = client.post("/api/v1/ai/conversations", headers=h, json={}).json()["data"]

    r = client.post(
        f"/api/v1/ai/conversations/{conv['id']}/messages", headers=h, json={"content": ""}
    )
    assert r.status_code == 422


def test_cross_tenant_conversation_404(client, db):
    _ub, _sb, hb = _founder(db)
    conv_b = client.post("/api/v1/ai/conversations", headers=hb, json={"message": "b"}).json()[
        "data"
    ]
    db.commit()
    _ua, _sa, ha = _founder(db)
    db.commit()

    r = client.get(f"/api/v1/ai/conversations/{conv_b['id']}", headers=ha)
    assert r.status_code == 404


def test_requires_auth(client, db):
    u, s, h = _founder(db)
    db.commit()
    r = client.get("/api/v1/ai/conversations")  # no auth headers
    assert r.status_code in (401, 403)


def test_conversation_private_to_creator(client, db):
    # A chat is private to the member who created it, not shared across the workspace.
    u, s, h = _founder(db)
    db.commit()
    conv = client.post(
        "/api/v1/ai/conversations", headers=h, json={"message": "my private chat"}
    ).json()["data"]
    member_h = _team_member(db, s)
    db.commit()

    assert client.get(f"/api/v1/ai/conversations/{conv['id']}", headers=member_h).status_code == 404
    assert client.get("/api/v1/ai/conversations", headers=member_h).json()["data"] == []


def test_second_message_while_generating_409(client, db):
    u, s, h = _founder(db)
    db.commit()
    conv = client.post("/api/v1/ai/conversations", headers=h, json={"message": "first"}).json()[
        "data"
    ]
    # the first assistant turn is still `generating` (no worker ran in-request), so a second
    # message must be refused to avoid stacking LLM jobs
    r = client.post(
        f"/api/v1/ai/conversations/{conv['id']}/messages", headers=h, json={"content": "second"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "REPLY_IN_PROGRESS"


def test_stale_generating_reply_is_failed_on_read_and_new_turn_allowed(client, db):
    from datetime import timedelta

    from app.core.config import settings
    from app.db.models.ai_chat import AiMessage
    from app.db.models.enums import ChatMessageRole, ChatMessageStatus

    u, s, h = _founder(db)
    db.commit()
    conv = client.post("/api/v1/ai/conversations", headers=h, json={"message": "first"}).json()[
        "data"
    ]
    pending = (
        db.query(AiMessage)
        .filter(
            AiMessage.role == ChatMessageRole.assistant, AiMessage.conversation_id == conv["id"]
        )
        .one()
    )
    pending.created_at = pending.created_at - timedelta(
        seconds=settings.AI_REPLY_STALE_SECONDS + 60
    )
    db.commit()

    got = client.get(f"/api/v1/ai/conversations/{conv['id']}", headers=h).json()["data"]
    assistant = [m for m in got["messages"] if m["role"] == "assistant"][0]
    assert assistant["status"] == "failed"
    assert assistant["content"]
    db.expire_all()
    assert db.get(AiMessage, pending.id).status == ChatMessageStatus.failed
    again = client.post(
        f"/api/v1/ai/conversations/{conv['id']}/messages", headers=h, json={"content": "retry"}
    )
    assert again.status_code == 202
