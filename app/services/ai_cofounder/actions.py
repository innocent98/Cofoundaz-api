"""Confirm-first actions (PRD 5.4.4): the model may PROPOSE a change in its reply; nothing touches
the workspace until the founder confirms it, and the reversible ones can be undone.

Safety rules in this module:
  * The model's proposal is parsed from a fenced `<actions>[...]</actions>` block at the end of its
    reply, validated field by field against an allow-list and size-capped. Anything else (unknown
    kind, unknown milestone key, bad date, oversized text) drops that one proposal. Parsing never
    raises: it runs after the paid model call, where nothing may fail.
  * The model never sees or returns a database id. Milestones are short keys (`M1`) mapped to ids
    by `context.milestone_keys`.
  * Execution re-checks everything at confirm time (the milestone may be gone, the role may have
    changed) and runs in a savepoint; the actor must hold the target module's write role.
  * Undo only reverses what the action itself did, and refuses (409) when the thing was changed
    since, so it can never wipe the founder's later edits.
"""

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass
from itertools import islice
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.core.logger import log
from app.db.models.ai_chat import AiAction, AiMessage
from app.db.models.document import Document
from app.db.models.enums import (
    DOCUMENT_ROLES,
    DocumentKind,
    MembershipRole,
    RoadmapStatus,
    TaskEffort,
)
from app.db.models.roadmap import RoadmapMilestone, RoadmapTask
from app.db.models.startup import Startup
from app.platform.events import event_bus
from app.services.ai_cofounder.agents import LEGAL_DISCLAIMER
from app.services.documents import service as documents
from app.services.roadmap.service import recompute_milestone_progress

KINDS = ("add_task", "update_roadmap", "draft_document")
MAX_PER_REPLY = 3
_MAX_SCANNED = 10  # raw items looked at, so a junk prefix can't hide a valid proposal
ROADMAP_WRITE_ROLES = frozenset({MembershipRole.founder, MembershipRole.team_member})
_DOC_KINDS = {
    "nda": DocumentKind.nda,
    "contractor_agreement": DocumentKind.contractor_agreement,
    "one_pager": DocumentKind.one_pager,
    "meeting_notes": DocumentKind.meeting_notes,
}
_LEGAL_DOC_KINDS = {DocumentKind.nda, DocumentKind.contractor_agreement}
_BLOCK = re.compile(r"<actions>(.*?)</actions>", re.DOTALL | re.IGNORECASE)
_OPEN_TAG = re.compile(r"<actions>.*$", re.DOTALL | re.IGNORECASE)
_MAX_TITLE, _MAX_REASON = 160, 200
_MAX_SECTIONS, _MAX_HEADING, _MAX_BODY = 8, 120, 3000

ACTIONS_PROMPT = (
    "You may propose changes the founder can confirm with one click. Only do so when they asked for "
    "something concrete or it clearly follows from the reply; never more than 3. Propose by ending "
    "your reply with ONE block, after your normal text, exactly in this form (JSON array):\n"
    '<actions>[{{"kind":"add_task","title":"...","milestone":"M1","effort":"small|medium|large"}},'
    '{{"kind":"update_roadmap","milestone":"M2","due_on":"YYYY-MM-DD","status":"todo|in_progress|done"}},'
    '{{"kind":"draft_document","doc_kind":"{doc_kinds}","title":"...",'
    '"sections":[{{"heading":"...","body":"..."}}]}}]</actions>\n'
    "Use only the milestone keys (M1, M2, ...) listed in the workspace data; omit milestone actions "
    "if none are listed. Allowed kinds for you: {kinds}. If you have nothing to propose, write no block."
)


@dataclass(frozen=True)
class Proposal:
    kind: str
    payload: dict[str, Any]


def _text(value: Any, cap: int) -> str:
    return " ".join(str(value or "").split())[:cap]


def _block_text(value: Any, cap: int) -> str:
    return str(value or "").strip()[:cap]


def _add_task(raw: dict, keys: dict[str, uuid.UUID]) -> Proposal | None:
    title, ms = _text(raw.get("title"), _MAX_TITLE), keys.get(str(raw.get("milestone")))
    if not title or ms is None:
        return None
    effort = raw.get("effort") if raw.get("effort") in {e.value for e in TaskEffort} else "medium"
    return Proposal(
        "add_task",
        {
            "title": title,
            "milestone_key": str(raw["milestone"]),
            "milestone_id": str(ms),
            "effort": effort,
        },
    )


def _update_roadmap(raw: dict, keys: dict[str, uuid.UUID]) -> Proposal | None:
    ms = keys.get(str(raw.get("milestone")))
    if ms is None:
        return None
    payload: dict[str, Any] = {"milestone_key": str(raw["milestone"]), "milestone_id": str(ms)}
    if raw.get("due_on"):
        try:
            payload["due_on"] = dt.date.fromisoformat(str(raw["due_on"])).isoformat()
        except ValueError:
            return None
    if raw.get("status") is not None:
        # `done` is left to the Roadmap page: completing a milestone also completes its tasks
        if raw["status"] not in {RoadmapStatus.todo.value, RoadmapStatus.in_progress.value}:
            return None
        payload["status"] = raw["status"]
    if "due_on" not in payload and "status" not in payload:
        return None
    return Proposal("update_roadmap", payload)


def _draft_document(raw: dict) -> Proposal | None:
    doc_kind, title, secs = (
        raw.get("doc_kind"),
        _text(raw.get("title"), _MAX_TITLE),
        raw.get("sections"),
    )
    if doc_kind not in _DOC_KINDS or not title or not isinstance(secs, list):
        return None
    clean = []
    for sec in secs[:_MAX_SECTIONS]:
        if isinstance(sec, dict):
            heading = _text(sec.get("heading"), _MAX_HEADING)
            body = _block_text(sec.get("body"), _MAX_BODY)
            if heading and body:
                clean.append({"heading": heading, "body": body})
    if not clean:
        return None
    return Proposal("draft_document", {"doc_kind": doc_kind, "title": title, "sections": clean})


def _one(raw: Any, allowed: tuple[str, ...], keys: dict[str, uuid.UUID]) -> Proposal | None:
    if not isinstance(raw, dict) or raw.get("kind") not in allowed:
        return None
    kind = raw["kind"]
    if kind == "add_task":
        return _add_task(raw, keys)
    if kind == "update_roadmap":
        return _update_roadmap(raw, keys)
    if kind == "draft_document":
        return _draft_document(raw)
    return None


def extract_actions(
    text: str, allowed: tuple[str, ...], keys: dict[str, uuid.UUID]
) -> tuple[str, list[Proposal]]:
    """(reply text with the block removed, the valid proposals). Never raises."""
    try:
        matches = _BLOCK.findall(text)
        clean = _OPEN_TAG.sub("", _BLOCK.sub("", text)).strip()
        if not matches:
            return clean, []
        raw = json.loads(matches[-1])
        if not isinstance(raw, list):
            return clean, []
        valid = (p for r in raw[:_MAX_SCANNED] if (p := _one(r, allowed, keys)) is not None)
        return clean, list(islice(valid, MAX_PER_REPLY))
    except Exception as exc:  # noqa: BLE001 - runs after the paid call: it must not raise
        log.warning(f"[ai] could not parse the actions block: {exc}")
        return _OPEN_TAG.sub("", _BLOCK.sub("", text)).strip(), []


def serialize_action(a: AiAction) -> dict[str, Any]:
    p = a.payload
    if a.kind == "add_task":
        label = f"Add as task: {p['title']}"
    elif a.kind == "update_roadmap":
        bits = [
            b
            for b in (
                p.get("status") and f"status {p['status']}",
                p.get("due_on") and f"due {p['due_on']}",
            )
            if b
        ]
        label = f"Update roadmap ({p['milestone_key']}): " + ", ".join(bits)
    else:
        label = f"Draft the document: {p['title']}"
    result = a.result or {}
    return {
        "id": str(a.id),
        "kind": a.kind,
        "label": label,
        "status": a.status,
        "undoable": a.status == "executed",
        "params": {k: v for k, v in p.items() if k != "milestone_id"},
        "href": result.get("href"),
        "result_label": result.get("label"),
    }


# ---------------------------------------------------------------- execution


def _load(
    db: Session, startup: Startup, user_id: uuid.UUID, message_id: uuid.UUID, action_id: uuid.UUID
) -> tuple[AiMessage, AiAction]:
    """The action, locked, for a message that belongs to this member's own conversation."""
    from app.db.models.ai_chat import AiConversation

    msg = (
        db.query(AiMessage)
        .join(AiConversation, AiConversation.id == AiMessage.conversation_id)
        .filter(
            AiMessage.id == message_id,
            AiMessage.removed_by_moderation_at.is_(None),
            AiConversation.startup_id == startup.id,
            AiConversation.created_by == user_id,
        )
        .first()
    )
    action = (
        db.query(AiAction)
        .filter(AiAction.id == action_id, AiAction.message_id == message_id)
        .with_for_update()
        .first()
        if msg is not None
        else None
    )
    if msg is None or action is None:
        raise NotFound()
    return msg, action


def _need_role(role: MembershipRole | None, allowed: frozenset[MembershipRole]) -> None:
    if role not in allowed:
        raise AppError("FORBIDDEN", "You don't have permission to do that.", 403)


def _milestone(db: Session, startup: Startup, milestone_id: str) -> RoadmapMilestone:
    from app.db.models.roadmap import Roadmap, RoadmapPhase

    m = (
        db.query(RoadmapMilestone)
        .join(RoadmapPhase, RoadmapPhase.id == RoadmapMilestone.phase_id)
        .join(Roadmap, Roadmap.id == RoadmapPhase.roadmap_id)
        .filter(RoadmapMilestone.id == uuid.UUID(milestone_id), Roadmap.startup_id == startup.id)
        .first()
    )
    if m is None:
        raise AppError("ACTION_TARGET_GONE", "That roadmap milestone no longer exists.", 409)
    return m


def _task_snapshot(t: RoadmapTask) -> dict[str, Any]:
    """Everything undo must find unchanged before it may delete the task."""
    return {
        "title": t.title,
        "description": t.description,
        "effort": t.effort.value,
        "status": t.status.value,
        "due_on": t.due_on.isoformat() if t.due_on else None,
        "assignee_id": str(t.assignee_id) if t.assignee_id else None,
        "milestone_id": str(t.milestone_id),
    }


def _do_add_task(db: Session, startup: Startup, p: dict[str, Any]) -> dict[str, Any]:
    m = _milestone(db, startup, p["milestone_id"])
    order = db.query(RoadmapTask).filter_by(milestone_id=m.id).count()
    t = RoadmapTask(
        milestone_id=m.id,
        title=p["title"],
        effort=TaskEffort(p["effort"]),
        status=RoadmapStatus.todo,
        order=order,
    )
    db.add(t)
    db.flush()
    recompute_milestone_progress(db, m)
    return {
        "task_id": str(t.id),
        "milestone_id": str(m.id),
        "snapshot": _task_snapshot(t),
        "href": "/roadmap",
        "label": f"Task added to {m.title}",
    }


def _do_update_roadmap(db: Session, startup: Startup, p: dict[str, Any]) -> dict[str, Any]:
    m = _milestone(db, startup, p["milestone_id"])
    before = {"due_on": m.due_on.isoformat() if m.due_on else None, "status": m.status.value}
    after = dict(before)
    if "due_on" in p:
        m.due_on = dt.date.fromisoformat(p["due_on"])
        after["due_on"] = p["due_on"]
    if "status" in p:
        m.status = RoadmapStatus(p["status"])
        after["status"] = p["status"]
    db.flush()
    return {
        "milestone_id": str(m.id),
        "before": before,
        "after": after,
        "href": "/roadmap",
        "label": f"Updated {m.title}",
    }


def _do_draft_document(
    db: Session, startup: Startup, user_id: uuid.UUID, p: dict[str, Any]
) -> dict[str, Any]:
    kind = _DOC_KINDS[p["doc_kind"]]
    sections = [dict(s) for s in p["sections"]]
    if kind in _LEGAL_DOC_KINDS:  # D-DOC-4: every legal draft carries the not-legal-advice notice
        sections.append({"heading": "Not legal advice", "body": LEGAL_DISCLAIMER})
    doc = documents.create_document(
        db,
        startup,
        created_by_id=user_id,
        kind=kind,
        title=p["title"],
        sections=sections,
        folder=None,
        template_key=None,
        ai_generated=True,
    )
    return {
        "document_id": str(doc.id),
        "href": f"/documents/{doc.id}",
        "label": f"Draft created: {doc.title}",
    }


def confirm_action(
    db: Session,
    startup: Startup,
    *,
    user_id: uuid.UUID,
    role: MembershipRole | None,
    message_id: uuid.UUID,
    action_id: uuid.UUID,
) -> AiAction:
    """Run a proposed action. Idempotent on an already executed one; 409 if it was dismissed,
    undone or failed. The caller commits."""
    _msg, a = _load(db, startup, user_id, message_id, action_id)
    if a.status == "executed":
        return a
    if a.status != "proposed":
        raise AppError("ACTION_NOT_PROPOSED", f"This action is already {a.status}.", 409)
    _need_role(role, DOCUMENT_ROLES if a.kind == "draft_document" else ROADMAP_WRITE_ROLES)
    try:
        with db.begin_nested():
            if a.kind == "add_task":
                result = _do_add_task(db, startup, a.payload)
            elif a.kind == "update_roadmap":
                result = _do_update_roadmap(db, startup, a.payload)
            else:
                result = _do_draft_document(db, startup, user_id, a.payload)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed write must not leave a half-done action
        log.error(f"[ai] action {a.id} failed: {exc}")
        a.status = "failed"
        db.flush()
        raise AppError("ACTION_FAILED", "That change could not be made.", 409) from exc
    a.status, a.result, a.executed_at = "executed", result, dt.datetime.now(dt.UTC)
    db.flush()
    event_bus.publish(
        db,
        "ai.action.executed",
        {
            "action_id": str(a.id),
            "kind": a.kind,
            "message_id": str(a.message_id),
            "startup_id": str(startup.id),
            "user_id": str(user_id),
        },
    )
    return a


def dismiss_action(
    db: Session,
    startup: Startup,
    *,
    user_id: uuid.UUID,
    message_id: uuid.UUID,
    action_id: uuid.UUID,
) -> AiAction:
    _msg, a = _load(db, startup, user_id, message_id, action_id)
    if a.status == "dismissed":
        return a
    if a.status != "proposed":
        raise AppError("ACTION_NOT_PROPOSED", f"This action is already {a.status}.", 409)
    a.status = "dismissed"
    db.flush()
    return a


def undo_action(
    db: Session,
    startup: Startup,
    *,
    user_id: uuid.UUID,
    role: MembershipRole | None,
    message_id: uuid.UUID,
    action_id: uuid.UUID,
) -> AiAction:
    _msg, a = _load(db, startup, user_id, message_id, action_id)
    if a.status == "undone":
        return a
    if a.status != "executed" or not a.result:
        raise AppError("ACTION_NOT_UNDOABLE", "Only a completed action can be undone.", 409)
    _need_role(role, DOCUMENT_ROLES if a.kind == "draft_document" else ROADMAP_WRITE_ROLES)
    r = a.result
    changed = AppError(
        "ACTION_CHANGED", "This was edited since, so it can't be undone automatically.", 409
    )
    with db.begin_nested():
        if a.kind == "add_task":
            t = db.get(RoadmapTask, uuid.UUID(r["task_id"]))
            if t is not None:
                m = _milestone(db, startup, r["milestone_id"])
                if _task_snapshot(t) != r.get("snapshot"):
                    raise changed  # edited, moved or assigned since: never wipe the founder's edits
                db.delete(t)
                db.flush()
                recompute_milestone_progress(db, m)
        elif a.kind == "update_roadmap":
            m = _milestone(db, startup, r["milestone_id"])
            now = {"due_on": m.due_on.isoformat() if m.due_on else None, "status": m.status.value}
            if now != r["after"]:
                raise changed
            m.due_on = (
                dt.date.fromisoformat(r["before"]["due_on"]) if r["before"]["due_on"] else None
            )
            m.status = RoadmapStatus(r["before"]["status"])
        else:
            doc = db.get(Document, uuid.UUID(r["document_id"]))
            if doc is not None and doc.trashed_at is None:
                documents.trash_document(db, doc, actor_id=user_id)  # recoverable from the trash
    a.status, a.undone_at = "undone", dt.datetime.now(dt.UTC)
    db.flush()
    return a
