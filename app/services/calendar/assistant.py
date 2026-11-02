"""Post-meeting summary (PRD 19.3 / D-CAL-1): notes or a transcript in, summary + decisions +
action items out. No meeting bot: the text is pasted by the user."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models.calendar import CalendarAttendee, CalendarEvent, CalendarMeeting
from app.platform.events import event_bus
from app.platform.llm import LLMMessage
from app.platform.llm_budget import metered_complete_json
from app.services.calendar.meetings import new_item_id

MAX_ITEMS = 12
SUMMARY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "action_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "decisions", "action_items"],
    "additionalProperties": False,
}
BUDGET_MESSAGE = "The AI budget for today is used up. Try again tomorrow."


def build_messages(ev: CalendarEvent, meeting: CalendarMeeting) -> list[LLMMessage]:
    system = (
        "You write post-meeting notes for a startup founder. Use only what the notes say; "
        "never invent people, numbers or commitments. Plain language, no markdown, no em or en "
        "dashes. 'summary' is 2 to 4 sentences. 'decisions' lists what was decided. "
        "'action_items' lists concrete follow-ups, each starting with a verb. "
        "Return empty lists when the notes contain none."
    )
    user = (
        f"Meeting: {ev.title}\nAgenda: {meeting.agenda or 'none given'}\n\n"
        f"Notes or transcript:\n{meeting.transcript}"
    )
    return [LLMMessage("system", system), LLMMessage("user", user)]


def summarise(db: Session, event_id: uuid.UUID) -> str:
    """Run the summary for one meeting. Returns the final assistant_state."""
    ev = db.get(CalendarEvent, event_id)
    meeting = db.query(CalendarMeeting).filter_by(event_id=event_id).with_for_update().one_or_none()
    if (
        ev is None
        or meeting is None
        or meeting.assistant_state != "queued"
        or not meeting.transcript
    ):
        return "skipped"
    out = metered_complete_json(
        db, ev.startup_id, build_messages(ev, meeting), schema=SUMMARY_SCHEMA, max_tokens=1500
    )
    if out is None:
        meeting.assistant_state, meeting.assistant_error = "failed", BUDGET_MESSAGE
        return "failed"
    items = [
        {"id": new_item_id(), "title": str(t).strip()[:300], "assignee_id": None, "task_id": None}
        for t in out.get("action_items", [])
        if str(t).strip()
    ][:MAX_ITEMS]
    meeting.summary = str(out.get("summary", "")).strip()[:4000]
    meeting.decisions = [str(d).strip()[:300] for d in out.get("decisions", []) if str(d).strip()][
        :MAX_ITEMS
    ]
    meeting.action_items = items
    meeting.assistant_state, meeting.assistant_error = "ready", None
    meeting.summarised_at = datetime.now(UTC)
    db.flush()
    who = {str(ev.created_by)} if ev.created_by else set()
    who |= {
        str(a.user_id)
        for a in db.query(CalendarAttendee).filter_by(event_id=ev.id)
        if a.user_id is not None
    }
    event_bus.publish(
        db,
        "calendar.meeting.summary_ready",
        {
            "startup_id": str(ev.startup_id),
            "event_id": str(ev.id),
            "title": ev.title,
            "action_item_count": len(items),
            "user_ids": sorted(who),
        },
    )
    return "ready"


def mark_failed(db: Session, event_id: uuid.UUID, message: str) -> None:
    meeting = db.query(CalendarMeeting).filter_by(event_id=event_id).one_or_none()
    if meeting is not None and meeting.assistant_state == "queued":
        meeting.assistant_state, meeting.assistant_error = "failed", message[:300]
