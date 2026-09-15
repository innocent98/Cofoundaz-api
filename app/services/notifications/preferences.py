import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.notification_preference import NotificationPreference
from app.services.notifications.categories import CATEGORIES, CATEGORY_DEFAULTS


def _row(db: Session, user_id: uuid.UUID, startup_id: uuid.UUID) -> NotificationPreference | None:
    return (
        db.query(NotificationPreference).filter_by(user_id=user_id, startup_id=startup_id).first()
    )


def effective_preferences(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID
) -> dict[str, Any]:
    """Stored prefs merged over defaults; every category key present."""
    row = _row(db, user_id, startup_id)
    stored = row.categories if row is not None else {}
    return {
        "master_email": row.master_email if row is not None else True,
        "categories": {c: bool(stored.get(c, CATEGORY_DEFAULTS[c])) for c in CATEGORIES},
    }


def set_preferences(
    db: Session,
    *,
    user_id: uuid.UUID,
    startup_id: uuid.UUID,
    master_email: bool | None,
    categories: dict[str, bool] | None,
) -> dict[str, Any]:
    """Upsert; master_email and/or a subset of category keys. Returns effective prefs."""
    row = _row(db, user_id, startup_id)
    if row is None:
        row = NotificationPreference(
            user_id=user_id, startup_id=startup_id, master_email=True, categories={}
        )
        db.add(row)
    if master_email is not None:
        row.master_email = master_email
    if categories:
        merged = dict(row.categories)
        merged.update({k: bool(v) for k, v in categories.items() if k in CATEGORIES})
        row.categories = merged
    db.flush()
    return effective_preferences(db, user_id=user_id, startup_id=startup_id)


def email_enabled(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, category: str | None
) -> bool:
    """Whether this recipient wants email for this category (enqueue-time gate)."""
    if category is None:
        return False
    eff = effective_preferences(db, user_id=user_id, startup_id=startup_id)
    return bool(eff["master_email"] and eff["categories"].get(category, False))
