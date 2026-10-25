"""Leaf helpers shared by the Documents services (no imports from the other service modules, so
service/versions/folders/grants can all use them without an import cycle)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models.user import User, UserProfile

DOCUMENT_MIME = "application/vnd.cofoundaz.document+json"
MAX_TAGS = 10
MAX_TAG_LENGTH = 30
MAX_FOLDER_DEPTH = 4
MAX_FOLDER_SEGMENT = 60
MAX_FOLDER_PATH = 120
MAX_SEARCH_TERMS = 8


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        message,
        422,
        field_errors=[{"field": field, "message": message}],
    )


def search_text_of(sections: list[dict[str, Any]]) -> str:
    """The plain heading + body text library search reads (no JSON syntax, no section ids)."""
    return " ".join(f"{s.get('heading', '')} {s.get('body', '')}".strip() for s in sections)


def normalize_tags(tags: list[str]) -> list[str]:
    """Trimmed, de-duplicated (case-insensitively, first spelling wins), at most `MAX_TAGS`."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = " ".join(raw.split())
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise _invalid("tags", f"A tag can be at most {MAX_TAG_LENGTH} characters.")
        if tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise _invalid("tags", f"A document can have at most {MAX_TAGS} tags.")
    return out


def normalize_folder(folder: str | None) -> str | None:
    """A folder path like "Legal/NDAs": segments trimmed, 1-60 chars each, at most 4 deep, 120
    chars overall. Blank means no folder."""
    if folder is None or not folder.strip():
        return None
    segments = [" ".join(seg.split()) for seg in folder.split("/")]
    if any(not seg for seg in segments):
        raise _invalid("folder", "A folder name can't be empty.")
    if len(segments) > MAX_FOLDER_DEPTH:
        raise _invalid("folder", f"Folders can be at most {MAX_FOLDER_DEPTH} levels deep.")
    if any(len(seg) > MAX_FOLDER_SEGMENT for seg in segments):
        raise _invalid("folder", f"A folder name can be at most {MAX_FOLDER_SEGMENT} characters.")
    path = "/".join(segments)
    if len(path) > MAX_FOLDER_PATH:
        raise _invalid("folder", f"A folder path can be at most {MAX_FOLDER_PATH} characters.")
    return path


def display_name(db: Session, user_id: uuid.UUID | None) -> str | None:
    """The user's profile name, else the local part of their email, else None."""
    if user_id is None:
        return None
    owner = owner_map(db, {user_id}).get(user_id)
    return owner["name"] if owner else None


def owner_map(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    """`{id, name}` for each user id, falling back to the email's local part when the user has no
    profile name."""
    if not ids:
        return {}
    names = dict(
        db.query(UserProfile.user_id, UserProfile.full_name)
        .filter(UserProfile.user_id.in_(ids))
        .tuples()
        .all()
    )
    emails = dict(db.query(User.id, User.email).filter(User.id.in_(ids)).tuples().all())
    return {
        i: {"id": str(i), "name": names.get(i) or (emails.get(i) or "").split("@")[0] or None}
        for i in ids
    }
