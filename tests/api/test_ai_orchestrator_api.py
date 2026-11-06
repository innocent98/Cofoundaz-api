"""AI Co-Founder over HTTP: routed replies, the new message fields, topic filter, Cancel, roles."""

from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.enums import MembershipRole
from app.platform import llm_budget
from app.worker import runner
from app.worker.handlers import ai as _ai  # noqa: F401  (registers ai.cofounder.reply)
from tests.factories import create_membership, create_startup, create_user

C = "/api/v1/ai/conversations"


class _LLM:
    last_usage_tokens = 5

    def complete(self, messages, *, max_tokens, temperature=0.7):
        return "Here is my answer."

    def complete_json(self, messages, *, schema, max_tokens):
        return {"agent": "strategist", "confidence": 0.7}


@pytest.fixture(autouse=True)
def _llm(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DAILY_TOKEN_BUDGET", 100_000)
    monkeypatch.setattr(llm_budget, "get_llm_client", lambda: _LLM())


def _world(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner)
    create_membership(db, owner, s, role=MembershipRole.founder)
    db.flush()
    return owner, s, _h(owner, s)


def _h(user, s):
    return {
        "Authorization": f"Bearer {create_access_token(str(user.id))}",
        "X-Workspace-Id": str(s.id),
    }


def _member(db, s, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, s, role=role)
    db.flush()
    return u, _h(u, s)


def _work(db):
    for _ in range(5):
        if runner.run_once(db) == 0:
            break


def test_a_routed_reply_exposes_agent_reasoning_sources_and_disclaimer(client, db):
    _o, _s, h = _world(db)
    db.commit()
    conv = client.post(C, headers=h, json={"message": "Draft an NDA for a contractor"}).json()[
        "data"
    ]
    assert conv["topic"] is None  # not routed yet
    first = conv["messages"][1]
    assert first["status"] == "generating"
    assert first["agent"] is None and first["disclaimer"] is None and first["sources"] is None

    _work(db)
    got = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]
    assert got["topic"] == "legal"
    user_msg, reply = got["messages"]
    assert user_msg["agent"] is None
    assert reply["status"] == "ready" and reply["content"] == "Here is my answer."
    assert reply["agent"] == {"key": "legal", "name": "Legal Assistant"}
    assert reply["disclaimer"].startswith("I'm an AI, not a lawyer")
    assert reply["reasoning_summary"].startswith("Answered by the Legal Assistant")
    assert reply["sources"] == [{"key": "profile", "label": "Your startup profile"}]

    by_topic = client.get(C, headers=h, params={"topic": "legal"}).json()["data"]
    assert [c["id"] for c in by_topic] == [conv["id"]]
    assert client.get(C, headers=h, params={"topic": "finance"}).json()["data"] == []


def test_cancel_a_generating_reply(client, db):
    _o, _s, h = _world(db)
    db.commit()
    conv = client.post(C, headers=h, json={"message": "How long is my runway?"}).json()["data"]
    mid = conv["messages"][1]["id"]

    r = client.post(f"{C}/{conv['id']}/messages/{mid}/cancel", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "cancelled" and r.json()["data"]["content"] == ""
    again = client.post(f"{C}/{conv['id']}/messages/{mid}/cancel", headers=h)
    assert again.status_code == 200  # idempotent

    _work(db)  # the queued job finds a cancelled turn and does nothing
    got = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]
    assert got["messages"][1]["status"] == "cancelled"
    # a new message is accepted straight away
    nxt = client.post(f"{C}/{conv['id']}/messages", headers=h, json={"content": "ok, runway?"})
    assert nxt.status_code == 202


def test_cancel_after_completion_is_409_and_unknown_ids_404(client, db):
    _o, _s, h = _world(db)
    db.commit()
    conv = client.post(C, headers=h, json={"message": "How long is my runway?"}).json()["data"]
    mid = conv["messages"][1]["id"]
    _work(db)
    r = client.post(f"{C}/{conv['id']}/messages/{mid}/cancel", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REPLY_NOT_RUNNING"
    user_mid = conv["messages"][0]["id"]
    res1 = client.post(f"{C}/{conv['id']}/messages/{user_mid}/cancel", headers=h)
    assert res1.status_code == 404
    bad = "00000000-0000-0000-0000-000000000009"
    res2 = client.post(f"{C}/{conv['id']}/messages/{bad}/cancel", headers=h)
    assert res2.status_code == 404


def test_cancel_is_private_to_the_conversation_owner_and_closed_to_other_roles(client, db):
    _o, s, h = _world(db)
    _u, tm = _member(db, s, MembershipRole.team_member)
    _u, mentor = _member(db, s, MembershipRole.mentor)
    _u, acct = _member(db, s, MembershipRole.accountant)
    db.commit()
    conv = client.post(C, headers=h, json={"message": "How long is my runway?"}).json()["data"]
    mid = conv["messages"][1]["id"]
    url = f"{C}/{conv['id']}/messages/{mid}/cancel"
    res3 = client.post(url, headers=tm)
    assert res3.status_code == 404  # a teammate cannot see or stop it
    res4 = client.post(url, headers=mentor)
    assert res4.status_code == 403
    res5 = client.post(url, headers=acct)
    assert res5.status_code == 403
    still = client.get(f"{C}/{conv['id']}", headers=h).json()["data"]
    assert still["messages"][1]["status"] == "generating"


def test_team_member_chat_uses_only_what_their_role_may_read(client, db):
    _o, s, h = _world(db)
    _u, tm = _member(db, s, MembershipRole.team_member)
    db.commit()
    conv = client.post(C, headers=tm, json={"message": "How long is my runway?"}).json()["data"]
    _work(db)
    got = client.get(f"{C}/{conv['id']}", headers=tm).json()["data"]
    assert got["messages"][1]["agent"]["key"] == "finance"
    assert {x["key"] for x in got["messages"][1]["sources"]} == {"profile", "finance"}
    assert client.get(f"{C}/{conv['id']}", headers=h).status_code == 404
