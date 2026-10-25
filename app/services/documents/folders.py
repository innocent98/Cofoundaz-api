import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import FolderExists, FolderNotEmpty, NotFound
from app.db.models.document import Document, DocumentFile, DocumentFolder
from app.services.documents.common import (
    MAX_FOLDER_PATH,
    MAX_FOLDER_SEGMENT,
    _invalid,
    normalize_folder,
)

_ITEM_MODELS: tuple[Any, Any] = (Document, DocumentFile)
STANDARD_FOLDERS = ("Corporate", "Financials", "Legal", "Fundraising", "Marketing", "Archive")


def ensure_path(db: Session, startup_id: uuid.UUID, path: str | None) -> DocumentFolder | None:
    """Make sure a folder row exists for `path` and every ancestor (idempotent)."""
    if not path:
        return None
    parent: DocumentFolder | None = None
    built: list[str] = []
    for name in path.split("/"):
        built.append(name)
        current = "/".join(built)
        row = db.query(DocumentFolder).filter_by(startup_id=startup_id, path=current).first()
        if row is None:
            row = DocumentFolder(
                startup_id=startup_id,
                parent_id=parent.id if parent else None,
                name=name,
                path=current,
            )
            db.add(row)
            db.flush()
        parent = row
    return parent


def get_folder(db: Session, startup_id: uuid.UUID, folder_id: Any) -> DocumentFolder:
    row = db.query(DocumentFolder).filter_by(id=folder_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or "/" in cleaned:
        raise _invalid("name", "A folder name can't be empty or contain a slash.")
    if len(cleaned) > MAX_FOLDER_SEGMENT:
        raise _invalid("name", f"A folder name can be at most {MAX_FOLDER_SEGMENT} characters.")
    return cleaned


def create_folder(
    db: Session, startup_id: uuid.UUID, *, name: str, parent_id: uuid.UUID | None
) -> DocumentFolder:
    name = _clean_name(name)
    parent = get_folder(db, startup_id, parent_id) if parent_id else None
    path = normalize_folder(f"{parent.path}/{name}" if parent else name)
    if db.query(DocumentFolder.id).filter_by(startup_id=startup_id, path=path).first():
        raise FolderExists()
    row = DocumentFolder(startup_id=startup_id, parent_id=parent_id, name=name, path=path)
    db.add(row)
    db.flush()
    return row


def standard_structure(db: Session, startup_id: uuid.UUID) -> list[DocumentFolder]:
    return [ensure_path(db, startup_id, name) for name in STANDARD_FOLDERS]  # type: ignore[misc]


def _under(path: str) -> str:
    """A LIKE pattern for everything below `path` (escape character `\\`)."""
    return path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def _subtree(db: Session, startup_id: uuid.UUID, path: str) -> list[DocumentFolder]:
    like = _under(path)
    return (
        db.query(DocumentFolder)
        .filter(
            DocumentFolder.startup_id == startup_id,
            (DocumentFolder.path == path) | DocumentFolder.path.like(like, escape="\\"),
        )
        .all()
    )


def rename_folder(db: Session, folder: DocumentFolder, *, name: str) -> DocumentFolder:
    """Rename and re-point the whole subtree plus every document and file inside it."""
    name = _clean_name(name)
    old = folder.path
    parent_path = old.rsplit("/", 1)[0] if "/" in old else None
    new = normalize_folder(f"{parent_path}/{name}" if parent_path else name)
    assert new is not None  # nosec B101 - name is non-empty
    if new == old:
        return folder
    if db.query(DocumentFolder.id).filter_by(startup_id=folder.startup_id, path=new).first():
        raise FolderExists()
    members = _subtree(db, folder.startup_id, old)
    longest = max(len(m.path) - len(old) for m in members) + len(new)
    if longest > MAX_FOLDER_PATH:
        raise _invalid("name", f"A folder path can be at most {MAX_FOLDER_PATH} characters.")
    for m in members:
        m.path = new + m.path[len(old) :]
    folder.name = name
    for model in _ITEM_MODELS:
        for item in db.query(model).filter(
            model.startup_id == folder.startup_id,
            (model.folder == old) | model.folder.like(_under(old), escape="\\"),
        ):
            item.folder = new + item.folder[len(old) :]
    db.flush()
    return folder


def delete_folder(db: Session, folder: DocumentFolder) -> None:
    """Only an empty one (no live document or file anywhere in its subtree). Trashed items keep
    their folder path and get the folder back if they are restored."""
    for member in _subtree(db, folder.startup_id, folder.path):
        for model in _ITEM_MODELS:
            if (
                db.query(model.id)
                .filter(
                    model.startup_id == folder.startup_id,
                    model.folder == member.path,
                    model.trashed_at.is_(None),
                )
                .first()
            ):
                raise FolderNotEmpty()
    db.delete(folder)
    db.flush()


def _counts(db: Session, startup_id: uuid.UUID, model: Any) -> dict[str | None, int]:
    rows = (
        db.query(model.folder, func.count())
        .filter(model.startup_id == startup_id, model.trashed_at.is_(None))
        .group_by(model.folder)
        .all()
    )
    return {k: int(n) for k, n in rows}


def folder_tree(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    """Every folder with its own counts and its subtree total, plus the uncategorised and trash
    buckets. Trashed items are not counted in folders."""
    docs = _counts(db, startup_id, Document)
    files = _counts(db, startup_id, DocumentFile)
    folders = (
        db.query(DocumentFolder)
        .filter_by(startup_id=startup_id)
        .order_by(DocumentFolder.path)
        .all()
    )
    out = []
    for f in folders:
        own_docs, own_files = docs.get(f.path, 0), files.get(f.path, 0)
        total = sum(
            docs.get(g.path, 0) + files.get(g.path, 0)
            for g in folders
            if g.path == f.path or g.path.startswith(f.path + "/")
        )
        out.append(
            {
                "id": str(f.id),
                "name": f.name,
                "path": f.path,
                "parent_id": str(f.parent_id) if f.parent_id else None,
                "document_count": own_docs,
                "file_count": own_files,
                "total_count": total,
            }
        )
    trash = sum(
        db.query(model.id)
        .filter(model.startup_id == startup_id, model.trashed_at.isnot(None))
        .count()
        for model in (Document, DocumentFile)
    )
    return {
        "folders": out,
        "uncategorized": docs.get(None, 0) + files.get(None, 0),
        "trash": trash,
        "total": sum(docs.values()) + sum(files.values()),
    }
