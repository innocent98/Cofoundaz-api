from datetime import UTC, datetime, timedelta

from app.db.models.document import Document, DocumentShare
from app.db.models.enums import ShareAccess
from tests.factories import create_startup, create_user


def test_document_share_round_trip(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    doc = Document(startup_id=s.id, created_by_id=u.id, title="Plan")
    db.add(doc)
    db.flush()
    share = DocumentShare(
        startup_id=s.id,
        document_id=doc.id,
        shared_by_id=u.id,
        email="tayo@lawfirm.ng",
        access_level=ShareAccess.view,
        token_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(share)
    db.flush()
    db.refresh(share)
    assert share.access_level == ShareAccess.view
    assert share.revoked_at is None and share.last_viewed_at is None
    assert share.email == "tayo@lawfirm.ng"
