"""AI journal-prompt inputs. Privacy: this module reads ONLY operational signals
(roadmap milestones + mission focus). It must never import JournalEntry or MoodLog."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import RoadmapStatus
from app.db.models.mission import Mission, MissionTask
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.platform.llm import LLMMessage


def journal_prompt_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"prompt": {"type": "string"}},
        "required": ["prompt"],
        "additionalProperties": False,
    }


def gather_prompt_context(db: Session, startup_id: uuid.UUID) -> tuple[str | None, str | None]:
    """Return (most-recent shipped milestone title, current mission focus). Operational only."""
    milestone = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id, RoadmapMilestone.status == RoadmapStatus.done)
        .order_by(RoadmapMilestone.updated_at.desc())
        .first()
    )
    milestone_title = milestone.title if milestone is not None else None

    mission = (
        db.query(Mission)
        .filter(Mission.startup_id == startup_id)
        .order_by(Mission.mission_date.desc())
        .first()
    )
    mission_focus: str | None = None
    if mission is not None:
        task = (
            db.query(MissionTask)
            .filter_by(mission_id=mission.id)
            .order_by(MissionTask.order)
            .first()
        )
        mission_focus = task.title if task is not None else None
    return milestone_title, mission_focus


def build_journal_prompt_messages(
    *, milestone_title: str | None, mission_focus: str | None
) -> list[LLMMessage]:
    """Prompt for one warm reflective journal question grounded in operational progress only."""
    lines = []
    if milestone_title:
        lines.append(f"Recently shipped: {milestone_title}")
    if mission_focus:
        lines.append(f"Current focus: {mission_focus}")
    context = "\n".join(lines)
    system = (
        "You are a warm journaling coach. Write ONE short, open reflective question (max 200 "
        "characters) for a founder's private daily journal, grounded in their recent progress. "
        "One question only, no preamble."
    )
    user = f"Founder's recent progress:\n{context}"
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]
