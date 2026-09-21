from datetime import UTC, datetime, timedelta

from app.db.models.document import DocumentFile, SignatureRequest, SignatureSigner
from app.db.models.enums import SignatureRequestStatus
from tests.factories import create_startup, create_user


def _file(db, s, u):
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
    return f


def test_signature_request_and_signers_round_trip(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    f = _file(db, s, u)
    req = SignatureRequest(
        startup_id=s.id,
        file_id=f.id,
        title="NDA",
        created_by_id=u.id,
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(req)
    db.flush()
    signer = SignatureSigner(
        request_id=req.id,
        email="tayo@x.com",
        name="Tayo",
        token_hash="a" * 64,
        position=0,
    )
    db.add(signer)
    db.flush()
    db.refresh(req)
    db.refresh(signer)
    assert req.status == SignatureRequestStatus.awaiting
    assert req.completed_at is None and req.cancelled_at is None
    assert signer.signed_at is None and signer.position == 0
