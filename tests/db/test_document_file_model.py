from app.db.models.document import DocumentFile
from tests.factories import create_startup, create_user


def test_document_file_round_trip(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    f = DocumentFile(
        startup_id=s.id,
        uploaded_by_id=u.id,
        folder="Legal",
        filename="nda.pdf",
        content_type="application/pdf",
        size_bytes=1234,
        storage_key=f"documents/{s.id}/abc.pdf",
        url="file:///var/storage/documents/abc.pdf",
    )
    db.add(f)
    db.flush()
    db.refresh(f)
    assert f.filename == "nda.pdf" and f.size_bytes == 1234
    assert f.folder == "Legal"
