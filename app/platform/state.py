"""Platform-wide switches kept in `platform_settings`: maintenance mode and the sign-out cutoff.

Reads never lock and never create a row. Writes lock the row (creating it on first use) so two
Super Admins can't interleave.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.platform_setting import PlatformSetting

MAINTENANCE = "maintenance"
SESSION_CUTOFF = "session_cutoff"

DEFAULT_MAINTENANCE_MESSAGE = (
    "Cofoundaz is down for maintenance. Your work is safe. Please try again shortly."
)

# Holds back sign-ins while a global sign-out runs. See `lock_session_issue`.
_SESSION_ISSUE_LOCK = 7_265_021_001


def _read(db: Session, key: str) -> dict[str, Any] | None:
    # A column query, not `db.get`: the identity map must never serve a stale copy of a switch.
    value = db.query(PlatformSetting.value).filter(PlatformSetting.key == key).scalar()
    return dict(value) if value else None


def maintenance_state(db: Session) -> dict[str, Any]:
    value = _read(db, MAINTENANCE) or {}
    enabled = bool(value.get("enabled"))
    return {
        "enabled": enabled,
        "message": (value.get("message") or DEFAULT_MAINTENANCE_MESSAGE) if enabled else None,
        "since": value.get("since") if enabled else None,
    }


def session_cutoff_ts(db: Session) -> float | None:
    """Epoch seconds before which no access token is honoured; None when never signed out."""
    value = _read(db, SESSION_CUTOFF)
    raw = value.get("valid_after") if value else None
    return datetime.fromisoformat(raw).timestamp() if raw else None


def last_global_signout(db: Session) -> str | None:
    value = _read(db, SESSION_CUTOFF)
    return value.get("valid_after") if value else None


def _locked(db: Session, key: str) -> PlatformSetting:
    db.execute(pg_insert(PlatformSetting).values(key=key, value={}).on_conflict_do_nothing())
    row = (
        db.query(PlatformSetting)
        .filter(PlatformSetting.key == key)
        .with_for_update()
        .populate_existing()
        .one()
    )
    return row


def set_maintenance(
    db: Session, *, enabled: bool, message: str | None, actor_id: uuid.UUID
) -> dict[str, Any] | None:
    """Switch maintenance on or off. Returns the new state, or None (and writes nothing) when it
    was already in that state, decided under the row lock."""
    row = _locked(db, MAINTENANCE)
    if bool((row.value or {}).get("enabled")) == enabled:
        return None
    row.value = (
        {
            "enabled": True,
            "message": message or DEFAULT_MAINTENANCE_MESSAGE,
            "since": datetime.now(UTC).isoformat(),
        }
        if enabled
        else {"enabled": False, "message": None, "since": None}
    )
    row.updated_by = actor_id
    db.flush()
    return maintenance_state(db)


def lock_session_issue(db: Session) -> None:
    """Shared lock, held to the end of the transaction, taken before any refresh row is created or
    claimed. A global sign-out takes the exclusive side first, so a sign-in in flight either
    commits before the sign-out revokes (and is revoked with the rest) or starts after it."""
    db.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": _SESSION_ISSUE_LOCK})


def lock_session_issue_exclusive(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SESSION_ISSUE_LOCK})


def set_session_cutoff(db: Session, *, actor_id: uuid.UUID) -> datetime:
    row = _locked(db, SESSION_CUTOFF)
    now = datetime.now(UTC)
    row.value = {"valid_after": now.isoformat()}
    row.updated_by = actor_id
    db.flush()
    return now
