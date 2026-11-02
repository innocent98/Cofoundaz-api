"""Learn next (D-ACA-3): the shelf, the hero course and one reason per hero course.

The shelf is the courses for the startup's stage that the learner has not finished, ranked by how
many of the startup's gaps a course covers (Assessment dimensions scoring under ``GAP_BELOW``, and
Validation signals such as "no customer interview logged yet"), then beginner first, then catalog
order. The first course is the hero. Its reason is templated at once, and an AI job rewrites it.
A reason is regenerated when the stage or the gaps change (``context_key``), never on a timer.
"""

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.enums import CourseLevel, EnrichmentStatus, StartupStage
from app.db.models.learning import AcademyCourse, LearningRecommendation
from app.db.models.validation import Experiment, Interview
from app.platform.events import event_bus
from app.platform.jobs import job_dispatcher
from app.services.health_score.service import latest_completed_result

GAP_BELOW = 60  # an Assessment dimension under this score is a gap
MAX_ASSESSMENT_GAPS = 2

_LEVEL_RANK: dict[CourseLevel, int] = {
    CourseLevel.beginner: 0,
    CourseLevel.intermediate: 1,
    CourseLevel.advanced: 2,
}
# An Assessment dimension to the course gap tags it points at.
_DIMENSION_TAGS: dict[str, tuple[str, ...]] = {
    "product": ("product",),
    "market": ("market",),
    "money": ("money", "pricing"),
    "legal": ("legal",),
    "team": ("team",),
}
# How each gap reads in the templated reason.
_GAP_PHRASE: dict[str, str] = {
    "product": "Your assessment flagged product as a gap",
    "market": "Your assessment flagged market as a gap",
    "money": "Your assessment flagged money as a gap",
    "pricing": "Your assessment flagged pricing as a gap",
    "legal": "Your assessment flagged legal as a gap",
    "team": "Your assessment flagged team as a gap",
    "customers": "You have not logged a customer interview yet",
    "demand": "You have not run a demand test yet",
}


@dataclass(frozen=True)
class Gaps:
    """The startup's gap tags, most pressing first."""

    tags: tuple[str, ...]

    @property
    def key(self) -> str:
        return ",".join(sorted(self.tags))


def gather_gaps(db: Session, startup_id: uuid.UUID) -> Gaps:
    tags: list[str] = []
    result = latest_completed_result(db, startup_id)
    if result is not None:
        weakest = sorted(
            (score, dim) for dim, score in result.dimension_scores.items() if score < GAP_BELOW
        )
        for _score, dim in weakest[:MAX_ASSESSMENT_GAPS]:
            tags.extend(t for t in _DIMENSION_TAGS.get(dim, ()) if t not in tags)
    if db.query(Interview.id).filter_by(startup_id=startup_id).first() is None:
        tags.append("customers")
    if db.query(Experiment.id).filter_by(startup_id=startup_id).first() is None:
        tags.append("demand")
    return Gaps(tuple(tags))


def matched_gaps(course: AcademyCourse, gaps: Gaps) -> list[str]:
    return [tag for tag in gaps.tags if tag in course.gap_tags]


def context_key(stage: StartupStage | None, gaps: Gaps) -> str:
    raw = f"{stage.value if stage is not None else ''}|{gaps.key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def rank_courses(
    courses: list[AcademyCourse],
    stage: StartupStage | None,
    completed_ids: set[str],
    gaps: Gaps,
) -> list[AcademyCourse]:
    """Stage match (beginner courses when no stage), unfinished only, best gap fit first."""
    if stage is None:
        pool = [c for c in courses if c.level == CourseLevel.beginner]
    else:
        pool = [c for c in courses if stage.value in c.stage_tags]
    remaining = [c for c in pool if c.id not in completed_ids]
    keys: tuple[Callable[[AcademyCourse], int], ...] = (
        lambda c: -len(matched_gaps(c, gaps)),
        lambda c: _LEVEL_RANK[c.level],
        lambda c: c.position,
    )
    return sorted(remaining, key=lambda c: tuple(key(c) for key in keys))


def gap_phrase(tag: str) -> str:
    return _GAP_PHRASE.get(tag, tag)


def templated_reason(
    stage: StartupStage | None, course: AcademyCourse | None, matched: list[str]
) -> str:
    if matched and course is not None:
        return f"{_GAP_PHRASE[matched[0]]}, and {course.title} covers it."[:300]
    if stage is None:
        return "Recommended to help you get started."
    return f"Recommended for your {stage.value} stage."


def _enqueue(db: Session, startup_id: uuid.UUID, course_id: str, key: str) -> None:
    job_dispatcher.enqueue(
        db,
        "ai.learning.recommendations",
        {"startup_id": str(startup_id), "course_id": course_id, "context_key": key},
        startup_id,
    )


def hero_reason(
    db: Session,
    *,
    startup_id: uuid.UUID,
    user_id: uuid.UUID,
    stage: StartupStage | None,
    hero: AcademyCourse,
    gaps: Gaps,
) -> LearningRecommendation:
    """The reason row for ``hero``, created or regenerated when its context changed.

    Creating a row for a new hero after the stage moved on also tells the learner ("New course
    recommended for your stage"); the very first row for a workspace does not.
    """
    key = context_key(stage, gaps)
    matched = matched_gaps(hero, gaps)
    stage_val = stage.value if stage is not None else None
    row = (
        db.query(LearningRecommendation).filter_by(startup_id=startup_id, course_id=hero.id).first()
    )
    if row is not None:
        if row.context_key != key:
            row.context_key = key
            row.stage = stage_val
            row.reason = templated_reason(stage, hero, matched)
            row.status = EnrichmentStatus.generating
            db.flush()
            _enqueue(db, startup_id, hero.id, key)
        return row
    earlier = db.query(LearningRecommendation).filter_by(startup_id=startup_id).all()
    try:
        with db.begin_nested():
            row = LearningRecommendation(
                startup_id=startup_id,
                course_id=hero.id,
                context_key=key,
                stage=stage_val,
                reason=templated_reason(stage, hero, matched),
                status=EnrichmentStatus.generating,
            )
            db.add(row)
            db.flush()
    except IntegrityError:
        # A concurrent caller won the race -- their row is now committed and visible.
        return (
            db.query(LearningRecommendation)
            .filter_by(startup_id=startup_id, course_id=hero.id)
            .one()
        )
    _enqueue(db, startup_id, hero.id, key)
    if earlier and all(r.stage != stage_val for r in earlier):
        event_bus.publish(
            db,
            "learning.course.recommended",
            {
                "startup_id": str(startup_id),
                "user_id": str(user_id),
                "course_id": hero.id,
                "course_title": hero.title,
                "stage": stage_val,
            },
        )
    return row


def hero_view(row: LearningRecommendation, hero: AcademyCourse, gaps: Gaps) -> dict[str, Any]:
    return {
        "course_id": hero.id,
        "reason": row.reason,
        "reason_source": "ai" if row.status == EnrichmentStatus.ready else "template",
        "matched_gaps": matched_gaps(hero, gaps),
    }
