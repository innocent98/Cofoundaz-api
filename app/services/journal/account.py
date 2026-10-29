"""Whole-journal operations: export, permanent delete, prompt dismissal, settings."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.journal import (
    JournalEntry,
    JournalPrompt,
    JournalRetro,
    JournalSettings,
    MoodLog,
)
from app.schemas.journal import JournalSettingsUpdate
from app.services.journal import retros as retro_svc
from app.services.journal.encryption import decrypt_content
from app.services.journal.service import JournalService


def export_all(db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID) -> dict[str, Any]:
    entries = (
        db.query(JournalEntry)
        .filter(JournalEntry.startup_id == startup_id, JournalEntry.founder_id == founder_id)
        .order_by(JournalEntry.date.asc())
        .all()
    )
    retros = retro_svc.list_all(db, founder_id=founder_id, startup_id=startup_id)
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "entries": [
            {
                "date": e.date.isoformat(),
                "mood": JournalService.score_to_mood(e.mood).value,
                "stress": e.stress,
                "content": decrypt_content(e.content_encrypted),
            }
            for e in entries
        ],
        "retros": [
            {
                **{
                    k: v for k, v in retro_svc.present(r).items() if k not in ("id", "milestone_id")
                },
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            }
            for r in retros
        ],
    }


def delete_all(db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID) -> dict[str, int]:
    """Destroy the founder's whole journal in this workspace. Returns counts only."""
    own = {"startup_id": startup_id, "founder_id": founder_id}
    counts = {
        "entries": db.query(JournalEntry).filter_by(**own).delete(synchronize_session=False),
        "mood_logs": db.query(MoodLog).filter_by(**own).delete(synchronize_session=False),
        "retros": db.query(JournalRetro).filter_by(**own).delete(synchronize_session=False),
        "prompts": db.query(JournalPrompt).filter_by(**own).delete(synchronize_session=False),
    }
    # Preferences and the passcode go too: the journal returns to its first-run state.
    db.query(JournalSettings).filter_by(**own).delete(synchronize_session=False)
    db.flush()
    return counts


def apply_settings(row: JournalSettings, data: JournalSettingsUpdate) -> None:
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
