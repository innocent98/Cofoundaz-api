import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Cookie, Depends, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_verified_user
from app.core.config import settings
from app.core.envelope import success_response
from app.core.errors import NotFound
from app.db.models.enums import CalendarEventType, MembershipRole
from app.db.models.membership import Membership
from app.db.models.user import User
from app.db.session import get_db
from app.db.tenancy import require_role, require_workspace
from app.schemas.calendar import (
    ActionItemPatch,
    AddToTasksIn,
    AssistantIn,
    EventCreate,
    EventUpdate,
    MeetingCreate,
    MeetingUpdate,
    ReminderRuleIn,
    SyncSettingsIn,
)
from app.services.calendar import connections as conn_svc
from app.services.calendar import feed as feed_svc
from app.services.calendar import meetings as meetings_svc
from app.services.calendar import milestones as milestones_svc
from app.services.calendar import reminders as reminders_svc
from app.services.calendar import service as calendar_svc
from app.services.calendar import sync as sync_svc

router = APIRouter()
# Founders and team members only (spec D4); other roles reach meetings they are invited to via
# `_member` routes, read-only (D-CAL-6).
_calendar = require_role(MembershipRole.founder, MembershipRole.team_member)
_founder = require_role(MembershipRole.founder)
_member = require_workspace
SYNC_MIN_INTERVAL = timedelta(seconds=10)
OAUTH_COOKIE = "cfz_cal_oauth"
OAUTH_COOKIE_PATH = "/api/v1/calendar/sync"


@router.post("/events", response_model=dict[str, Any])
def create_event(
    payload: EventCreate,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = calendar_svc.create_event(
        db, startup_id=membership.startup_id, created_by=user.id, data=payload
    )
    calendar_svc.queue_pushes(db, membership.startup_id)
    db.commit()
    return success_response(calendar_svc.serialize_event(row).model_dump(mode="json"))


@router.get("/events", response_model=dict[str, Any])
def list_events(
    date_from: date | None = None,
    date_to: date | None = None,
    type: CalendarEventType | None = None,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """One list for three views: Month, Week and Agenda differ only in the range they ask for."""
    rows = calendar_svc.list_events(
        db,
        startup_id=membership.startup_id,
        date_from=date_from,
        date_to=date_to,
        type=type,
        viewer_id=user.id,
    )
    return success_response(
        {"events": [calendar_svc.serialize_event(r).model_dump(mode="json") for r in rows]}
    )


@router.get("/feed", response_model=dict[str, Any])
def get_feed(
    date_from: date,
    date_to: date,
    layers: str | None = Query(default=None, description="comma list; default all"),
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """PRD 19.1: every layer in one range-bounded list (max 366 days)."""
    items = feed_svc.build_feed(
        db,
        startup_id=membership.startup_id,
        user_id=user.id,
        date_from=date_from,
        date_to=date_to,
        layers=feed_svc.parse_layers(layers),
    )
    return success_response(
        {"items": items, "counts": feed_svc.count_in_range(items), "layers": list(feed_svc.LAYERS)}
    )


@router.get("/events/{event_id}", response_model=dict[str, Any])
def get_event(
    event_id: uuid.UUID,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = calendar_svc.get_event(
        db, startup_id=membership.startup_id, event_id=event_id, viewer_id=user.id
    )
    return success_response(calendar_svc.serialize_event(row).model_dump(mode="json"))


@router.patch("/events/{event_id}", response_model=dict[str, Any])
def update_event(
    event_id: uuid.UUID,
    payload: EventUpdate,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    row = calendar_svc.update_event(
        db, startup_id=membership.startup_id, event_id=event_id, data=payload, viewer_id=user.id
    )
    calendar_svc.queue_pushes(db, membership.startup_id)
    db.commit()
    return success_response(calendar_svc.serialize_event(row).model_dump(mode="json"))


@router.delete("/events/{event_id}", response_model=dict[str, Any])
def delete_event(
    event_id: uuid.UUID,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    calendar_svc.delete_event(db, startup_id=membership.startup_id, event_id=event_id, viewer=user)
    db.commit()
    return success_response({"deleted": True})


@router.get("/milestones", response_model=dict[str, Any])
def list_milestones(
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Read-only projection of this workspace's roadmap milestones (spec D1).

    Owns no table and never writes: editing a milestone stays in Roadmap. The "+ Milestone"
    button on this screen creates a calendar EVENT via POST /calendar/events (D11/D13), so what
    it creates does not appear in this list.
    """
    rows = milestones_svc.list_milestones(db, startup_id=membership.startup_id)
    return success_response(
        {
            "milestones": [m.model_dump(mode="json") for m in rows],
            "summary": {
                "total": len(rows),
                "complete": sum(1 for m in rows if m.calendar_status == milestones_svc.COMPLETE),
            },
        }
    )


# ---------- meetings (PRD 19.3) ---------------------------------------------------------


def _meeting_out(db: Session, ev: Any, meeting: Any, role: MembershipRole) -> dict[str, Any]:
    return meetings_svc.serialize_meeting(db, ev, meeting, manager=meetings_svc.can_manage(role))


@router.post("/meetings", response_model=dict[str, Any])
def create_meeting(
    payload: MeetingCreate,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    ev = meetings_svc.create_meeting(db, startup_id=membership.startup_id, user=user, data=payload)
    calendar_svc.queue_pushes(db, membership.startup_id)
    db.commit()
    ev, meeting = meetings_svc.get_meeting_event(
        db, startup_id=membership.startup_id, event_id=ev.id, user=user, role=membership.role
    )
    return success_response(_meeting_out(db, ev, meeting, membership.role))


@router.get("/meetings", response_model=dict[str, Any])
def list_meetings(
    when: Literal["upcoming", "past", "all"] = "all",
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Founders and team members: every visible meeting. Any other role: only meetings it is
    invited to (D-CAL-6)."""
    rows = meetings_svc.list_meetings(
        db, startup_id=membership.startup_id, user=user, role=membership.role, when=when
    )
    return success_response(
        {"meetings": [_meeting_out(db, e, m, membership.role) for e, m in rows]}
    )


@router.get("/meetings/{event_id}", response_model=dict[str, Any])
def get_meeting(
    event_id: uuid.UUID,
    membership: Membership = Depends(_member),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    ev, meeting = meetings_svc.get_meeting_event(
        db, startup_id=membership.startup_id, event_id=event_id, user=user, role=membership.role
    )
    return success_response(_meeting_out(db, ev, meeting, membership.role))


@router.patch("/meetings/{event_id}", response_model=dict[str, Any])
def update_meeting(
    event_id: uuid.UUID,
    payload: MeetingUpdate,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    ev = meetings_svc.update_meeting(
        db,
        startup_id=membership.startup_id,
        user=user,
        role=membership.role,
        event_id=event_id,
        data=payload,
    )
    calendar_svc.queue_pushes(db, membership.startup_id)
    db.commit()
    ev, meeting = meetings_svc.get_meeting_event(
        db, startup_id=membership.startup_id, event_id=ev.id, user=user, role=membership.role
    )
    return success_response(_meeting_out(db, ev, meeting, membership.role))


@router.delete("/meetings/{event_id}", response_model=dict[str, Any])
def delete_meeting(
    event_id: uuid.UUID,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    meetings_svc.get_meeting_event(
        db, startup_id=membership.startup_id, event_id=event_id, user=user, role=membership.role
    )
    calendar_svc.delete_event(db, startup_id=membership.startup_id, event_id=event_id, viewer=user)
    db.commit()
    return success_response({"deleted": True})


@router.post("/meetings/{event_id}/assistant", status_code=202, response_model=dict[str, Any])
def run_assistant(
    event_id: uuid.UUID,
    payload: AssistantIn,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """D-CAL-1: paste notes or a transcript; poll `GET /meetings/{id}` until
    `assistant_state` is `ready` (or `failed`)."""
    meeting = meetings_svc.request_summary(
        db,
        startup_id=membership.startup_id,
        user=user,
        role=membership.role,
        event_id=event_id,
        transcript=payload.transcript,
    )
    db.commit()
    return success_response({"assistant_state": meeting.assistant_state})


@router.patch("/meetings/{event_id}/action-items/{item_id}", response_model=dict[str, Any])
def patch_action_item(
    event_id: uuid.UUID,
    item_id: str,
    payload: ActionItemPatch,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    meeting = meetings_svc.patch_action_item(
        db,
        startup_id=membership.startup_id,
        user=user,
        role=membership.role,
        event_id=event_id,
        item_id=item_id,
        data=payload,
    )
    db.commit()
    return success_response({"action_items": meeting.action_items})


@router.post("/meetings/{event_id}/action-items/add-to-tasks", response_model=dict[str, Any])
def add_action_items_to_tasks(
    event_id: uuid.UUID,
    payload: AddToTasksIn,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    result = meetings_svc.add_to_tasks(
        db,
        startup_id=membership.startup_id,
        user=user,
        role=membership.role,
        event_id=event_id,
        item_ids=payload.item_ids,
        milestone_id=payload.milestone_id,
    )
    db.commit()
    return success_response(result)


# ---------- reminder rules (PRD 19.4) ---------------------------------------------------


@router.get("/reminder-rules", response_model=dict[str, Any])
def list_reminder_rules(
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(
        {
            "rules": reminders_svc.serialize_rules(db, membership.startup_id),
            "can_edit": membership.role == MembershipRole.founder,
        }
    )


@router.put("/reminder-rules/{kind}", response_model=dict[str, Any])
def put_reminder_rule(
    kind: str,
    payload: ReminderRuleIn,
    membership: Membership = Depends(_founder),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    reminders_svc.set_rule(
        db, startup_id=membership.startup_id, kind=kind, offsets=payload.offsets_minutes
    )
    db.commit()
    return success_response(
        {"rules": reminders_svc.serialize_rules(db, membership.startup_id), "can_edit": True}
    )


@router.delete("/reminder-rules/{kind}", response_model=dict[str, Any])
def reset_reminder_rule(
    kind: str,
    membership: Membership = Depends(_founder),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Back to the PRD default offsets."""
    reminders_svc.reset_rule(db, startup_id=membership.startup_id, kind=kind)
    db.commit()
    return success_response(
        {"rules": reminders_svc.serialize_rules(db, membership.startup_id), "can_edit": True}
    )


# ---------- external calendar sync (PRD 19.5) -------------------------------------------


@router.get("/sync", response_model=dict[str, Any])
def sync_status(
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    return success_response(conn_svc.status(db, user_id=user.id))


@router.post("/sync/{provider}/connect", response_model=dict[str, Any])
def sync_connect(
    provider: str,
    response: Response,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Returns the Google consent URL; the FE navigates the browser to it."""
    result, proof = conn_svc.start_connect(
        db, user_id=user.id, startup_id=membership.startup_id, provider=provider
    )
    db.commit()
    # Ties the consent to THIS browser: the callback is public, so without it an attacker could
    # hand their connect URL to a victim and attach the victim's Google account to their user.
    response.set_cookie(
        OAUTH_COOKIE,
        proof,
        max_age=conn_svc.STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite="lax",
        path=OAUTH_COOKIE_PATH,
    )
    return success_response(result)


@router.get("/sync/{provider}/callback", include_in_schema=False)
def sync_callback(
    provider: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    cfz_cal_oauth: str | None = Cookie(default=None),
    db: Session = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Public: Google redirects the browser here. Identity comes from the signed `state` and the
    single-use nonce, never from a session. Always ends in a redirect to the FE sync page."""
    conn_svc.check_provider(provider)
    failure = conn_svc.complete_callback(
        db, code=code, state=state, error=error, browser_proof=cfz_cal_oauth
    )
    db.commit()
    base = (settings.APP_BASE_URL or settings.SERVER_HOST).rstrip("/")
    query = f"error={failure}" if failure else "connected=google"
    redirect = RedirectResponse(f"{base}/calendar/sync?{query}", status_code=302)
    redirect.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return redirect


@router.patch("/sync/{provider}", response_model=dict[str, Any])
def sync_update(
    provider: str,
    payload: SyncSettingsIn,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    conn_svc.check_provider(provider)
    conn = conn_svc.update_settings(
        db, user_id=user.id, direction=payload.direction, calendar_id=payload.calendar_id
    )
    db.commit()
    return success_response(conn_svc.serialize(conn))


@router.get("/sync/{provider}/calendars", response_model=dict[str, Any])
def sync_calendars(
    provider: str,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    conn_svc.check_provider(provider)
    conn = conn_svc.get_connection(db, user_id=user.id)
    if conn is None or conn.status not in ("active", "needs_attention"):
        raise NotFound()
    items = conn_svc.list_calendars(db, conn)
    db.commit()
    return success_response({"calendars": items, "selected": conn.calendar_id})


@router.post("/sync/{provider}/sync", response_model=dict[str, Any])
def sync_now(
    provider: str,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """ "Sync now": one cycle inline. A second click inside 10 seconds is a no-op."""
    conn_svc.check_provider(provider)
    conn = conn_svc.get_connection(db, user_id=user.id)
    if conn is None or conn.status not in ("active", "needs_attention"):
        raise NotFound()
    recent = (
        conn.last_sync_at is not None and datetime.now(UTC) - conn.last_sync_at < SYNC_MIN_INTERVAL
    )
    summary = {"skipped": True} if recent else sync_svc.sync_connection(db, conn.id)
    db.commit()
    db.refresh(conn)
    return success_response({"summary": summary, "connection": conn_svc.serialize(conn)})


@router.delete("/sync/{provider}", response_model=dict[str, Any])
def sync_disconnect(
    provider: str,
    membership: Membership = Depends(_calendar),  # noqa: B008
    user: User = Depends(get_verified_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    conn_svc.check_provider(provider)
    result = conn_svc.disconnect(db, user_id=user.id, startup_id=membership.startup_id)
    db.commit()
    return success_response(result)
