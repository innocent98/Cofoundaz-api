import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import FileHasSignatureRequests, FileNotClean, NotFound
from app.db.models.document import DocumentFile, SignatureRequest
from app.db.models.enums import ScanStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.storage import get_storage
from app.services.documents.scanner import scan_file
from app.services.documents.sniff import ALLOWED_TYPES, MARKDOWN

# content-type -> file extension. Also the upload allowlist; `sniff.ALLOWED_TYPES` must list
# exactly the same types (asserted below).
EXT_BY_CONTENT_TYPE: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/plain": ".txt",
    "text/csv": ".csv",
    MARKDOWN: ".md",
}
assert set(EXT_BY_CONTENT_TYPE) == ALLOWED_TYPES  # nosec B101 - import-time invariant


def upload_file(
    db: Session,
    startup: Startup,
    *,
    uploaded_by_id: uuid.UUID,
    filename: str,
    content_type: str,
    size_bytes: int,
    content: bytes,
    folder: str | None,
) -> DocumentFile:
    ext = EXT_BY_CONTENT_TYPE.get(content_type, "")
    storage_key = f"documents/{startup.id}/{uuid.uuid4().hex}{ext}"
    url = get_storage().save(storage_key, content, content_type)
    row = DocumentFile(
        startup_id=startup.id,
        uploaded_by_id=uploaded_by_id,
        folder=folder,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        url=url,
        scan_status=ScanStatus.pending,
    )
    db.add(row)
    db.flush()
    scan_file(db, row, content)
    event_bus.publish(
        db,
        "document.file.uploaded",
        {"startup_id": str(startup.id), "file_id": str(row.id), "content_type": content_type},
    )
    return row


def list_files(db: Session, startup: Startup, *, folder: str | None) -> list[DocumentFile]:
    q = db.query(DocumentFile).filter_by(startup_id=startup.id)
    if folder is not None:
        q = q.filter_by(folder=folder)
    return q.order_by(DocumentFile.created_at.desc()).all()


def get_file(db: Session, membership: Membership, file_id: Any) -> DocumentFile:
    row = db.query(DocumentFile).filter_by(id=file_id, startup_id=membership.startup_id).first()
    if row is None:
        raise NotFound()
    return row


def assert_clean(file: DocumentFile) -> None:
    """409 unless the file has passed the scan. Gate for sending a file to signers (and for
    sharing, when files become shareable)."""
    if file.scan_status != ScanStatus.clean:
        raise FileNotClean()


def delete_file(db: Session, file: DocumentFile) -> None:
    """Refuse (409) while any signature request references the file: the request, and above
    all a completed one, is the signed record and must outlive the upload."""
    if db.query(SignatureRequest.id).filter_by(file_id=file.id).first() is not None:
        raise FileHasSignatureRequests()
    get_storage().delete(file.storage_key)
    db.delete(file)
    db.flush()
    event_bus.publish(
        db,
        "document.file.deleted",
        {"startup_id": str(file.startup_id), "file_id": str(file.id)},
    )


def serialize_file(f: DocumentFile) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "filename": f.filename,
        "content_type": f.content_type,
        "size_bytes": f.size_bytes,
        "folder": f.folder,
        "url": f.url,
        "scan_status": f.scan_status.value,
        "uploaded_at": f.created_at.isoformat(),
    }
