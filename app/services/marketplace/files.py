"""Marketplace uploads: allowlist by purpose, magic-byte sniff, size cap, private storage.

Reuses the Documents sniffing (`documents/sniff.py`) and scanner hook rather than a second copy of
either. The client's `Content-Type` is a claim; the bytes decide. Nothing is ever public: owners
read their own files and staff read credentials through audited routes."""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import MarketplaceFilePurpose, ScanStatus
from app.db.models.marketplace import MarketplaceFile, MarketplaceProviderPortfolio
from app.platform.storage import get_storage
from app.services.documents.files import EXT_BY_CONTENT_TYPE
from app.services.documents.scanner import get_scanner
from app.services.documents.sniff import JPEG, PDF, PNG, PPTX, declared_type, sniff_content_type
from app.services.marketplace.constants import (
    MAX_AVATAR_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES_PER_PURPOSE,
)

ALLOWED_BY_PURPOSE: dict[MarketplaceFilePurpose, frozenset[str]] = {
    MarketplaceFilePurpose.credential: frozenset({PDF, PNG, JPEG}),
    MarketplaceFilePurpose.portfolio: frozenset({PDF, PNG, JPEG, PPTX}),
    MarketplaceFilePurpose.avatar: frozenset({PNG, JPEG}),
}
_UNSAFE_NAME = re.compile(r"[^\w .()\-]+")


def _err(code: str, message: str, status: int = 422) -> AppError:
    return AppError(code=code, message=message, http_status=status)


def safe_filename(raw: str | None) -> str:
    base = re.split(r"[\\/]", raw or "")[-1]
    cleaned = _UNSAFE_NAME.sub("_", base).strip(" .")[:200]
    return cleaned or "file"


def _live_count(db: Session, user_id: uuid.UUID, purpose: MarketplaceFilePurpose) -> int:
    return (
        db.query(func.count(MarketplaceFile.id))
        .filter(
            MarketplaceFile.owner_user_id == user_id,
            MarketplaceFile.purpose == purpose,
            MarketplaceFile.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )


def store_file(
    db: Session,
    *,
    owner_user_id: uuid.UUID,
    purpose: MarketplaceFilePurpose,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> MarketplaceFile:
    allowed = ALLOWED_BY_PURPOSE.get(purpose)
    if allowed is None:
        raise _err("VALIDATION_ERROR", "That kind of upload isn't available here.")
    name = safe_filename(filename)
    declared = declared_type(content_type, name)
    if declared is None or declared not in allowed:
        hint = (
            "Upload a PNG or JPEG image."
            if purpose == MarketplaceFilePurpose.avatar
            else "Upload a PDF, PNG or JPEG."
        )
        raise _err("UNSUPPORTED_FILE_TYPE", f"That file type isn't allowed here. {hint}")
    limit = MAX_AVATAR_BYTES if purpose == MarketplaceFilePurpose.avatar else MAX_FILE_BYTES
    if len(content) > limit:
        raise _err("FILE_TOO_LARGE", f"File must be {limit // (1024 * 1024)} MB or smaller.")
    sniffed = sniff_content_type(content, declared)
    if sniffed is None:
        raise _err(
            "FILE_CONTENT_MISMATCH",
            "That file's contents don't match its type, or the file is empty.",
        )
    cap = MAX_FILES_PER_PURPOSE.get(purpose.value)
    if (
        purpose != MarketplaceFilePurpose.avatar
        and cap
        and (_live_count(db, owner_user_id, purpose) >= cap)
    ):
        raise _err("FILE_LIMIT_REACHED", f"You can keep up to {cap} {purpose.value} files.", 409)
    verdict = get_scanner().scan(content, name)
    if verdict == ScanStatus.blocked:
        raise _err("FILE_BLOCKED", "That file didn't pass our safety scan.")
    key = f"marketplace/{owner_user_id}/{uuid.uuid4().hex}{EXT_BY_CONTENT_TYPE.get(sniffed, '')}"
    get_storage().save_private(key, content, sniffed)
    if purpose == MarketplaceFilePurpose.avatar:
        retire_files(db, owner_user_id, MarketplaceFilePurpose.avatar)
    row = MarketplaceFile(
        owner_user_id=owner_user_id,
        purpose=purpose,
        filename=name,
        mime=sniffed,
        size_bytes=len(content),
        storage_key=key,
        scan_status=verdict,
        scanned_at=None if verdict == ScanStatus.pending else datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def erase(db: Session, row: MarketplaceFile) -> None:
    """Remove the bytes and mark the row deleted. Anything that pointed at the file is cleared."""
    if row.deleted_at is not None:
        return
    get_storage().delete_private(row.storage_key)
    row.deleted_at = datetime.now(UTC)
    db.query(MarketplaceProviderPortfolio).filter(
        MarketplaceProviderPortfolio.file_id == row.id
    ).update({"file_id": None}, synchronize_session=False)
    db.flush()


def retire_files(
    db: Session, owner_user_id: uuid.UUID, purpose: MarketplaceFilePurpose | None = None
) -> int:
    q = db.query(MarketplaceFile).filter(
        MarketplaceFile.owner_user_id == owner_user_id, MarketplaceFile.deleted_at.is_(None)
    )
    if purpose is not None:
        q = q.filter(MarketplaceFile.purpose == purpose)
    rows = q.all()
    for r in rows:
        erase(db, r)
    return len(rows)


def get_owned(db: Session, owner_user_id: uuid.UUID, file_id: uuid.UUID) -> MarketplaceFile:
    row = (
        db.query(MarketplaceFile)
        .filter(
            MarketplaceFile.id == file_id,
            MarketplaceFile.owner_user_id == owner_user_id,
            MarketplaceFile.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise NotFound()
    return row


def read_bytes(row: MarketplaceFile) -> bytes:
    data = get_storage().read_private(row.storage_key)
    if data is None:
        raise NotFound()
    return data


def serialize_file(row: MarketplaceFile) -> dict[str, object]:
    return {
        "id": str(row.id),
        "purpose": row.purpose.value,
        "filename": row.filename,
        "mime": row.mime,
        "size_bytes": row.size_bytes,
        "scan_status": row.scan_status.value,
        "created_at": row.created_at.isoformat(),
    }
