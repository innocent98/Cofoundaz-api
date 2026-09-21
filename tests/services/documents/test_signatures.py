from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import NotFound, SignatureNotActive
from app.db.models.document import DocumentFile
from app.db.models.enums import SignatureRequestStatus
from app.services.documents.signatures import (
    cancel_request,
    create_request,
    open_for_signing,
    record_signature,
    request_status,
    serialize_request,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s)
    f = DocumentFile(
        startup_id=s.id,
        uploaded_by_id=u.id,
        filename="nda.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_key=f"documents/{s.id}/x.pdf",
        url="file:///x.pdf",
    )
    db.add(f)
    db.flush()
    return u, s, m, f


def _make(db, u, f, signers=(("a@x.com", "A"), ("b@x.com", "B")), days=14):
    return create_request(
        db,
        f,
        created_by_id=u.id,
        title="NDA",
        signers=[{"email": e, "name": n} for e, n in signers],
        expires_in_days=days,
    )


def test_create_persists_request_and_signers_with_tokens(db):
    u, _s, _m, f = _ctx(db)
    req, pairs = _make(db, u, f)
    assert req.status == SignatureRequestStatus.awaiting
    assert req.expires_at is not None and len(pairs) == 2
    raws = [raw for _s, raw in pairs]
    assert len(set(raws)) == 2 and all(r for r in raws)  # distinct, non-empty


def test_create_requires_at_least_one_signer(db):
    u, _s, _m, f = _ctx(db)
    with pytest.raises(Exception) as e:
        create_request(db, f, created_by_id=u.id, title="x", signers=[], expires_in_days=14)
    assert getattr(e.value, "http_status", None) == 422


def test_open_and_sign_first_of_two_stays_awaiting(db):
    u, _s, _m, f = _ctx(db)
    req, pairs = _make(db, u, f)
    _signer_a, raw_a = pairs[0]
    signer = open_for_signing(db, raw_a)
    updated = record_signature(db, signer, typed_name="Alice A", ip="1.2.3.4", user_agent="pytest")
    assert updated.status == SignatureRequestStatus.awaiting  # b still pending
    assert signer.signed_at is not None and signer.signed_name == "Alice A"
    assert signer.signed_ip == "1.2.3.4"


def test_signing_last_signer_completes(db):
    u, _s, _m, f = _ctx(db)
    req, pairs = _make(db, u, f)
    for _signer, raw in pairs:
        record_signature(db, open_for_signing(db, raw), typed_name="X", ip=None, user_agent=None)
    db.refresh(req)
    assert req.status == SignatureRequestStatus.complete and req.completed_at is not None


def test_open_unknown_expired_cancelled_signed_all_404(db):
    u, _s, m, f = _ctx(db)
    with pytest.raises(NotFound):
        open_for_signing(db, "no-token")

    req_exp, pairs_exp = _make(db, u, f, days=14)
    req_exp.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    with pytest.raises(NotFound):
        open_for_signing(db, pairs_exp[0][1])

    req_c, pairs_c = _make(db, u, f)
    cancel_request(db, req_c)
    with pytest.raises(NotFound):
        open_for_signing(db, pairs_c[0][1])

    req_s, pairs_s = _make(db, u, f)
    record_signature(
        db, open_for_signing(db, pairs_s[0][1]), typed_name="X", ip=None, user_agent=None
    )
    with pytest.raises(NotFound):  # same signer can't sign twice
        open_for_signing(db, pairs_s[0][1])


def test_cancel_complete_request_409(db):
    u, _s, _m, f = _ctx(db)
    req, pairs = _make(db, u, f, signers=(("only@x.com", "O"),))
    record_signature(
        db, open_for_signing(db, pairs[0][1]), typed_name="O", ip=None, user_agent=None
    )
    db.refresh(req)
    with pytest.raises(SignatureNotActive):
        cancel_request(db, req)


def test_reissue_unsigned_rotates_tokens(db):
    u, _s, _m, f = _ctx(db)
    req, pairs = _make(db, u, f)
    old_a = pairs[0][0].token_hash
    from app.services.documents.signatures import reissue_unsigned

    reissued = reissue_unsigned(db, req)
    assert len(reissued) == 2  # both still unsigned
    assert pairs[0][0].token_hash != old_a  # rotated
    # the OLD raw token no longer opens
    with pytest.raises(NotFound):
        open_for_signing(db, pairs[0][1])
    # the NEW raw token does
    new_raw = next(raw for s, raw in reissued if s.id == pairs[0][0].id)
    assert open_for_signing(db, new_raw).id == pairs[0][0].id


def test_request_status_derives_expired(db):
    u, _s, _m, f = _ctx(db)
    req, _pairs = _make(db, u, f)
    req.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    assert request_status(req) == "expired"


def test_serialize_counts_and_omits_token(db):
    u, _s, _m, f = _ctx(db)
    req, pairs = _make(db, u, f)
    record_signature(
        db, open_for_signing(db, pairs[0][1]), typed_name="A", ip=None, user_agent=None
    )
    out = serialize_request(db, req)
    assert out["signed_count"] == 1 and out["total"] == 2
    assert out["filename"] == "nda.pdf"
    assert all("token_hash" not in s for s in out["signers"])
    assert {s["status"] for s in out["signers"]} == {"signed", "pending"}
