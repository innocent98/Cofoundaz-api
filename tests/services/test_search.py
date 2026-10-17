from datetime import UTC, date, datetime

import pytest

from app.core.security import create_access_token
from app.db.models.ai_chat import AiConversation
from app.db.models.document import Document
from app.db.models.enums import (
    CampaignObjective,
    MembershipRole,
    RiskLevel,
    TransactionDirection,
)
from app.db.models.finance import Transaction
from app.db.models.marketing import Campaign
from app.db.models.validation import Assumption
from app.services.search.service import normalize_query, search
from tests.factories import create_membership, create_startup, create_user


@pytest.fixture
def ws(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    db.add_all(
        [
            Document(startup_id=s.id, title="Pricing memo for Acme"),
            Transaction(
                startup_id=s.id,
                date=date.today(),
                description="Acme pricing consultant",
                amount_minor=100,
                currency="NGN",
                direction=TransactionDirection.outflow,
            ),
            Campaign(startup_id=s.id, name="Pricing launch", objective=CampaignObjective.leads),
            Assumption(startup_id=s.id, statement="Buyers accept our pricing", risk=RiskLevel.high),
            AiConversation(startup_id=s.id, created_by=u.id, title="Pricing strategy chat"),
        ]
    )
    db.flush()
    return u, s


def _types(out):
    return sorted({r["type"] for r in out["results"]})


def test_founder_finds_every_source(db, ws):
    u, s = ws
    out = search(db, s, u, MembershipRole.founder, "pricing")
    assert _types(out) == [
        "assumption",
        "campaign",
        "conversation",
        "document",
        "page",
        "transaction",
    ]
    doc = next(r for r in out["results"] if r["type"] == "document")
    assert doc["href"] == f"/documents/{doc['id']}"
    conv = next(r for r in out["results"] if r["type"] == "conversation")
    assert conv["href"] == f"/ai?c={conv['id']}"


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (MembershipRole.accountant, ["document", "page", "transaction"]),
        (MembershipRole.investor, ["document", "page"]),
        (MembershipRole.mentor, ["document", "page"]),
        (MembershipRole.team_member, ["assumption", "campaign", "document", "page", "transaction"]),
    ],
)
def test_sources_follow_module_gates(db, ws, role, expected):
    u, s = ws
    other = create_user(db)  # a different member: conversations are private to their creator
    assert _types(search(db, s, other, role, "pricing")) == expected
    del u


def test_pages_are_role_filtered(db, ws):
    u, s = ws
    titles = {r["title"] for r in search(db, s, u, MembershipRole.investor, "fin")["results"]}
    assert "Finance Hub" not in titles
    titles = {r["title"] for r in search(db, s, u, MembershipRole.accountant, "fin")["results"]}
    assert "Finance Hub" in titles


def test_other_workspaces_never_leak(db, ws):
    _u, s = ws
    stranger = create_user(db)
    other = create_startup(db, owner=stranger)
    out = search(db, other, stranger, MembershipRole.founder, "acme")
    assert [r for r in out["results"] if r["type"] != "page"] == []
    del s


def test_like_metacharacters_are_literal(db, ws):
    u, s = ws
    assert search(db, s, u, MembershipRole.founder, "%_")["results"] == []
    db.add(Document(startup_id=s.id, title="Growth 100%_ plan"))
    db.flush()
    hits = search(db, s, u, MembershipRole.founder, "100%_")["results"]
    assert [r["title"] for r in hits] == ["Growth 100%_ plan"]


def test_short_or_blank_queries_return_nothing(db, ws):
    u, s = ws
    assert normalize_query("  a ") is None
    assert normalize_query(None) is None
    assert normalize_query("  two   words ") == "two words"
    assert search(db, s, u, MembershipRole.founder, "p") == {"query": "p", "results": []}


def test_per_source_limit(db, ws):
    u, s = ws
    db.add_all([Document(startup_id=s.id, title=f"Pricing v{i}") for i in range(8)])
    db.flush()
    docs = [
        r
        for r in search(db, s, u, MembershipRole.founder, "pricing")["results"]
        if r["type"] == "document"
    ]
    assert len(docs) == 5


def test_endpoint(client, db):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=u)
    create_membership(db, u, s, role=MembershipRole.founder)
    db.add(Document(startup_id=s.id, title="Board deck"))
    db.commit()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}", "X-Workspace-Id": str(s.id)}
    r = client.get("/api/v1/search", params={"q": "board"}, headers=h)
    assert r.status_code == 200, r.text
    results = r.json()["data"]["results"]
    assert [x["title"] for x in results if x["type"] == "document"] == ["Board deck"]
    assert "Dashboard" in [x["title"] for x in results if x["type"] == "page"]  # substring match
    assert client.get("/api/v1/search", params={"q": "x" * 201}, headers=h).status_code == 422
    assert client.get("/api/v1/search", params={"q": "board"}).status_code == 401
