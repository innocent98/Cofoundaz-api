"""Validation Hub slice (c): the MVP feedback inbox (PRD 09.7, D-VAL-2/3).

Interview key quotes and open survey answers are **mirrored** into `feedback_items` on every read
of the inbox (and before clustering): editing the source changes the item, deleting the source
removes it, and a founder can dismiss an imported item to hide it. Manual entries are tagged
interview / in_app / email. Services flush; the endpoints commit.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import RoadmapStatus, TaskEffort
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.validation import (
    FeedbackClusterRun,
    FeedbackItem,
    FeedbackTheme,
    Interview,
    Survey,
    SurveyResponse,
)
from app.services.roadmap.service import recompute_milestone_progress

MANUAL_SOURCES = ("interview", "in_app", "email")
SOURCES = ("interview", "survey", "in_app", "email")
MAX_TEXT = 4000
MAX_LABEL = 255


def _key_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _interview_label(interview: Interview) -> str:
    # Never the interviewee's name: labels travel into exports and AI prompts.
    return f"Interview, {interview.segment}" if interview.segment else "Interview"


def _desired_imports(db: Session, startup_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for iv in db.query(Interview).filter_by(startup_id=startup_id):
        for quote in iv.key_quotes or []:
            text = str(quote).strip()
            if not text:
                continue
            out[f"interview:{iv.id}:{_key_hash(text)}"] = {
                "source": "interview",
                "text": text[:MAX_TEXT],
                "origin_type": "interview",
                "origin_id": iv.id,
                "origin_label": _interview_label(iv)[:MAX_LABEL],
                "received_on": iv.held_on,
            }
    surveys = {s.id: s for s in db.query(Survey).filter_by(startup_id=startup_id)}
    open_ids = {
        sid: {q["id"] for q in (s.questions or []) if q.get("type") == "open"}
        for sid, s in surveys.items()
    }
    if any(open_ids.values()):
        for resp in db.query(SurveyResponse).filter_by(startup_id=startup_id):
            for qid, value in (resp.answers or {}).items():
                if qid not in open_ids.get(resp.survey_id, set()):
                    continue
                if not isinstance(value, str) or not value.strip():
                    continue
                out[f"survey:{resp.id}:{qid}"] = {
                    "source": "survey",
                    "text": value.strip()[:MAX_TEXT],
                    "origin_type": "survey",
                    "origin_id": resp.survey_id,
                    "origin_label": f"Survey: {surveys[resp.survey_id].title}"[:MAX_LABEL],
                    "received_on": resp.submitted_at.date(),
                }
    return out


def sync_imports(db: Session, startup_id: uuid.UUID) -> None:
    """Make the imported items match their sources. Concurrent syncs are safe: inserts skip a
    key another request already wrote (partial unique index)."""
    desired = _desired_imports(db, startup_id)
    existing = {
        row.origin_key: row
        for row in db.query(FeedbackItem).filter(
            FeedbackItem.startup_id == startup_id, FeedbackItem.origin_key.isnot(None)
        )
    }
    for key, row in existing.items():
        if key not in desired:
            db.delete(row)
        else:
            want = desired[key]
            row.origin_label = want["origin_label"]
            row.received_on = want["received_on"]
    missing = [
        {"id": uuid.uuid4(), "startup_id": startup_id, "origin_key": key, **values}
        for key, values in desired.items()
        if key not in existing
    ]
    db.flush()
    if missing:
        db.execute(
            insert(FeedbackItem)
            .values(missing)
            .on_conflict_do_nothing(
                index_elements=["startup_id", "origin_key"],
                index_where=FeedbackItem.origin_key.isnot(None),
            )
        )
        db.flush()


# --- inbox -------------------------------------------------------------------------------------


def list_items(
    db: Session,
    startup_id: uuid.UUID,
    *,
    source: str | None = None,
    include_dismissed: bool = False,
) -> list[FeedbackItem]:
    query = db.query(FeedbackItem).filter_by(startup_id=startup_id)
    if source:
        query = query.filter_by(source=source)
    if not include_dismissed:
        query = query.filter_by(dismissed=False)
    return query.order_by(
        FeedbackItem.received_on.desc(), FeedbackItem.created_at.desc(), FeedbackItem.id
    ).all()


def source_counts(db: Session, startup_id: uuid.UUID) -> dict[str, int]:
    counts = dict.fromkeys(SOURCES, 0)
    for source, n in (
        db.query(FeedbackItem.source, func.count(FeedbackItem.id))
        .filter(FeedbackItem.startup_id == startup_id, FeedbackItem.dismissed.is_(False))
        .group_by(FeedbackItem.source)
    ):
        counts[source] = int(n)
    return counts


def get_item(db: Session, startup_id: uuid.UUID, item_id: Any) -> FeedbackItem:
    row = db.query(FeedbackItem).filter_by(id=item_id, startup_id=startup_id).first()
    if row is None:
        raise NotFound()
    return row


def _clean_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise AppError("VALIDATION_ERROR", "Feedback needs some text.", 422)
    return text[:MAX_TEXT]


def create_item(
    db: Session,
    startup_id: uuid.UUID,
    *,
    text: str,
    source: str,
    received_on: date | None,
    created_by: uuid.UUID,
) -> FeedbackItem:
    row = FeedbackItem(
        startup_id=startup_id,
        source=source,
        text=_clean_text(text),
        origin_type="manual",
        origin_label="Added by hand",
        received_on=received_on or datetime.now(UTC).date(),
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def _manual_only(row: FeedbackItem) -> None:
    if row.origin_key is not None:
        raise AppError(
            "IMPORTED_FEEDBACK",
            "This came from an interview or survey. Edit it there, or dismiss it here.",
            422,
        )


def update_item(
    db: Session,
    row: FeedbackItem,
    *,
    text: str | None = None,
    source: str | None = None,
    received_on: date | None = None,
) -> FeedbackItem:
    _manual_only(row)
    if text is not None:
        row.text = _clean_text(text)
    if source is not None:
        row.source = source
    if received_on is not None:
        row.received_on = received_on
    db.flush()
    return row


def delete_item(db: Session, row: FeedbackItem) -> dict[str, bool]:
    """Manual items are deleted; imported ones are dismissed (they would come back otherwise)."""
    if row.origin_key is None:
        db.delete(row)
        db.flush()
        return {"deleted": True}
    row.dismissed = True
    db.flush()
    return {"dismissed": True}


def restore_item(db: Session, row: FeedbackItem) -> FeedbackItem:
    row.dismissed = False
    db.flush()
    return row


def serialize_item(row: FeedbackItem) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source": row.source,
        "text": row.text,
        "origin": {
            "type": row.origin_type,
            "id": str(row.origin_id) if row.origin_id else None,
            "label": row.origin_label,
        },
        "imported": row.origin_key is not None,
        "dismissed": row.dismissed,
        "received_on": row.received_on.isoformat(),
        "created_at": row.created_at.isoformat(),
    }


# --- themes ------------------------------------------------------------------------------------


def create_run(
    db: Session, startup_id: uuid.UUID, *, item_count: int, themes: list[dict[str, Any]]
) -> FeedbackClusterRun:
    # Stamped here, not by the DB's now(): two runs in one transaction must still order newest
    # first (now() is the transaction start).
    run = FeedbackClusterRun(
        startup_id=startup_id, item_count=item_count, created_at=datetime.now(UTC)
    )
    db.add(run)
    db.flush()
    for theme in themes:
        db.add(
            FeedbackTheme(
                run_id=run.id,
                startup_id=startup_id,
                theme=theme["theme"],
                summary=theme["summary"],
                item_ids=theme["item_ids"],
            )
        )
    db.flush()
    return run


def latest_run(db: Session, startup_id: uuid.UUID) -> FeedbackClusterRun | None:
    return (
        db.query(FeedbackClusterRun)
        .filter_by(startup_id=startup_id)
        .order_by(FeedbackClusterRun.created_at.desc(), FeedbackClusterRun.id.desc())
        .first()
    )


def _label(theme: str, mentions: int, sources: int) -> str:
    plural = "source" if sources == 1 else "sources"
    return f"{theme} mentioned {mentions}× across {sources} {plural}"


def serialize_theme(theme: FeedbackTheme, items: dict[str, FeedbackItem]) -> dict[str, Any]:
    live = [items[i] for i in theme.item_ids or [] if i in items]
    sources = sorted({row.source for row in live})
    return {
        "id": str(theme.id),
        "theme": theme.theme,
        "summary": theme.summary,
        "mention_count": len(live),
        "source_count": len(sources),
        "sources": sources,
        "label": _label(theme.theme, len(live), len(sources)),
        "items": [serialize_item(row) for row in live],
        "roadmap_task_id": str(theme.roadmap_task_id) if theme.roadmap_task_id else None,
        "sent_at": theme.sent_at.isoformat() if theme.sent_at else None,
    }


def themes_view(db: Session, startup_id: uuid.UUID) -> dict[str, Any]:
    """The newest run's themes, most mentioned first; themes whose items are all gone drop out.
    Dismissed items still count (dismissing hides an item from the inbox, not from evidence)."""
    run = latest_run(db, startup_id)
    if run is None:
        return {"run": None, "themes": []}
    items = {str(r.id): r for r in db.query(FeedbackItem).filter_by(startup_id=startup_id)}
    themes = [
        serialize_theme(t, items)
        for t in db.query(FeedbackTheme).filter_by(run_id=run.id).order_by(FeedbackTheme.created_at)
    ]
    themes = [t for t in themes if t["mention_count"]]
    themes.sort(key=lambda t: (-t["mention_count"], -t["source_count"]))
    return {
        "run": {
            "id": str(run.id),
            "item_count": run.item_count,
            "created_at": run.created_at.isoformat(),
        },
        "themes": themes,
    }


def _target_milestone(
    db: Session, startup_id: uuid.UUID, milestone_id: uuid.UUID | None
) -> RoadmapMilestone:
    query = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id)
    )
    if milestone_id is not None:
        milestone = query.filter(RoadmapMilestone.id == milestone_id).first()
        if milestone is None:
            raise NotFound()
        return milestone
    milestone = (
        query.filter(RoadmapMilestone.status != RoadmapStatus.done)
        .order_by(RoadmapPhase.order, RoadmapMilestone.order, RoadmapMilestone.id)
        .first()
    )
    if milestone is None:
        raise AppError(
            "NO_OPEN_MILESTONE",
            "Your roadmap has no open milestone to add this to. Add one on the Roadmap first.",
            422,
        )
    return milestone


def send_to_roadmap(
    db: Session, startup_id: uuid.UUID, theme_id: Any, *, milestone_id: uuid.UUID | None
) -> dict[str, Any]:
    """Turn a theme into one roadmap task, once (the theme row is locked against double clicks)."""
    theme = (
        db.query(FeedbackTheme)
        .filter_by(id=theme_id, startup_id=startup_id)
        .with_for_update()
        .first()
    )
    if theme is None:
        raise NotFound()
    if theme.sent_at is not None:
        raise AppError("ALREADY_SENT", "This theme is already on your roadmap.", 409)
    milestone = _target_milestone(db, startup_id, milestone_id)
    items = {str(r.id): r for r in db.query(FeedbackItem).filter_by(startup_id=startup_id)}
    view = serialize_theme(theme, items)
    quotes = "\n".join(f'- "{i["text"][:200]}"' for i in view["items"][:3])
    description = (
        f"From MVP feedback: {theme.summary}\n\n"
        f"Mentioned {view['mention_count']}× across {view['source_count']} "
        f"{'source' if view['source_count'] == 1 else 'sources'}, for example:\n{quotes}"
    ).strip()
    order = db.query(func.max(RoadmapTask.order)).filter_by(milestone_id=milestone.id).scalar()
    task = RoadmapTask(
        milestone_id=milestone.id,
        title=theme.theme[:200],
        description=description,
        effort=TaskEffort.medium,
        status=RoadmapStatus.todo,
        order=0 if order is None else order + 1,
    )
    db.add(task)
    db.flush()
    recompute_milestone_progress(db, milestone)
    theme.roadmap_task_id = task.id
    theme.sent_at = datetime.now(UTC)
    db.flush()
    return {
        "theme": serialize_theme(theme, items),
        "task": {
            "id": str(task.id),
            "milestone_id": str(milestone.id),
            "milestone_title": milestone.title,
            "title": task.title,
            "description": task.description,
            "status": task.status.value,
        },
    }
