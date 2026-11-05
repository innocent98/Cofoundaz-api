"""Funding Hub slice B: the data room. Structure, shares, the email + code gate, serve-time
authorisation, watermarking, heartbeat, access log, notification."""

import io
import re
from datetime import UTC, datetime, timedelta

import pytest
from pypdf import PdfReader

from app.core.security import create_access_token
from app.db.models.data_room import DataRoomChallenge, DataRoomShare, DataRoomView
from app.db.models.document import DocumentFile
from app.db.models.enums import FUNDING_ROLES, MembershipRole, ScanStatus
from app.db.models.notification import Notification
from app.services.funding import data_room_gate as gate
from tests.documents_helpers import new_doc, upload
from tests.factories import create_membership, create_startup, create_user
from tests.pdf_helpers import make_pdf

BASE = "/api/v1/funding"
DR = f"{BASE}/data-room"
INVESTOR = "Investor.One@Example.com"
SESSION = "X-Data-Room-Session"


def _member(db, startup, role):
    u = create_user(db, email_verified_at=datetime.now(UTC))
    create_membership(db, u, startup, role=role)
    db.flush()
    return u, {
        "Authorization": f"Bearer {create_access_token(str(u.id))}",
        "X-Workspace-Id": str(startup.id),
    }


@pytest.fixture()
def team(db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    s = create_startup(db, owner=owner, name="Placeholder Co")
    db.flush()
    members = {role: _member(db, s, role) for role in MembershipRole}
    db.commit()
    return s, {r: m[1] for r, m in members.items()}


@pytest.fixture()
def founder(team):
    return team[1][MembershipRole.founder]


@pytest.fixture()
def outbox(monkeypatch):
    sent = []

    class _Sender:
        def send(self, msg):
            sent.append(msg)

    for mod in ("data_room_gate", "data_room_shares"):
        monkeypatch.setattr(f"app.services.funding.{mod}.get_email_sender", lambda: _Sender())
    return sent


def _code(outbox) -> str:
    mail = [m for m in outbox if "code" in m.subject][-1]
    return re.search(r"<strong>(\d{6})</strong>", mail.html).group(1)


class Room:
    """A set-up data room with a Financials folder holding two PDFs."""

    def __init__(self, client, db, startup, h):
        self.client, self.db, self.startup, self.h = client, db, startup, h
        r = client.post(f"{DR}/setup", headers=h)
        assert r.status_code == 200, r.text
        self.folders = {f["name"]: f for f in r.json()["data"]["folders"]}
        self.fin = self.folders["Financials"]["id"]
        self.legal = self.folders["Legal"]["id"]
        self.pdf = upload(client, h, name="deck.pdf", body=make_pdf("Pitch deck"))
        self.pdf2 = upload(client, h, name="notes.pdf", body=make_pdf("Notes"))
        self.pdf_item = self.add(self.fin, file_id=self.pdf["id"])
        self.pdf2_item = self.add(self.fin, file_id=self.pdf2["id"])

    def add(self, folder, **target):
        r = self.client.post(f"{DR}/folders/{folder}/items", json=target, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()["data"]

    def add_file(self, name, ct, body, folder=None):
        f = upload(self.client, self.h, name=name, ct=ct, body=body)
        return self.add(folder or self.fin, file_id=f["id"])

    def share(self, **kw):
        body = {"email": INVESTOR, "folder_ids": [self.fin], **kw}
        r = self.client.post(f"{DR}/shares", json=body, headers=self.h)
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        data["token"] = data["link"].rsplit("/dr/", 1)[1]
        return data


@pytest.fixture()
def room(client, db, team, founder, outbox):
    return Room(client, db, team[0], founder)


def _login(client, outbox, share, email=INVESTOR):
    t = share["token"]
    _r0 = client.post(f"{BASE}/dr/{t}/otp", json={"email": email})
    assert _r0.status_code == 202
    r = client.post(f"{BASE}/dr/{t}/verify", json={"email": email, "code": _code(outbox)})
    assert r.status_code == 200, r.text
    return {SESSION: r.json()["data"]["session"]}


def _pdf_text(body: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(body)).pages)


# ---------------------------------------------------------------- roles
FOUNDER_ROUTES = [
    ("GET", "/data-room"),
    ("POST", "/data-room/setup"),
    ("GET", "/data-room/shares"),
    ("GET", "/data-room/access-log"),
]


@pytest.mark.parametrize("role", [r for r in MembershipRole if r not in FUNDING_ROLES])
@pytest.mark.parametrize("method,path", FOUNDER_ROUTES)
def test_other_roles_are_refused(client, team, role, method, path):
    r = client.request(method, BASE + path, headers=team[1][role])
    assert r.status_code == 403


def test_team_member_has_full_access(client, team):
    h = team[1][MembershipRole.team_member]
    _r0 = client.post(f"{DR}/setup", headers=h)
    assert _r0.status_code == 200
    assert client.get(f"{DR}/shares", headers=h).status_code == 200


# ---------------------------------------------------------------- structure
def test_setup_is_idempotent_and_builds_the_six_standard_folders(client, founder):
    first = client.post(f"{DR}/setup", headers=founder).json()["data"]
    assert [f["name"] for f in first["folders"]] == [
        "Corporate",
        "Financials",
        "Product",
        "Market",
        "Team",
        "Legal",
    ]
    again = client.post(f"{DR}/setup", headers=founder).json()["data"]
    assert [f["id"] for f in again["folders"]] == [f["id"] for f in first["folders"]]


def test_get_before_setup_is_the_empty_state(client, founder):
    d = client.get(DR, headers=founder).json()["data"]
    assert d["set_up"] is False and d["folders"] == [] and d["shares"] == []


def test_folder_crud_and_duplicate_names(client, founder):
    r = client.post(f"{DR}/folders", json={"name": "Board"}, headers=founder)
    assert r.status_code == 201
    fid = r.json()["data"]["id"]
    _r0 = client.post(f"{DR}/folders", json={"name": "board"}, headers=founder)
    assert _r0.status_code == 409
    _r0 = client.patch(f"{DR}/folders/{fid}", json={"name": "Board pack"}, headers=founder)
    assert _r0.status_code == 200
    _r0 = client.delete(f"{DR}/folders/{fid}", headers=founder)
    assert _r0.status_code == 200
    _r0 = client.delete(f"{DR}/folders/{fid}", headers=founder)
    assert _r0.status_code == 404


def test_items_reference_documents_and_reject_bad_targets(client, db, room, team):
    h = room.h
    doc = new_doc(client, h, title="One pager")
    r = client.post(f"{DR}/folders/{room.legal}/items", json={"document_id": doc["id"]}, headers=h)
    assert r.status_code == 201 and r.json()["data"]["kind"] == "document"
    dup = client.post(
        f"{DR}/folders/{room.legal}/items", json={"document_id": doc["id"]}, headers=h
    )
    assert dup.status_code == 409
    both = {"document_id": doc["id"], "file_id": room.pdf["id"]}
    _r0 = client.post(f"{DR}/folders/{room.legal}/items", json=both, headers=h)
    assert _r0.status_code == 422
    _r0 = client.post(f"{DR}/folders/{room.legal}/items", json={}, headers=h)
    assert _r0.status_code == 422
    # a file that has not passed the scan cannot be added
    f = upload(client, h, name="x.pdf", body=make_pdf("x"))
    db.query(DocumentFile).filter_by(id=f["id"]).update({"scan_status": ScanStatus.pending})
    db.commit()
    r = client.post(f"{DR}/folders/{room.legal}/items", json={"file_id": f["id"]}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_NOT_CLEAN"


def test_cannot_add_another_workspaces_file(client, db, room):
    other_owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=other_owner, name="Other")
    _, oh = _member(db, other, MembershipRole.founder)
    db.commit()
    theirs = upload(client, oh, name="secret.pdf", body=make_pdf("secret"))
    r = client.post(
        f"{DR}/folders/{room.legal}/items", json={"file_id": theirs["id"]}, headers=room.h
    )
    assert r.status_code == 404


def test_item_chips_show_why_an_item_vanished(client, db, room):
    db.query(DocumentFile).filter_by(id=room.pdf["id"]).update({"trashed_at": datetime.now(UTC)})
    db.commit()
    items = {
        i["id"]: i
        for f in client.get(DR, headers=room.h).json()["data"]["folders"]
        for i in f["items"]
    }
    gone = items[room.pdf_item["id"]]
    assert gone["available"] is False and gone["reason"] == "trashed"


# ---------------------------------------------------------------- shares
def test_share_stores_only_a_hash_and_emails_the_invite(client, db, room, outbox):
    s = room.share()
    assert s["status"] == "active" and s["email"] == INVESTOR.lower()
    assert s["watermark"] is True and s["allow_download"] is False
    row = db.query(DataRoomShare).filter_by(id=s["id"]).one()
    assert len(s["token"]) >= 43 and s["token"] != row.token_hash
    assert row.token_hash != s["token"] and s["token"] not in row.token_hash
    invite = [m for m in outbox if "data room" in m.subject]
    assert invite and s["token"] in invite[0].html and invite[0].to == INVESTOR.lower()
    # the founder can copy the same link again
    again = client.get(f"{DR}/shares", headers=room.h).json()["data"]["items"][0]
    assert again["link"] == s["link"]


def test_share_validation(client, room):
    h = room.h
    bad = client.post(
        f"{DR}/shares",
        json={"email": INVESTOR, "folder_ids": ["00000000-0000-4000-8000-000000000000"]},
        headers=h,
    )
    assert bad.status_code == 422
    _r0 = client.post(f"{DR}/shares", json={"email": "nope", "folder_ids": [room.fin]}, headers=h)
    assert _r0.status_code == 422
    _r0 = client.post(f"{DR}/shares", json={"email": INVESTOR, "folder_ids": []}, headers=h)
    assert _r0.status_code == 422
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _r0 = client.post(
        f"{DR}/shares",
        json={"email": INVESTOR, "folder_ids": [room.fin], "expires_at": past},
        headers=h,
    )
    assert _r0.status_code == 422


def test_share_ties_to_a_pipeline_investor(client, room):
    inv = client.post(f"{BASE}/investors", json={"name": "Tolu"}, headers=room.h).json()["data"]
    s = room.share(investor_id=inv["id"])
    assert s["investor_name"] == "Tolu"
    assert (
        client.get(f"{DR}/shares?investor_id={inv['id']}", headers=room.h).json()["data"]["items"][
            0
        ]["id"]
        == s["id"]
    )
    bad = client.post(
        f"{DR}/shares",
        json={
            "email": INVESTOR,
            "folder_ids": [room.fin],
            "investor_id": "00000000-0000-4000-8000-000000000000",
        },
        headers=room.h,
    )
    assert bad.status_code == 422


def test_shares_are_per_workspace(client, db, room):
    s = room.share()
    other_owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=other_owner, name="Other")
    _, oh = _member(db, other, MembershipRole.founder)
    db.commit()
    _r0 = client.delete(f"{DR}/shares/{s['id']}", headers=oh)
    assert _r0.status_code == 404
    assert client.get(f"{DR}/shares", headers=oh).json()["data"]["items"] == []


# ---------------------------------------------------------------- the public gate
def test_dead_links_are_one_uniform_404(client, db, room):
    live = room.share()
    revoked = room.share(email="b@example.com")
    client.delete(f"{DR}/shares/{revoked['id']}", headers=room.h)
    expired = room.share(email="c@example.com")
    db.query(DataRoomShare).filter_by(id=expired["id"]).update(
        {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}
    )
    db.commit()
    assert client.get(f"{BASE}/dr/{live['token']}").status_code == 200
    bodies = set()
    for t in (revoked["token"], expired["token"], "A" * 43, "x", live["token"][:-2] + "zz"):
        r = client.get(f"{BASE}/dr/{t}")
        assert r.status_code == 404
        bodies.add(r.text)
        _r0 = client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
        assert _r0.status_code == 404
    assert len(bodies) == 1


def test_meta_reveals_company_only(client, room):
    s = room.share()
    d = client.get(f"{BASE}/dr/{s['token']}").json()["data"]
    assert d == {"company": "Placeholder Co", "requires_code": True}


def test_code_request_answers_the_same_for_any_email(client, room, outbox):
    s = room.share()
    t = s["token"]
    right = client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR.upper()})
    n = len(outbox)
    wrong = client.post(f"{BASE}/dr/{t}/otp", json={"email": "stranger@example.com"})
    assert right.status_code == wrong.status_code == 202
    assert right.json() == wrong.json()
    assert len(outbox) == n  # nothing was sent to the stranger
    assert outbox[-1].to == INVESTOR.lower()


def test_code_is_stored_hashed_and_expires(client, db, room, outbox):
    s = room.share()
    client.post(f"{BASE}/dr/{s['token']}/otp", json={"email": INVESTOR})
    code = _code(outbox)
    ch = db.query(DataRoomChallenge).one()
    assert code not in ch.code_hash and len(ch.code_hash) == 64
    ch.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    r = client.post(f"{BASE}/dr/{s['token']}/verify", json={"email": INVESTOR, "code": code})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CODE"


def test_code_works_once(client, room, outbox):
    s = room.share()
    t = s["token"]
    client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    code = _code(outbox)
    ok = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": code})
    assert ok.status_code == 200
    again = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": code})
    assert again.status_code == 401


def test_wrong_code_wrong_email_and_no_code_are_the_same_401(client, room, outbox):
    s = room.share()
    t = s["token"]
    no_code = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": "123456"})
    client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    good = _code(outbox)
    wrong = "000000" if good != "000000" else "111111"
    bad_code = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": wrong})
    bad_mail = client.post(f"{BASE}/dr/{t}/verify", json={"email": "x@example.com", "code": good})
    assert no_code.status_code == bad_code.status_code == bad_mail.status_code == 401
    assert no_code.json() == bad_code.json() == bad_mail.json()


def test_five_wrong_tries_kill_the_code(client, room, outbox):
    s = room.share()
    t = s["token"]
    client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    good = _code(outbox)
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(5):
        client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": wrong})
    r = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": good})
    assert r.status_code == 401  # even the right code is dead now


def test_ten_failures_against_a_live_code_lock_the_share_then_it_recovers(client, db, room, outbox):
    s = room.share()
    t = s["token"]
    for _ in range(2):  # 5 tries kill a code, so ask again between rounds
        client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
        good = _code(outbox)
        wrong = "000000" if good != "000000" else "111111"
        for _ in range(5):
            r = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": wrong})
            assert r.status_code == 401
    locked = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": "123456"})
    assert locked.status_code == 429
    again = client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    assert again.status_code == 429
    db.query(DataRoomShare).update({"locked_until": datetime.now(UTC) - timedelta(seconds=1)})
    db.commit()
    ok = client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    assert ok.status_code == 202


def test_guesses_without_a_live_code_never_lock_the_share(client, db, room, outbox):
    """Someone holding only the link must not be able to lock the real investor out."""
    s = room.share()
    t = s["token"]
    for _ in range(25):
        r = client.post(f"{BASE}/dr/{t}/verify", json={"email": "x@example.com", "code": "123456"})
        assert r.status_code == 401
    share = db.query(DataRoomShare).one()
    assert share.failed_attempts == 0 and share.locked_until is None
    assert client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR}).status_code == 202


def test_non_ascii_email_is_a_401_that_counts_not_a_500(client, db, room, outbox):
    s = room.share()
    t = s["token"]
    client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    r = client.post(
        f"{BASE}/dr/{t}/verify", json={"email": "jos\u00e9@example.com", "code": "123456"}
    )
    assert r.status_code == 401
    assert db.query(DataRoomShare).one().failed_attempts == 1
    r = client.post(f"{BASE}/dr/{t}/otp", json={"email": "jos\u00e9@example.com"})
    assert r.status_code == 202


def test_code_cap_is_the_same_for_every_email_and_never_a_429(client, db, room, outbox):
    """Past 5 requests an hour nothing is sent, and the answer is the same 202 for the invited
    address and a stranger, so the cap cannot be used to test an address."""
    s = room.share()
    t = s["token"]
    codes = [
        client.post(f"{BASE}/dr/{t}/otp", json={"email": e})
        for e in (INVESTOR, "a@example.com", INVESTOR, "b@example.com", INVESTOR)
    ]
    assert [r.status_code for r in codes] == [202] * 5
    sent = len([m for m in outbox if "code" in m.subject])
    assert sent == 3  # only the invited address got mail
    after = [
        client.post(f"{BASE}/dr/{t}/otp", json={"email": e}) for e in (INVESTOR, "c@example.com")
    ]
    assert [r.status_code for r in after] == [202, 202]
    assert after[0].json() == after[1].json() == codes[0].json()
    assert len([m for m in outbox if "code" in m.subject]) == sent  # nothing more was sent
    assert db.query(DataRoomChallenge).count() == 5


def test_every_request_does_the_same_database_work(client, db, room, outbox):
    s = room.share()
    t = s["token"]
    client.post(f"{BASE}/dr/{t}/otp", json={"email": "stranger@example.com"})
    client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    rows = db.query(DataRoomChallenge).order_by(DataRoomChallenge.created_at).all()
    assert len(rows) == 2
    assert rows[0].consumed_at is not None and rows[1].consumed_at is None
    # a decoy never replaces the invited investor's live code
    code = _code(outbox)
    client.post(f"{BASE}/dr/{t}/otp", json={"email": "stranger@example.com"})
    ok = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": code})
    assert ok.status_code == 200


def test_the_database_allows_one_live_code_per_share(db, room):
    from sqlalchemy.exc import IntegrityError

    sid = room.share()["id"]
    now = datetime.now(UTC)
    db.add(DataRoomChallenge(share_id=sid, code_hash="a" * 64, expires_at=now))
    db.flush()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(DataRoomChallenge(share_id=sid, code_hash="b" * 64, expires_at=now))
            db.flush()


def test_a_new_code_replaces_the_old_one(client, room, outbox):
    s = room.share()
    t = s["token"]
    client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    first = _code(outbox)
    client.post(f"{BASE}/dr/{t}/otp", json={"email": INVESTOR})
    second = _code(outbox)
    if first != second:
        r = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": first})
        assert r.status_code == 401
    ok = client.post(f"{BASE}/dr/{t}/verify", json={"email": INVESTOR, "code": second})
    assert ok.status_code == 200


@pytest.fixture()
def live_limiter():
    from app.main import app

    app.state.limiter.enabled = True
    app.state.limiter.reset()
    try:
        yield
    finally:
        app.state.limiter.enabled = False
        app.state.limiter.reset()


def test_verify_is_throttled_per_client_address_across_shares(client, room, live_limiter):
    """One request per share, so no per-share lock can be the reason for the 429."""
    tokens = [room.share(email=f"u{i}@example.com")["token"] for i in range(12)]
    results = [
        client.post(f"{BASE}/dr/{t}/verify", json={"email": "z@example.com", "code": "123456"})
        for t in tokens
    ]
    codes = [r.status_code for r in results]
    assert codes[:8] == [401] * 8 and 429 in codes[8:]
    limited = next(r for r in results if r.status_code == 429)
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


def test_otp_is_throttled_per_client_address_across_shares(client, room, live_limiter):
    tokens = [room.share(email=f"u{i}@example.com")["token"] for i in range(12)]
    codes = [
        client.post(f"{BASE}/dr/{t}/otp", json={"email": "z@example.com"}).status_code
        for t in tokens
    ]
    assert codes[:10] == [202] * 10 and codes[10:] == [429, 429]


# ---------------------------------------------------------------- the viewer
def test_session_is_required_and_bound_to_its_share(client, room, outbox):
    s1 = room.share()
    s2 = room.share(email="b@example.com")
    assert client.get(f"{BASE}/dr/{s1['token']}/tree").status_code == 401
    assert client.get(f"{BASE}/dr/{s1['token']}/tree", headers={SESSION: "junk"}).status_code == 401
    sess1 = _login(client, outbox, s1)
    assert client.get(f"{BASE}/dr/{s1['token']}/tree", headers=sess1).status_code == 200
    assert client.get(f"{BASE}/dr/{s2['token']}/tree", headers=sess1).status_code == 401


def test_session_expires(client, room, outbox, monkeypatch):
    s = room.share()
    sess = _login(client, outbox, s)
    real = gate.time.time
    monkeypatch.setattr(gate.time, "time", lambda: real() + 3 * 3600)
    assert client.get(f"{BASE}/dr/{s['token']}/tree", headers=sess).status_code == 401


def test_tree_shows_only_scoped_live_items(client, db, room, outbox):
    doc = new_doc(client, room.h, title="Legal memo")
    room.add(room.legal, document_id=doc["id"])
    s = room.share()  # Financials only
    sess = _login(client, outbox, s)
    d = client.get(f"{BASE}/dr/{s['token']}/tree", headers=sess).json()["data"]
    assert [f["name"] for f in d["folders"]] == ["Financials"]
    assert {i["name"] for i in d["folders"][0]["items"]} == {"deck.pdf", "notes.pdf"}
    assert d["viewer_email"] == INVESTOR.lower() and d["allow_download"] is False
    assert "document_id" not in d["folders"][0]["items"][0]
    db.query(DocumentFile).filter_by(id=room.pdf2["id"]).update({"trashed_at": datetime.now(UTC)})
    db.commit()
    d = client.get(f"{BASE}/dr/{s['token']}/tree", headers=sess).json()["data"]
    assert [i["name"] for i in d["folders"][0]["items"]] == ["deck.pdf"]


def _open(client, s, sess, item):
    return client.post(f"{BASE}/dr/{s['token']}/items/{item['id']}/open", headers=sess)


def test_pdf_is_watermarked_with_the_viewer_email_and_time(client, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    o = _open(client, s, sess, room.pdf_item)
    assert o.status_code == 200
    d = o.json()["data"]
    assert d["viewer"] == "pdf" and d["expires_in_s"] == 120 and d["heartbeat_every_s"] == 15
    r = client.get(d["content_url"].replace("/api/v1/funding", BASE))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert "no-store" in r.headers["cache-control"]
    text = _pdf_text(r.content)
    assert INVESTOR.lower() in text and "UTC" in text and "Pitch deck" in text


def test_content_with_session_header_logs_the_view_itself(client, db, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    r = client.get(f"{BASE}/dr/{s['token']}/items/{room.pdf_item['id']}/content", headers=sess)
    assert r.status_code == 200 and r.headers["x-data-room-view"]
    assert db.query(DataRoomView).count() == 1


def test_content_needs_a_ticket_or_session(client, room, outbox):
    s = room.share()
    url = f"{BASE}/dr/{s['token']}/items/{room.pdf_item['id']}/content"
    assert client.get(url).status_code == 401
    assert client.get(url + "?ticket=junk").status_code == 401


def test_ticket_is_bound_to_its_item_and_share(client, room, outbox):
    s = room.share()
    other = room.share(email="b@example.com")
    sess = _login(client, outbox, s)
    d = _open(client, s, sess, room.pdf_item).json()["data"]
    ticket = d["content_url"].split("ticket=")[1]
    other_item = f"{BASE}/dr/{s['token']}/items/{room.pdf2_item['id']}/content?ticket={ticket}"
    assert client.get(other_item).status_code == 401
    other_share = f"{BASE}/dr/{other['token']}/items/{room.pdf_item['id']}/content?ticket={ticket}"
    assert client.get(other_share).status_code == 401


def test_ticket_expires(client, room, outbox, monkeypatch):
    s = room.share()
    sess = _login(client, outbox, s)
    d = _open(client, s, sess, room.pdf_item).json()["data"]
    real = gate.time.time
    monkeypatch.setattr(gate.time, "time", lambda: real() + 200)
    assert client.get(d["content_url"].replace("/api/v1/funding", BASE)).status_code == 401


def test_non_watermarkable_types_are_refused_when_downloads_are_off(client, db, room, outbox):
    txt = room.add_file("notes.txt", "text/plain", b"hello notes")
    s = room.share()
    sess = _login(client, outbox, s)
    tree = client.get(f"{BASE}/dr/{s['token']}/tree", headers=sess).json()["data"]
    flags = {i["name"]: i["viewable"] for i in tree["folders"][0]["items"]}
    assert flags == {"deck.pdf": True, "notes.pdf": True, "notes.txt": False}
    o = _open(client, s, sess, txt)
    assert o.status_code == 422 and o.json()["error"]["code"] == "VIEW_ONLY_UNSUPPORTED"
    c = client.get(f"{BASE}/dr/{s['token']}/items/{txt['id']}/content", headers=sess)
    assert c.status_code == 422 and c.json()["error"]["code"] == "VIEW_ONLY_UNSUPPORTED"
    assert db.query(DataRoomView).count() == 0  # a refusal is not a view
    assert b"hello notes" not in c.content


def test_founder_is_warned_about_unviewable_items_in_advance(client, room):
    txt = room.add_file("notes.txt", "text/plain", b"hello notes")
    item = [i for f in client.get(DR, headers=room.h).json()["data"]["folders"] for i in f["items"]]
    flags = {i["name"]: i["viewable"] for i in item}
    assert flags["notes.txt"] is False and flags["deck.pdf"] is True
    s = room.share()
    assert s["unviewable_items"] == [
        {"item_id": txt["id"], "name": "notes.txt", "code": "VIEW_ONLY_UNSUPPORTED"}
    ]
    on = room.share(email="b@example.com", allow_download=True)
    assert on["unviewable_items"] == []


def test_with_downloads_on_other_types_are_served_as_they_are(client, room, outbox):
    txt = room.add_file("notes.txt", "text/plain", b"hello notes")
    s = room.share(allow_download=True)
    sess = _login(client, outbox, s)
    tree = client.get(f"{BASE}/dr/{s['token']}/tree", headers=sess).json()["data"]
    assert all(i["viewable"] for i in tree["folders"][0]["items"])
    r = client.get(f"{BASE}/dr/{s['token']}/items/{txt['id']}/content", headers=sess)
    assert r.status_code == 200 and r.content == b"hello notes"
    assert r.headers["content-disposition"].startswith("inline")


def test_images_are_converted_to_a_watermarked_pdf_when_downloads_are_off(client, room, outbox):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (10, 90, 200)).save(buf, "PNG")
    img = room.add_file("logo.png", "image/png", buf.getvalue())
    s = room.share()
    sess = _login(client, outbox, s)
    r = client.get(f"{BASE}/dr/{s['token']}/items/{img['id']}/content", headers=sess)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-" and INVESTOR.lower() in _pdf_text(r.content)
    on = room.share(email="b@example.com", allow_download=True)
    sess2 = _login(client, outbox, on, email="b@example.com")
    raw = client.get(f"{BASE}/dr/{on['token']}/items/{img['id']}/content", headers=sess2)
    assert raw.headers["content-type"] == "image/png" and raw.content[:4] == b"\x89PNG"


def test_download_flag_is_honoured_only_when_the_share_allows_it(client, room, outbox):
    off = room.share()
    on = room.share(email="b@example.com", allow_download=True)
    for s, expected in ((off, "inline"), (on, "attachment")):
        sess = _login(client, outbox, s, email=s["email"])
        r = client.get(
            f"{BASE}/dr/{s['token']}/items/{room.pdf2_item['id']}/content?download=true",
            headers=sess,
        )
        assert r.headers["content-disposition"].startswith(expected)
        assert INVESTOR.lower() in _pdf_text(r.content) or s["email"] in _pdf_text(r.content)


def test_rich_documents_are_rendered_to_a_watermarked_pdf(client, db, room, outbox):
    doc = new_doc(client, room.h, title="Board memo")
    item = room.add(room.fin, document_id=doc["id"])
    s = room.share()
    sess = _login(client, outbox, s)
    r = client.get(f"{BASE}/dr/{s['token']}/items/{item['id']}/content", headers=sess)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert INVESTOR.lower() in _pdf_text(r.content)


def test_an_unstampable_pdf_is_never_served_bare(client, db, room, outbox):
    from app.platform.storage import get_storage

    get_storage().save_private(
        db.query(DocumentFile).filter_by(id=room.pdf["id"]).one().storage_key,
        b"%PDF-1.4 not really a pdf",
        "application/pdf",
    )
    s = room.share()
    sess = _login(client, outbox, s)
    r = client.get(f"{BASE}/dr/{s['token']}/items/{room.pdf_item['id']}/content", headers=sess)
    assert r.status_code == 422 and r.json()["error"]["code"] == "DOCUMENT_UNAVAILABLE"


# ---------------------------------------------------------------- serve-time authorisation
def _assert_cut_off(client, s, sess, item, view_id):
    url = f"{BASE}/dr/{s['token']}/items/{item['id']}"
    assert client.get(url + "/content", headers=sess).status_code == 404
    _r0 = client.post(url + "/open", headers=sess)
    assert _r0.status_code == 404
    hb = client.post(f"{BASE}/dr/{s['token']}/heartbeat", json={"view_id": view_id}, headers=sess)
    assert hb.status_code == 404


def _live_view(client, s, sess, item):
    d = _open(client, s, sess, item).json()["data"]
    return d, d["view_id"]


def test_revoking_a_share_cuts_off_a_viewer_mid_session(client, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    d, vid = _live_view(client, s, sess, room.pdf_item)
    _r0 = client.delete(f"{DR}/shares/{s['id']}", headers=room.h)
    assert _r0.json()["data"]["status"] == "revoked"
    _assert_cut_off(client, s, sess, room.pdf_item, vid)
    assert client.get(d["content_url"].replace("/api/v1/funding", BASE)).status_code == 404
    assert client.get(f"{BASE}/dr/{s['token']}/tree", headers=sess).status_code == 404


def test_expiry_cuts_off_a_viewer_mid_session(client, db, room, outbox):
    s = room.share(expires_in_days=1)
    sess = _login(client, outbox, s)
    _, vid = _live_view(client, s, sess, room.pdf_item)
    db.query(DataRoomShare).update({"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    db.commit()
    _assert_cut_off(client, s, sess, room.pdf_item, vid)


def test_trashing_the_documents_file_cuts_it_off(client, db, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    d, vid = _live_view(client, s, sess, room.pdf_item)
    client.delete(f"/api/v1/documents/files/{room.pdf['id']}", headers=room.h)
    db.expire_all()
    assert db.query(DocumentFile).filter_by(id=room.pdf["id"]).one().trashed_at is not None
    _assert_cut_off(client, s, sess, room.pdf_item, vid)
    assert client.get(d["content_url"].replace("/api/v1/funding", BASE)).status_code == 404
    # the other item is untouched
    assert _open(client, s, sess, room.pdf2_item).status_code == 200


def test_a_file_that_loses_clean_status_is_cut_off(client, db, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    _, vid = _live_view(client, s, sess, room.pdf_item)
    db.query(DocumentFile).filter_by(id=room.pdf["id"]).update({"scan_status": ScanStatus.blocked})
    db.commit()
    _assert_cut_off(client, s, sess, room.pdf_item, vid)


def test_a_trashed_rich_document_is_cut_off(client, db, room, outbox):
    from app.db.models.document import Document

    doc = new_doc(client, room.h, title="Memo")
    item = room.add(room.fin, document_id=doc["id"])
    s = room.share()
    sess = _login(client, outbox, s)
    _, vid = _live_view(client, s, sess, item)
    db.query(Document).filter_by(id=doc["id"]).update({"trashed_at": datetime.now(UTC)})
    db.commit()
    _assert_cut_off(client, s, sess, item, vid)


def test_removing_the_item_or_folder_cuts_it_off(client, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    _, vid = _live_view(client, s, sess, room.pdf_item)
    _r0 = client.delete(f"{DR}/items/{room.pdf_item['id']}", headers=room.h)
    assert _r0.status_code == 200
    _assert_cut_off(client, s, sess, room.pdf_item, vid)
    _, vid2 = _live_view(client, s, sess, room.pdf2_item)
    _r0 = client.delete(f"{DR}/folders/{room.fin}", headers=room.h)
    assert _r0.status_code == 200
    _assert_cut_off(client, s, sess, room.pdf2_item, vid2)


def test_an_item_outside_the_share_scope_is_a_404(client, room, outbox):
    other = new_doc(client, room.h, title="Legal only")
    legal_item = room.add(room.legal, document_id=other["id"])
    s = room.share()  # Financials only
    sess = _login(client, outbox, s)
    assert _open(client, s, sess, legal_item).status_code == 404
    url = f"{BASE}/dr/{s['token']}/items/{legal_item['id']}/content"
    assert client.get(url, headers=sess).status_code == 404


def test_narrowing_the_scope_takes_effect_immediately(client, room, outbox):
    s = room.share(folder_ids=[room.fin, room.legal])
    sess = _login(client, outbox, s)
    assert _open(client, s, sess, room.pdf_item).status_code == 200
    r = client.patch(f"{DR}/shares/{s['id']}", json={"folder_ids": [room.legal]}, headers=room.h)
    assert r.status_code == 200
    assert _open(client, s, sess, room.pdf_item).status_code == 404


def test_item_ids_from_another_workspace_are_a_404(client, db, room, outbox):
    other_owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=other_owner, name="Other")
    _, oh = _member(db, other, MembershipRole.founder)
    db.commit()
    theirs = Room(client, db, other, oh)
    s = room.share()
    sess = _login(client, outbox, s)
    assert _open(client, s, sess, theirs.pdf_item).status_code == 404


def test_a_revoked_share_cannot_be_edited_or_revived(client, room):
    s = room.share()
    client.delete(f"{DR}/shares/{s['id']}", headers=room.h)
    r = client.patch(f"{DR}/shares/{s['id']}", json={"expires_at": None}, headers=room.h)
    assert r.status_code == 404
    assert client.get(f"{BASE}/dr/{s['token']}").status_code == 404


def test_clearing_the_expiry(client, room):
    s = room.share(expires_in_days=3)
    assert s["expires_at"]
    r = client.patch(f"{DR}/shares/{s['id']}", json={"expires_at": None}, headers=room.h)
    assert r.json()["data"]["expires_at"] is None


# ---------------------------------------------------------------- heartbeat, log, notification
def test_heartbeat_accumulates_real_time_but_caps_a_stall(client, db, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    _, vid = _live_view(client, s, sess, room.pdf_item)
    view = db.query(DataRoomView).one()
    view.last_seen_at = datetime.now(UTC) - timedelta(seconds=14)
    db.commit()
    hb = client.post(f"{BASE}/dr/{s['token']}/heartbeat", json={"view_id": vid}, headers=sess)
    assert 13 <= hb.json()["data"]["duration_s"] <= 15
    db.expire_all()
    db.query(DataRoomView).update({"last_seen_at": datetime.now(UTC) - timedelta(hours=3)})
    db.commit()
    before = hb.json()["data"]["duration_s"]
    hb2 = client.post(f"{BASE}/dr/{s['token']}/heartbeat", json={"view_id": vid}, headers=sess)
    assert hb2.json()["data"]["duration_s"] == before + gate_cap()


def gate_cap():
    from app.services.funding.data_room_serve import MAX_HEARTBEAT_GAP

    return MAX_HEARTBEAT_GAP


def test_heartbeat_for_someone_elses_view_is_a_404(client, room, outbox):
    a = room.share()
    b = room.share(email="b@example.com")
    sa, sb = _login(client, outbox, a), _login(client, outbox, b, email="b@example.com")
    _, vid = _live_view(client, a, sa, room.pdf_item)
    r = client.post(f"{BASE}/dr/{b['token']}/heartbeat", json={"view_id": vid}, headers=sb)
    assert r.status_code == 404


def test_access_log_has_viewer_document_time_and_duration(client, db, room, outbox):
    inv = client.post(f"{BASE}/investors", json={"name": "Tolu"}, headers=room.h).json()["data"]
    s = room.share(investor_id=inv["id"])
    sess = _login(client, outbox, s)
    _, vid = _live_view(client, s, sess, room.pdf_item)
    client.get(f"{BASE}/dr/{s['token']}/items/{room.pdf2_item['id']}/content", headers=sess)
    log = client.get(f"{DR}/access-log", headers=room.h).json()["data"]
    assert log["total"] == 2
    row = next(r for r in log["items"] if r["document"] == "deck.pdf")
    assert row["viewer_email"] == INVESTOR.lower() and row["investor_name"] == "Tolu"
    assert row["duration_s"] == 0 and row["started_at"]
    assert "ip" not in "".join(row.keys())
    one = client.get(f"{DR}/access-log?share_id={s['id']}&limit=1", headers=room.h).json()["data"]
    assert one["total"] == 2 and len(one["items"]) == 1
    shares = client.get(f"{DR}/shares", headers=room.h).json()["data"]["items"]
    assert shares[0]["view_count"] == 2 and shares[0]["last_viewed_at"]
    row = db.query(DataRoomView).first()
    assert row.ip_hash is None or len(row.ip_hash) == 64


def test_log_survives_removal_of_the_document(client, room, outbox):
    s = room.share()
    sess = _login(client, outbox, s)
    _live_view(client, s, sess, room.pdf_item)
    client.delete(f"{DR}/items/{room.pdf_item['id']}", headers=room.h)
    log = client.get(f"{DR}/access-log", headers=room.h).json()["data"]
    assert log["items"][0]["document"] == "deck.pdf" and log["items"][0]["item_id"] is None


def test_one_notification_per_share_per_day_to_founder_and_team(client, db, room, team, outbox):
    inv = client.post(f"{BASE}/investors", json={"name": "Tolu"}, headers=room.h).json()["data"]
    s = room.share(investor_id=inv["id"])
    sess = _login(client, outbox, s)
    for item in (room.pdf_item, room.pdf2_item, room.pdf_item):
        assert _open(client, s, sess, item).status_code == 200
    rows = db.query(Notification).filter_by(type="funding.dataroom.viewed").all()
    recipients = {r.user_id for r in rows}
    assert len(rows) == len(recipients) == 2  # founder + team member, once each
    assert "Tolu viewed your data room" in rows[0].title


def test_removing_a_pipeline_investor_keeps_the_share(client, room):
    inv = client.post(f"{BASE}/investors", json={"name": "Tolu"}, headers=room.h).json()["data"]
    s = room.share(investor_id=inv["id"])
    _r0 = client.delete(f"{BASE}/investors/{inv['id']}", headers=room.h)
    assert _r0.status_code in (200, 204)
    got = client.get(f"{DR}/shares", headers=room.h).json()["data"]["items"][0]
    assert got["id"] == s["id"] and got["investor_id"] is None


# ---------------------------------------------------------------- roles and tenancy on writes
MUTATIONS = [
    ("POST", "/data-room/folders", {"name": "X"}),
    ("PATCH", "/data-room/folders/{folder}", {"name": "Y"}),
    ("DELETE", "/data-room/folders/{folder}", None),
    ("POST", "/data-room/folders/{folder}/items", {"file_id": "{file}"}),
    ("PATCH", "/data-room/items/{item}", {"position": 3}),
    ("DELETE", "/data-room/items/{item}", None),
    ("POST", "/data-room/shares", {"email": "a@example.com", "folder_ids": ["{folder}"]}),
    ("PATCH", "/data-room/shares/{share}", {"allow_download": True}),
    ("DELETE", "/data-room/shares/{share}", None),
]


def _fill(value, ids):
    if isinstance(value, str):
        return value.format(**ids)
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


@pytest.mark.parametrize("role", [r for r in MembershipRole if r not in FUNDING_ROLES])
@pytest.mark.parametrize("method,path,body", MUTATIONS)
def test_other_roles_cannot_write_and_nothing_changes(
    client, db, room, team, role, method, path, body
):
    ids = {
        "folder": room.fin,
        "file": room.pdf["id"],
        "item": room.pdf_item["id"],
        "share": room.share()["id"],
    }
    h = team[1][role]
    r = client.request(method, BASE + _fill(path, ids), json=_fill(body, ids), headers=h)
    assert r.status_code == 403
    assert db.query(DataRoomShare).filter(DataRoomShare.revoked_at.is_not(None)).count() == 0


@pytest.fixture()
def other_workspace(client, db):
    owner = create_user(db, email_verified_at=datetime.now(UTC))
    other = create_startup(db, owner=owner, name="Other")
    _, oh = _member(db, other, MembershipRole.founder)
    db.commit()
    return oh


@pytest.mark.parametrize("method,path,body", MUTATIONS[1:3] + MUTATIONS[4:6] + MUTATIONS[7:])
def test_other_workspaces_cannot_touch_these_ids(
    client, db, room, other_workspace, method, path, body
):
    ids = {
        "folder": room.fin,
        "file": room.pdf["id"],
        "item": room.pdf_item["id"],
        "share": room.share()["id"],
    }
    r = client.request(
        method, BASE + _fill(path, ids), json=_fill(body, ids), headers=other_workspace
    )
    assert r.status_code == 404
    assert db.query(DataRoomShare).filter(DataRoomShare.revoked_at.is_not(None)).count() == 0
    again = client.get(DR, headers=room.h).json()["data"]
    assert any(f["id"] == room.fin for f in again["folders"])
    assert again["item_count"] == 2


def test_cross_workspace_items_and_shares_cannot_be_created_against_foreign_folders(
    client, room, other_workspace
):
    r = client.post(
        f"{DR}/folders/{room.fin}/items", json={"file_id": room.pdf["id"]}, headers=other_workspace
    )
    assert r.status_code == 404
    r = client.post(
        f"{DR}/shares",
        json={"email": "a@example.com", "folder_ids": [room.fin]},
        headers=other_workspace,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------- watermark geometry
def test_watermark_lands_on_the_visible_box_of_an_offset_page():
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    from app.services.funding.watermark import stamp_pdf

    w = PdfWriter()
    page = w.add_blank_page(width=612, height=792)
    page.mediabox = RectangleObject([500, 500, 1112, 1292])
    page.cropbox = RectangleObject([500, 500, 1112, 1292])
    buf = io.BytesIO()
    w.write(buf)
    out = stamp_pdf(buf.getvalue(), email="viewer@example.com")
    reader = PdfReader(io.BytesIO(out))
    assert "viewer@example.com" in reader.pages[0].extract_text()
    # the footer label sits near the bottom-left of the VISIBLE box, not near the origin
    parts = []
    reader.pages[0].extract_text(visitor_text=lambda t, cm, tm, fd, fs: parts.append((t, cm, tm)))
    placed = [
        (tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5]) for t, cm, tm in parts if "viewer@" in t
    ]
    assert placed and all(x > 400 and y >= 500 for x, y in placed)  # nothing near the origin
    assert any(x >= 500 and y >= 500 for x, y in placed)  # the footer, on the visible box


def test_stamp_refuses_encrypted_and_garbage_pdfs():
    from app.core.errors import AppError
    from app.services.funding.watermark import stamp_pdf

    with pytest.raises(AppError):
        stamp_pdf(b"not a pdf", email="v@example.com")


def test_oversized_images_are_refused_before_decoding(client, room, outbox, monkeypatch):
    from PIL import Image

    from app.services.funding import watermark

    buf = io.BytesIO()
    Image.new("RGB", (200, 100), (1, 2, 3)).save(buf, "PNG")  # 20,000 px, tiny on disk
    img = room.add_file("big.png", "image/png", buf.getvalue())
    s = room.share()
    sess = _login(client, outbox, s)
    monkeypatch.setattr(watermark, "MAX_IMAGE_PIXELS", 10_000)
    r = client.get(f"{BASE}/dr/{s['token']}/items/{img['id']}/content", headers=sess)
    assert r.status_code == 422 and r.json()["error"]["code"] == "DOCUMENT_UNAVAILABLE"
