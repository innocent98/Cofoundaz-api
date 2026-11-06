"""Suggested actions (gap row 15, D-AI-3a): cards built from what the app already generates.

No model call and no new spend. Sources, all read-only: today's dashboard briefing (actions and
opportunities), pending health recommendations and ready learning recommendations. Cards are per
member (dismissing is personal) and deduplicated by `(user, source, source_ref)`, so a dismissed or
accepted card never comes back. Switching "Proactive suggestions" off in AI settings empties the
list. Every card links to a module page from a fixed allow-list; the model never supplies a URL."""

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.ai_chat import AiSuggestion
from app.db.models.dashboard import DailyBriefing
from app.db.models.enums import BriefingStatus, EnrichmentStatus, RecommendationStatus
from app.db.models.health_score import HealthRecommendation
from app.db.models.learning import AcademyCourse, LearningRecommendation
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.ai_preferences import get_prefs
from app.services.dashboard.briefing import MODULE_HREFS

MAX_NEW_PER_REFRESH = 5
MAX_LIST = 20
DEFAULT_SNOOZE_DAYS = 7
NOTIFY_TITLE = "Your AI Co-Founder has a suggestion for you"


@dataclass(frozen=True)
class Candidate:
    source: str
    ref: str
    title: str
    body: str | None
    href: str | None


def _ref(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode(), usedforsecurity=False).hexdigest()[:16]


def _briefing_candidates(db: Session, s: Startup) -> list[Candidate]:
    row = (
        db.query(DailyBriefing)
        .filter_by(startup_id=s.id, briefing_date=dt.date.today(), status=BriefingStatus.ready)
        .first()
    )
    if row is None:
        return []
    out: list[Candidate] = []
    for a in row.actions or []:
        if isinstance(a, dict) and a.get("label") and not a.get("accepted_task_id"):
            href = MODULE_HREFS.get(a.get("module") or "", "/mission")
            out.append(
                Candidate(
                    "briefing",
                    _ref("a", str(row.briefing_date), a["label"]),
                    a["label"][:200],
                    None,
                    href,
                )
            )
    for o in row.opportunity_items or []:
        if isinstance(o, dict) and o.get("text"):
            ohref = MODULE_HREFS.get(o.get("module") or "")
            out.append(
                Candidate(
                    "opportunity",
                    _ref("o", str(row.briefing_date), o["text"]),
                    o["text"][:200],
                    None,
                    ohref,
                )
            )
    return out


def _health_candidates(db: Session, s: Startup) -> list[Candidate]:
    recs = (
        db.query(HealthRecommendation)
        .filter_by(startup_id=s.id, status=RecommendationStatus.pending)
        .order_by(HealthRecommendation.priority.asc())
        .limit(3)
        .all()
    )
    return [
        Candidate("health", str(r.id), r.title[:200], (r.body or "")[:300] or None, "/health")
        for r in recs
    ]


def _learning_candidates(db: Session, s: Startup) -> list[Candidate]:
    rows = (
        db.query(LearningRecommendation, AcademyCourse)
        .join(AcademyCourse, AcademyCourse.id == LearningRecommendation.course_id)
        .filter(
            LearningRecommendation.startup_id == s.id,
            LearningRecommendation.status == EnrichmentStatus.ready,
            LearningRecommendation.reason.isnot(None),
        )
        .limit(2)
        .all()
    )
    return [
        Candidate(
            "learning",
            f"{r.course_id}:{r.context_key}"[:80],
            c.title[:200],
            (r.reason or "")[:300],
            "/academy",
        )
        for r, c in rows
    ]


def refresh(db: Session, startup: Startup, user_id: uuid.UUID) -> int:
    """Create cards for source items this member has not seen yet. Returns how many were new."""
    candidates = (
        _briefing_candidates(db, startup)
        + _health_candidates(db, startup)
        + _learning_candidates(db, startup)
    )
    created = 0
    for c in candidates:
        if created >= MAX_NEW_PER_REFRESH:
            break
        new_id = db.execute(
            pg_insert(AiSuggestion)
            .values(
                id=uuid.uuid4(),
                startup_id=startup.id,
                user_id=user_id,
                source=c.source,
                source_ref=c.ref,
                title=c.title,
                body=c.body,
                href=c.href,
                status="new",
            )
            .on_conflict_do_nothing(constraint="uq_ai_suggestion")
            .returning(AiSuggestion.id)
        ).scalar_one_or_none()
        if new_id is None:
            continue
        created += 1
        event_bus.publish(
            db,
            "ai.suggestion.created",
            {
                "suggestion_id": str(new_id),
                "startup_id": str(startup.id),
                "user_id": str(user_id),
                "source": c.source,
                # one notification per refresh batch; the digest batches across batches
                "notify": created == 1,
            },
        )
    return created


def list_suggestions(db: Session, startup: Startup, user_id: uuid.UUID) -> dict[str, Any]:
    if not get_prefs(db, user_id=user_id, startup_id=startup.id).proactive_suggestions:
        return {"enabled": False, "items": []}
    refresh(db, startup, user_id)
    now = dt.datetime.now(dt.UTC)
    rows = (
        db.query(AiSuggestion)
        .filter(
            AiSuggestion.startup_id == startup.id,
            AiSuggestion.user_id == user_id,
            or_(
                AiSuggestion.status == "new",
                (AiSuggestion.status == "snoozed") & (AiSuggestion.snoozed_until <= now),
            ),
        )
        .order_by(AiSuggestion.created_at.desc(), AiSuggestion.id.asc())
        .limit(MAX_LIST)
        .all()
    )
    return {"enabled": True, "items": [serialize(r) for r in rows]}


def serialize(r: AiSuggestion) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "source": r.source,
        "title": r.title,
        "body": r.body,
        "href": r.href,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def act(
    db: Session,
    startup: Startup,
    user_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    op: str,
    *,
    days: int = DEFAULT_SNOOZE_DAYS,
) -> AiSuggestion:
    """accept | dismiss | snooze on the caller's own card. Idempotent when repeated; an accepted
    or dismissed card cannot be changed to the other."""
    r = (
        db.query(AiSuggestion)
        .filter_by(id=suggestion_id, startup_id=startup.id, user_id=user_id)
        .with_for_update()
        .first()
    )
    if r is None:
        raise NotFound()
    target = {"accept": "accepted", "dismiss": "dismissed", "snooze": "snoozed"}[op]
    if r.status in ("accepted", "dismissed") and r.status != target:
        raise AppError("SUGGESTION_CLOSED", f"This suggestion was already {r.status}.", 409)
    r.status = target
    r.snoozed_until = (
        (dt.datetime.now(dt.UTC) + dt.timedelta(days=days)) if op == "snooze" else None
    )
    db.flush()
    return r
