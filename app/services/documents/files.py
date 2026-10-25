import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import FileHasSignatureRequests, FileNotClean, NotFound
from app.core.logger import log
from app.db.models.document import DocumentFile, SignatureRequest
from app.db.models.enums import ScanStatus
from app.db.models.membership import Membership
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.platform.signed_urls import sign_key
from app.platform.storage import LocalStorage, Storage, get_storage
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


FILE_LINK_TTL_SECONDS = 300
LOCAL_DOWNLOAD_PATH = "/api/v1/documents/files/download"


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
    # Private from the first byte: no stable public URL ever exists, so the role gate, the scan
    # gate and request cancellation actually control access to the content.
    get_storage().save_private(storage_key, content, content_type)
    row = DocumentFile(
        startup_id=startup.id,
        uploaded_by_id=uploaded_by_id,
        folder=folder,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        url=None,
        is_private=True,
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


def get_file(
    db: Session, membership: Membership, file_id: Any, *, lock: bool = False
) -> DocumentFile:
    """The workspace's file or 404. `lock=True` takes `SELECT ... FOR UPDATE` on the row so a
    concurrent delete and signature-request create serialise instead of racing."""
    q = db.query(DocumentFile).filter_by(id=file_id, startup_id=membership.startup_id)
    row = (q.with_for_update() if lock else q).first()
    if row is None:
        raise NotFound()
    return row


def assert_clean(file: DocumentFile) -> None:
    """409 unless the file has passed the scan. Gate for sending a file to signers, for issuing
    any download link, and for sharing when files become shareable."""
    if file.scan_status != ScanStatus.clean:
        raise FileNotClean()


def delete_file(db: Session, file: DocumentFile) -> tuple[str, bool]:
    """Remove the row and return `(storage_key, is_private)` for `purge_object`.

    The caller must hold the row lock (`get_file(..., lock=True)`) and call `purge_object` only
    AFTER committing: deleting the stored object first would orphan or destroy data if the commit
    then fails. Refuses (409) while any signature request references the file: the request, and
    above all a completed one, is the signed record and must outlive the upload.
    """
    if db.query(SignatureRequest.id).filter_by(file_id=file.id).first() is not None:
        raise FileHasSignatureRequests()
    target = (file.storage_key, file.is_private)
    db.delete(file)
    db.flush()
    event_bus.publish(
        db,
        "document.file.deleted",
        {"startup_id": str(file.startup_id), "file_id": str(file.id)},
    )
    return target


def purge_object(target: tuple[str, bool]) -> None:
    """Delete the stored object after the row's deletion has committed. A failure leaves an
    orphaned object (logged), never a row pointing at nothing."""
    key, private = target
    storage = get_storage()
    try:
        (storage.delete_private if private else storage.delete)(key)
    except Exception:  # noqa: BLE001 - the row is already gone; an orphan is the lesser harm
        log.exception("document object {} was not removed after its row was deleted", key)


def ensure_private(file: DocumentFile) -> Storage:
    """Move a legacy public file behind authentication (idempotent) and return the storage."""
    storage = get_storage()
    if not file.is_private:
        storage.make_private(file.storage_key)
        file.is_private = True
        file.url = None
    return storage


def file_link(file: DocumentFile, *, api_public_url: str) -> dict[str, Any]:
    """A short-lived signed download link for `file` (caller has already authorised the reader).

    Refuses (409) a file that is not `clean`. A legacy public file is moved behind authentication
    first; if that fails the link is refused rather than handing out the public URL.
    """
    assert_clean(file)
    storage = ensure_private(file)
    url = storage.private_url(file.storage_key, FILE_LINK_TTL_SECONDS)
    if url is None:
        token, expires = sign_key(
            file.storage_key, startup_id=str(file.startup_id), ttl_seconds=FILE_LINK_TTL_SECONDS
        )
        url = f"{api_public_url.rstrip('/')}{LOCAL_DOWNLOAD_PATH}?token={token}"
    else:
        expires = int(dt.datetime.now(dt.UTC).timestamp()) + FILE_LINK_TTL_SECONDS
    return {
        "url": url,
        "expires_at": dt.datetime.fromtimestamp(expires, dt.UTC).isoformat(),
        "content_type": file.content_type,
        "filename": file.filename,
    }


def local_path(file: DocumentFile) -> Any:
    """The on-disk path of a locally stored file, or None (missing, or a remote backend)."""
    storage = get_storage()
    return storage.open_private(file.storage_key) if isinstance(storage, LocalStorage) else None


def file_by_key(db: Session, *, startup_id: str, key: str) -> DocumentFile | None:
    try:
        sid = uuid.UUID(startup_id)
    except ValueError:
        return None
    return db.query(DocumentFile).filter_by(startup_id=sid, storage_key=key).first()


def serialize_file(f: DocumentFile) -> dict[str, Any]:
    """The file summary. There is deliberately no `url`: content is read through
    `GET /documents/files/{id}/download` (a short-lived signed link) only."""
    return {
        "id": str(f.id),
        "filename": f.filename,
        "content_type": f.content_type,
        "size_bytes": f.size_bytes,
        "folder": f.folder,
        "scan_status": f.scan_status.value,
        "uploaded_at": f.created_at.isoformat(),
    }
