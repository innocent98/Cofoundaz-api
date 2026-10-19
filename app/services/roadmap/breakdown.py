"""AI milestone breakdown (PRD 05.3 "Ask AI to break this down"): an async job that appends
3-5 concrete tasks to a milestone. It never edits or deletes existing tasks (co-pilot principle);
the founder can delete any suggestion. Poll `GET /jobs/{id}`: `result.status` is `ready` with
`created_task_ids`, or `over_budget`.
"""

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.enums import JobStatus, RoadmapStatus, TaskEffort
from app.db.models.job import Job
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase, RoadmapTask
from app.db.models.startup import Startup
from app.platform.jobs import job_dispatcher
from app.platform.llm import LLMMessage

JOB_TYPE = "ai.roadmap.breakdown"
MIN_TASKS, MAX_TASKS = 3, 5


def _milestone_in(db: Session, startup: Startup, milestone_id: uuid.UUID) -> RoadmapMilestone:
    m = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(RoadmapMilestone.id == milestone_id, Roadmap.startup_id == startup.id)
        .first()
    )
    if m is None:
        raise NotFound()
    return m


def start_breakdown(db: Session, startup: Startup, milestone_id: uuid.UUID) -> Job:
    m = _milestone_in(db, startup, milestone_id)
    # Lock the milestone row first so two concurrent clicks serialize: the second waits for
    # the first to commit its job, then sees it and gets the 409 (D-RM-1 one run at a time).
    db.query(RoadmapMilestone.id).filter(RoadmapMilestone.id == m.id).with_for_update().one()
    running = (
        db.query(Job.id)
        .filter(
            Job.type == JOB_TYPE,
            Job.status.in_([JobStatus.queued, JobStatus.running]),
            Job.payload["milestone_id"].astext == str(m.id),
        )
        .first()
    )
    if running is not None:
        raise AppError("BREAKDOWN_IN_PROGRESS", "I'm already breaking this milestone down.", 409)
    return job_dispatcher.enqueue(
        db, JOB_TYPE, {"startup_id": str(startup.id), "milestone_id": str(m.id)}, startup.id
    )


def breakdown_schema() -> dict[str, Any]:
    task = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "effort": {"type": "string", "enum": [e.value for e in TaskEffort]},
        },
        "required": ["title", "effort"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": MIN_TASKS,
                "maxItems": MAX_TASKS,
                "items": task,
            }
        },
        "required": ["tasks"],
        "additionalProperties": False,
    }


def build_breakdown_messages(
    *,
    milestone: str,
    description: str | None,
    existing: list[str],
    stage: str | None,
    industry: str | None,
) -> list[LLMMessage]:
    system = (
        "You are an AI co-founder breaking a startup milestone into concrete tasks. Return 3-5 "
        "tasks, each a short imperative title under 80 characters that someone could finish in "
        "a day or two, with an effort of small, medium or large. Don't repeat existing tasks. "
        "No numbering, no preamble."
    )
    user = (
        f"Startup stage: {stage or 'unspecified'}. Industry: {industry or 'unspecified'}.\n"
        f"Milestone: {milestone}\n"
        f"Details: {description or 'none'}\n"
        f"Existing tasks: {'; '.join(existing) if existing else 'none'}"
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


def apply_breakdown(db: Session, milestone: RoadmapMilestone, rows: Any) -> list[RoadmapTask]:
    """Append the model's tasks (validated, deduped against existing titles, clamped)."""
    existing = {
        t.title.strip().lower()
        for t in db.query(RoadmapTask.title).filter(RoadmapTask.milestone_id == milestone.id)
    }
    next_order = (
        db.query(func.max(RoadmapTask.order))
        .filter(RoadmapTask.milestone_id == milestone.id)
        .scalar()
    )
    order = 0 if next_order is None else next_order + 1
    created: list[RoadmapTask] = []
    for row in rows if isinstance(rows, list) else []:
        title = str((row or {}).get("title") or "").strip()[:200] if isinstance(row, dict) else ""
        if not title or title.lower() in existing:
            continue
        effort = row.get("effort")
        task = RoadmapTask(
            milestone_id=milestone.id,
            title=title,
            effort=(
                TaskEffort(effort) if effort in TaskEffort._value2member_map_ else TaskEffort.medium
            ),
            status=RoadmapStatus.todo,
            order=order,
        )
        db.add(task)
        created.append(task)
        existing.add(title.lower())
        order += 1
        if len(created) >= MAX_TASKS:
            break
    db.flush()
    return created
