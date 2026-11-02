"""Dashboard briefing v2: structured risks/opportunities/actions and the "Do it" accept.

The LLM proposes rows; this module is the only place they're trusted. `normalize_*` clamps
and validates them (the model can ignore `maxItems` or drift from an enum), and the server
owns every link via MODULE_HREFS, so the AI can never point a CTA at an arbitrary URL.
"""

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.models.dashboard import DailyBriefing
from app.db.models.enums import BriefingStatus
from app.db.models.mission import MissionTask
from app.db.models.startup import Startup
from app.db.models.user import User
from app.platform.activity import write_activity
from app.services.dashboard.ai_briefing import BRIEFING_MODULES, MAX_ITEMS, SEVERITIES
from app.services.mission.service import add_custom_task, ensure_today_mission, serialize_task

MODULE_HREFS: dict[str, str] = {
    "mission": "/mission",
    "roadmap": "/roadmap",
    "health": "/health",
    "validation": "/validation",
    "business_builder": "/business-builder",
    "marketing": "/marketing",
    "finance": "/finance",
    "documents": "/documents",
    "team": "/team",
    "assessment": "/assessment",
}
assert set(MODULE_HREFS) == set(BRIEFING_MODULES)  # nosec B101 - import-time invariant

_MAX_TEXT = 280
_MAX_LABEL = 120


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _module(row: dict[str, Any]) -> str | None:
    m = row.get("module")
    return m if m in MODULE_HREFS else None


def normalize_risks(rows: Any) -> list[dict[str, Any]]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        text = _clean(r.get("text") if isinstance(r, dict) else None, _MAX_TEXT)
        if not text:
            continue
        sev = r.get("severity")
        out.append(
            {"severity": sev if sev in SEVERITIES else "medium", "text": text, "module": _module(r)}
        )
    return out[:MAX_ITEMS]


def normalize_opportunities(rows: Any) -> list[dict[str, Any]]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        text = _clean(r.get("text") if isinstance(r, dict) else None, _MAX_TEXT)
        if text:
            out.append({"text": text, "module": _module(r)})
    return out[:MAX_ITEMS]


def normalize_actions(rows: Any) -> list[dict[str, Any]]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        label = _clean(r.get("label") if isinstance(r, dict) else None, _MAX_LABEL)
        if not label:
            continue
        module = _module(r)
        kind = r.get("kind")
        # A navigate action with nowhere to go is useless; treat it as a task.
        if kind != "navigate" or module is None:
            kind = "task"
        out.append({"label": label, "kind": kind, "module": module, "accepted_task_id": None})
    return out[:MAX_ITEMS]


def prose(rows: list[dict[str, Any]]) -> str:
    """Legacy text columns: the rows joined, for readers that predate the items."""
    return " ".join(r["text"] for r in rows)


def _href(module: str | None) -> str | None:
    return MODULE_HREFS.get(module) if module else None


def serialize_blocks(row: DailyBriefing) -> dict[str, Any]:
    st = row.status.value
    risks = [
        {"index": i, "severity": r["severity"], "text": r["text"], "href": _href(r.get("module"))}
        for i, r in enumerate(row.risk_items or [])
    ]
    opps = [
        {"index": i, "text": r["text"], "href": _href(r.get("module"))}
        for i, r in enumerate(row.opportunity_items or [])
    ]
    actions = [
        {
            "index": i,
            "label": a["label"],
            "kind": a["kind"],
            "href": _href(a.get("module")),
            "accepted": a.get("accepted_task_id") is not None or a.get("accepted") is True,
            "mission_task_id": a.get("accepted_task_id"),
        }
        for i, a in enumerate(row.actions or [])
    ]
    return {
        "briefing": {"status": st, "message": row.briefing, "id": str(row.id), "actions": actions},
        "risks": {"status": st, "message": row.risks, "items": risks},
        "opportunities": {"status": st, "message": row.opportunities, "items": opps},
    }


def accept_action(
    db: Session, startup: Startup, user: User, briefing_id: Any, index: int
) -> dict[str, Any]:
    """Run briefing action `index` ("Do it"). Idempotent: a repeat returns the same outcome.

    `task` actions add the label to today's mission; `navigate` actions just record the
    accept and hand back the route. The briefing row is locked (FOR UPDATE) so two concurrent
    clicks can't both add a task.
    """
    row = (
        db.query(DailyBriefing)
        .filter(DailyBriefing.id == briefing_id, DailyBriefing.startup_id == startup.id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise NotFound()
    if row.status != BriefingStatus.ready:
        raise AppError("BRIEFING_NOT_READY", "This briefing is still being written.", 409)
    if row.briefing_date != date.today():
        raise AppError(
            "BRIEFING_STALE", "That briefing is from an earlier day. Use today's briefing.", 409
        )
    actions = [dict(a) for a in (row.actions or [])]
    if not 0 <= index < len(actions):
        raise NotFound()
    action = actions[index]
    href = _href(action.get("module"))

    if action["kind"] == "navigate":
        if not action.get("accepted"):
            action["accepted"] = True
            row.actions = actions  # reassign: JSONB isn't mutation-tracked
            db.flush()
        return {"action_index": index, "kind": "navigate", "href": href, "mission_task": None}

    task = None
    if action.get("accepted_task_id"):
        task = db.get(MissionTask, action["accepted_task_id"])
    if task is None:
        mission = ensure_today_mission(db, startup)
        task = add_custom_task(db, mission, action["label"])
        action["accepted_task_id"] = str(task.id)
        row.actions = actions
        db.flush()
        name = (user.profile.full_name if user.profile else None) or "A teammate"
        write_activity(
            db,
            startup_id=startup.id,
            actor_user_id=user.id,
            action="mission.task.added",
            entity_type="mission_task",
            entity_id=task.id,
            summary=f"{name} added '{task.title}' to today's mission",
            meta={"source": "briefing", "briefing_id": str(row.id), "action_index": index},
        )
    return {
        "action_index": index,
        "kind": "task",
        "href": href,
        "mission_task": serialize_task(task),
    }
