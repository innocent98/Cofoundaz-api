"""Milestone retrospectives (PRD 21.5): What worked / What didn't / What we'll change.

The three answers are stored as one Fernet token (same key as entries). A milestone is offered
for a retro for 30 days after it completes, unless the founder already wrote or dismissed one.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import RoadmapStatus
from app.db.models.journal import JournalRetro
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.schemas.journal import RetroBody
from app.services.journal.encryption import decrypt_content, encrypt_content

OFFER_WINDOW_DAYS = 30


def _milestone(db: Session, startup_id: uuid.UUID, milestone_id: uuid.UUID) -> RoadmapMilestone:
    milestone = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id, RoadmapMilestone.id == milestone_id)
        .first()
    )
    if milestone is None:
        raise NotFound()
    return milestone


def _pack(body: RetroBody) -> str:
    return encrypt_content(json.dumps(body.model_dump()))


def present(row: JournalRetro) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "milestone_id": row.milestone_id,
        "milestone_title": row.milestone_title,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    out.update(
        json.loads(decrypt_content(row.content_encrypted or "")) if row.content_encrypted else {}
    )
    return out


def offers(db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID) -> list[dict[str, Any]]:
    since = datetime.now(UTC) - timedelta(days=OFFER_WINDOW_DAYS)
    handled = {
        r[0]
        for r in db.query(JournalRetro.milestone_id)
        .filter(JournalRetro.founder_id == founder_id, JournalRetro.milestone_id.isnot(None))
        .all()
    }
    rows = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(
            Roadmap.startup_id == startup_id,
            RoadmapMilestone.status == RoadmapStatus.done,
            RoadmapMilestone.updated_at >= since,
        )
        .order_by(RoadmapMilestone.updated_at.desc())
        .all()
    )
    return [
        {
            "milestone_id": m.id,
            "milestone_title": m.title,
            "completed_at": m.updated_at,
            "prompt": f"Run a 5-minute retro on {m.title}?",
        }
        for m in rows
        if m.id not in handled
    ]


def dismiss_offer(
    db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID, milestone_id: uuid.UUID
) -> None:
    milestone = _milestone(db, startup_id, milestone_id)
    exists = (
        db.query(JournalRetro.id)
        .filter_by(founder_id=founder_id, milestone_id=milestone_id)
        .first()
    )
    if exists is None:
        db.add(
            JournalRetro(
                startup_id=startup_id,
                founder_id=founder_id,
                milestone_id=milestone_id,
                milestone_title=milestone.title[:200],
                dismissed=True,
            )
        )
        db.flush()


def create(
    db: Session,
    *,
    startup_id: uuid.UUID,
    founder_id: uuid.UUID,
    milestone_id: uuid.UUID,
    body: RetroBody,
) -> JournalRetro:
    milestone = _milestone(db, startup_id, milestone_id)
    if milestone.status != RoadmapStatus.done:
        raise AppError("MILESTONE_NOT_DONE", "A retro can be run once the milestone is done.", 409)
    if not any([body.what_worked.strip(), body.what_didnt.strip(), body.what_change.strip()]):
        raise AppError(
            "VALIDATION_ERROR",
            "Write at least one answer.",
            422,
            field_errors=[{"field": "what_worked", "message": "Write at least one answer."}],
        )
    row = (
        db.query(JournalRetro)
        .filter_by(founder_id=founder_id, milestone_id=milestone_id)
        .with_for_update()
        .first()
    )
    if row is not None and row.content_encrypted is not None:
        raise AppError("RETRO_EXISTS", "You already ran a retro for this milestone.", 409)
    if row is None:
        row = JournalRetro(
            startup_id=startup_id,
            founder_id=founder_id,
            milestone_id=milestone_id,
            milestone_title=milestone.title[:200],
        )
        db.add(row)
    row.dismissed = False
    row.content_encrypted = _pack(body)
    db.flush()
    return row


def get(
    db: Session, *, founder_id: uuid.UUID, startup_id: uuid.UUID, retro_id: uuid.UUID
) -> JournalRetro:
    row = (
        db.query(JournalRetro)
        .filter_by(id=retro_id, founder_id=founder_id, startup_id=startup_id)
        .first()
    )
    if row is None or row.content_encrypted is None:
        raise NotFound()
    return row


def list_all(db: Session, *, founder_id: uuid.UUID, startup_id: uuid.UUID) -> list[JournalRetro]:
    return (
        db.query(JournalRetro)
        .filter(
            JournalRetro.founder_id == founder_id,
            JournalRetro.startup_id == startup_id,
            JournalRetro.content_encrypted.isnot(None),
        )
        .order_by(JournalRetro.created_at.desc())
        .all()
    )


def update(db: Session, row: JournalRetro, body: RetroBody) -> JournalRetro:
    row.content_encrypted = _pack(body)
    db.flush()
    return row


def delete(db: Session, row: JournalRetro) -> None:
    db.delete(row)
    db.flush()
