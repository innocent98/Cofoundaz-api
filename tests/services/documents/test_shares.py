from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import NotFound
from app.db.models.document import Document
from app.db.models.enums import ShareAccess
from app.services.documents.shares import (
    create_share,
    list_shares,
    list_workspace_shares,
    open_shared,
    revoke_share,
    serialize_share,
)
from tests.factories import create_membership, create_startup, create_user


def _doc(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s)
    doc = Document(startup_id=s.id, created_by_id=u.id, title="Plan")
    db.add(doc)
    db.flush()
    return u, s, m, doc


def test_create_returns_raw_and_sets_expiry(db):
    u, _s, _m, doc = _doc(db)
    share, raw = create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="x@y.com",
        access_level=ShareAccess.view,
        expires_in_days=30,
    )
    assert raw and share.token_hash != raw  # stored hashed, not raw
    assert share.expires_at is not None
    assert (share.expires_at - datetime.now(UTC)) > timedelta(days=29)


def test_create_no_expiry_when_falsy(db):
    u, _s, _m, doc = _doc(db)
    share, _raw = create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="x@y.com",
        access_level=ShareAccess.comment,
        expires_in_days=0,
    )
    assert share.expires_at is None


def test_open_valid_sets_last_viewed(db):
    u, _s, _m, doc = _doc(db)
    _share, raw = create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="x@y.com",
        access_level=ShareAccess.view,
        expires_in_days=30,
    )
    opened = open_shared(db, raw)
    assert opened.document_id == doc.id and opened.last_viewed_at is not None


def test_open_unknown_expired_revoked_all_404(db):
    u, _s, _m, doc = _doc(db)
    with pytest.raises(NotFound):
        open_shared(db, "no-such-token")

    share_exp, raw_exp = create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="x@y.com",
        access_level=ShareAccess.view,
        expires_in_days=30,
    )
    share_exp.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    with pytest.raises(NotFound):
        open_shared(db, raw_exp)

    share_rev, raw_rev = create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="x@y.com",
        access_level=ShareAccess.view,
        expires_in_days=30,
    )
    revoke_share(db, share_rev)
    with pytest.raises(NotFound):
        open_shared(db, raw_rev)


def test_list_and_workspace_list(db):
    u, s, _m, doc = _doc(db)
    create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="a@y.com",
        access_level=ShareAccess.view,
        expires_in_days=30,
    )
    create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="b@y.com",
        access_level=ShareAccess.view,
        expires_in_days=0,
    )
    assert len(list_shares(db, doc)) == 2
    assert len(list_workspace_shares(db, s)) == 2


def test_serialize_omits_token_and_derives_status(db):
    u, _s, _m, doc = _doc(db)
    share, _raw = create_share(
        db,
        doc,
        shared_by_id=u.id,
        email="x@y.com",
        access_level=ShareAccess.view,
        expires_in_days=30,
    )
    out = serialize_share(share)
    assert "token_hash" not in out and out["status"] == "active"
    revoke_share(db, share)
    assert serialize_share(share)["status"] == "revoked"
