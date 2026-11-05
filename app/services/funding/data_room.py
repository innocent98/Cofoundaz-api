"""The data room's structure: folders and items that REFERENCE Documents (D-FUND-L4).

Nothing is copied. Whether an item is still viewable (file not trashed, scan clean, document not
trashed) is decided on every read and every serve, never at add time (`live_target`).
"""

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.data_room import DataRoomFolder, DataRoomItem
from app.db.models.document import Document, DocumentFile
from app.db.models.enums import ScanStatus
from app.schemas.data_room import FolderCreate, FolderUpdate, ItemCreate, ItemUpdate
from app.services.documents.files import VIEWER_BY_CONTENT_TYPE
from app.services.funding.errors import _validation

STANDARD_FOLDERS = ("Corporate", "Financials", "Product", "Market", "Team", "Legal")
MAX_FOLDERS = 50
MAX_ITEMS = 500
PDF = "application/pdf"
IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})
# Types that can be shown to a viewer with a watermark when downloads are off (D-FUND-8): PDFs
# and rich Documents (rendered to PDF) and images (converted to PDF). Everything else would go
# out as raw bytes, so it needs "allow downloads".
WATERMARKABLE_TYPES = frozenset({PDF}) | IMAGE_TYPES


def viewable(content_type: str | None, *, allow_download: bool) -> bool:
    return allow_download or content_type in WATERMARKABLE_TYPES


def _name_taken(name: str) -> AppError:
    return AppError("FOLDER_EXISTS", "A folder with that name already exists.", 409)


def list_folders(db: Session, startup_id: uuid.UUID) -> list[DataRoomFolder]:
    return (
        db.query(DataRoomFolder)
        .filter_by(startup_id=startup_id)
        .order_by(DataRoomFolder.position, DataRoomFolder.created_at, DataRoomFolder.id)
        .all()
    )


def get_folder(db: Session, startup_id: uuid.UUID, folder_id: uuid.UUID) -> DataRoomFolder:
    row = db.query(DataRoomFolder).filter_by(id=folder_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def get_item(db: Session, startup_id: uuid.UUID, item_id: uuid.UUID) -> DataRoomItem:
    row = db.query(DataRoomItem).filter_by(id=item_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def _lock_workspace(db: Session, startup_id: uuid.UUID) -> None:
    """Serialise structure writes per workspace so the name and count rules hold under races."""
    from app.db.models.startup import Startup

    db.query(Startup.id).filter(Startup.id == startup_id).with_for_update().one()


def setup_standard(db: Session, startup_id: uuid.UUID) -> list[DataRoomFolder]:
    """The six standard folders. Idempotent: creates only the ones that are missing."""
    _lock_workspace(db, startup_id)
    existing = {f.name.lower() for f in list_folders(db, startup_id)}
    position = (
        db.query(func.coalesce(func.max(DataRoomFolder.position), -1))
        .filter_by(startup_id=startup_id)
        .scalar()
    )
    for name in STANDARD_FOLDERS:
        if name.lower() in existing:
            continue
        position += 1
        db.add(
            DataRoomFolder(startup_id=startup_id, name=name, position=position, is_standard=True)
        )
    db.flush()
    return list_folders(db, startup_id)


def create_folder(db: Session, startup_id: uuid.UUID, data: FolderCreate) -> DataRoomFolder:
    _lock_workspace(db, startup_id)
    folders = list_folders(db, startup_id)
    if len(folders) >= MAX_FOLDERS:
        raise _validation("name", f"A data room can have at most {MAX_FOLDERS} folders.")
    if any(f.name.lower() == data.name.lower() for f in folders):
        raise _name_taken(data.name)
    row = DataRoomFolder(
        startup_id=startup_id,
        name=data.name,
        position=(max((f.position for f in folders), default=-1) + 1),
        is_standard=False,
    )
    db.add(row)
    db.flush()
    return row


def update_folder(
    db: Session, startup_id: uuid.UUID, folder_id: uuid.UUID, data: FolderUpdate
) -> DataRoomFolder:
    _lock_workspace(db, startup_id)
    row = get_folder(db, startup_id, folder_id)
    if data.name is not None and data.name.lower() != row.name.lower():
        if any(f.name.lower() == data.name.lower() for f in list_folders(db, startup_id)):
            raise _name_taken(data.name)
        row.name = data.name
    if data.position is not None:
        row.position = data.position
    db.flush()
    return row


def delete_folder(db: Session, startup_id: uuid.UUID, folder_id: uuid.UUID) -> None:
    """Deleting a folder removes its item references (never the Documents files). Shares scoped to
    it simply stop matching anything: serve-time checks read the live folder."""
    row = get_folder(db, startup_id, folder_id)
    db.delete(row)
    db.flush()


def _target_of(
    db: Session, startup_id: uuid.UUID, data: ItemCreate
) -> tuple[Document | None, DocumentFile | None]:
    if data.document_id is not None:
        doc = (
            db.query(Document)
            .filter(
                Document.id == data.document_id,
                Document.startup_id == startup_id,
                Document.trashed_at.is_(None),
            )
            .first()
        )
        if doc is None:
            raise NotFound()
        return doc, None
    file = (
        db.query(DocumentFile)
        .filter(
            DocumentFile.id == data.file_id,
            DocumentFile.startup_id == startup_id,
            DocumentFile.trashed_at.is_(None),
        )
        .first()
    )
    if file is None:
        raise NotFound()
    if file.scan_status != ScanStatus.clean:
        raise AppError(
            "FILE_NOT_CLEAN", "That file hasn't passed the virus scan, so it can't be added.", 409
        )
    return None, file


def add_item(
    db: Session,
    startup_id: uuid.UUID,
    folder_id: uuid.UUID,
    data: ItemCreate,
    *,
    actor_id: uuid.UUID,
) -> DataRoomItem:
    _lock_workspace(db, startup_id)
    folder = get_folder(db, startup_id, folder_id)
    doc, file = _target_of(db, startup_id, data)
    if db.query(DataRoomItem).filter_by(startup_id=startup_id).count() >= MAX_ITEMS:
        raise _validation("folder_id", f"A data room can hold at most {MAX_ITEMS} items.")
    clash = db.query(DataRoomItem).filter_by(
        folder_id=folder.id,
        document_id=doc.id if doc else None,
        file_id=file.id if file else None,
    )
    if clash.first() is not None:
        raise AppError("ITEM_EXISTS", "That item is already in this folder.", 409)
    position = (
        db.query(func.coalesce(func.max(DataRoomItem.position), -1))
        .filter_by(folder_id=folder.id)
        .scalar()
        + 1
    )
    row = DataRoomItem(
        startup_id=startup_id,
        folder_id=folder.id,
        document_id=doc.id if doc else None,
        file_id=file.id if file else None,
        position=position,
        added_by_id=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def update_item(
    db: Session, startup_id: uuid.UUID, item_id: uuid.UUID, data: ItemUpdate
) -> DataRoomItem:
    _lock_workspace(db, startup_id)
    row = get_item(db, startup_id, item_id)
    if data.folder_id is not None and data.folder_id != row.folder_id:
        dest = get_folder(db, startup_id, data.folder_id)
        clash = db.query(DataRoomItem).filter_by(
            folder_id=dest.id, document_id=row.document_id, file_id=row.file_id
        )
        if clash.first() is not None:
            raise AppError("ITEM_EXISTS", "That item is already in this folder.", 409)
        row.folder_id = dest.id
    if data.position is not None:
        row.position = data.position
    db.flush()
    return row


def remove_item(db: Session, startup_id: uuid.UUID, item_id: uuid.UUID) -> None:
    db.delete(get_item(db, startup_id, item_id))
    db.flush()


# ---- liveness: the single place that decides whether an item may be shown or served ----


def live_target(db: Session, item: DataRoomItem) -> tuple[Document | None, DocumentFile | None]:
    """The item's Documents row, only while it is viewable: a file must be in the same workspace,
    not trashed and `clean`; a document must be in the same workspace and not trashed. (None, None)
    means the item has vanished from the room."""
    if item.file_id is not None:
        file = (
            db.query(DocumentFile)
            .filter(
                DocumentFile.id == item.file_id,
                DocumentFile.startup_id == item.startup_id,
                DocumentFile.trashed_at.is_(None),
                DocumentFile.scan_status == ScanStatus.clean,
            )
            .first()
        )
        return None, file
    doc = (
        db.query(Document)
        .filter(
            Document.id == item.document_id,
            Document.startup_id == item.startup_id,
            Document.trashed_at.is_(None),
        )
        .first()
    )
    return doc, None


def facts(doc: Document | None, file: DocumentFile | None) -> dict[str, Any]:
    if file is not None:
        return {
            "kind": "file",
            "name": file.filename,
            "content_type": file.content_type,
            "size_bytes": file.size_bytes,
            "viewer": VIEWER_BY_CONTENT_TYPE.get(file.content_type, "download"),
            "viewable": file.content_type in WATERMARKABLE_TYPES,
        }
    if doc is not None:
        # A rich document is rendered to PDF when served.
        return {
            "kind": "document",
            "name": doc.title or "Untitled",
            "content_type": PDF,
            "size_bytes": None,
            "viewer": "pdf",
            "viewable": True,
        }
    return {}


def serialize_item(db: Session, item: DataRoomItem) -> dict[str, Any]:
    doc, file = live_target(db, item)
    out: dict[str, Any] = {
        "id": str(item.id),
        "folder_id": str(item.folder_id),
        "position": item.position,
        "document_id": str(item.document_id) if item.document_id else None,
        "file_id": str(item.file_id) if item.file_id else None,
        "available": doc is not None or file is not None,
        **(facts(doc, file) or _gone(db, item)),
    }
    return out


def _gone(db: Session, item: DataRoomItem) -> dict[str, Any]:
    """Why an item is not viewable, for the founder's chips (never shown to investors)."""
    if item.file_id is not None:
        file = db.query(DocumentFile).filter_by(id=item.file_id).first()
        if file is None:
            return {"kind": "file", "name": "Removed file", "reason": "removed"}
        reason = "trashed" if file.trashed_at is not None else f"scan_{file.scan_status.value}"
        return {
            "kind": "file",
            "name": file.filename,
            "content_type": file.content_type,
            "size_bytes": file.size_bytes,
            "viewer": None,
            "viewable": False,
            "reason": reason,
        }
    doc = db.query(Document).filter_by(id=item.document_id).first()
    return {
        "kind": "document",
        "name": doc.title if doc else "Removed document",
        "reason": "trashed" if doc is not None else "removed",
    }


def serialize_folder(folder: DataRoomFolder) -> dict[str, Any]:
    return {
        "id": str(folder.id),
        "name": folder.name,
        "position": folder.position,
        "is_standard": folder.is_standard,
    }


def tree(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    folders = list_folders(db, startup_id)
    items = (
        db.query(DataRoomItem)
        .filter_by(startup_id=startup_id)
        .order_by(DataRoomItem.position, DataRoomItem.created_at, DataRoomItem.id)
        .all()
    )
    by_folder: dict[uuid.UUID, list[dict[str, Any]]] = {f.id: [] for f in folders}
    for it in items:
        by_folder[it.folder_id].append(serialize_item(db, it))
    return {
        "set_up": bool(folders),
        "standard_folders": list(STANDARD_FOLDERS),
        "folders": [{**serialize_folder(f), "items": by_folder[f.id]} for f in folders],
        "item_count": len(items),
    }


def unviewable_items(
    db: Session, startup_id: uuid.UUID, folder_ids: list[str]
) -> list[dict[str, Any]]:
    """Items in these folders that cannot be shown view-only (they would need downloads on), so
    the founder is told when creating a share, not when the investor hits a refusal."""
    if not folder_ids:
        return []
    rows = (
        db.query(DataRoomItem)
        .filter(
            DataRoomItem.startup_id == startup_id,
            DataRoomItem.folder_id.in_([uuid.UUID(f) for f in folder_ids]),
        )
        .all()
    )
    out = []
    for it in rows:
        doc, file = live_target(db, it)
        if file is not None and file.content_type not in WATERMARKABLE_TYPES:
            out.append(
                {"item_id": str(it.id), "name": file.filename, "code": "VIEW_ONLY_UNSUPPORTED"}
            )
    return out
