"""Pull a user's Google events (private to them) and push their Cofoundaz events (D-CAL-2).

Pull: events Google holds become `CalendarExternalEvent` rows owned by the connection's user;
teammates only ever see a busy block built from the times. Events WE pushed carry the private
extended property `cofoundazEventId` and are skipped on the way back, so nothing duplicates.
Push: events created in the Calendar module that the user created or was invited to.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.logger import log
from app.db.models.calendar import (
    CalendarAttendee,
    CalendarConnection,
    CalendarEvent,
    CalendarEventLink,
    CalendarExternalEvent,
)
from app.db.models.enums import MembershipStatus
from app.db.models.membership import Membership
from app.services.calendar import connections, google

PULL_PAST_DAYS = 30
PULL_FUTURE_DAYS = 365
PUSH_PAST_DAYS = 30
DEFAULT_MINUTES = 60


def _now() -> datetime:
    return datetime.now(UTC)


# ---------- Google <-> Cofoundaz shapes -------------------------------------------------


def event_body(row: CalendarEvent, description_suffix: str = "") -> dict[str, Any]:
    desc = (row.description or "") + description_suffix
    if row.video_url:
        desc = (desc + "\n" if desc else "") + row.video_url
    body: dict[str, Any] = {
        "summary": row.title,
        "description": desc or None,
        "location": row.location,
        "extendedProperties": {"private": {google.EXT_PROP: str(row.id)}},
    }
    end_day = row.end_date or row.event_date
    if row.all_day or row.start_time is None:
        # Google's all-day end is exclusive.
        body["start"] = {"date": row.event_date.isoformat()}
        body["end"] = {"date": (end_day + timedelta(days=1)).isoformat()}
    else:
        start = datetime.combine(row.event_date, row.start_time)
        end = (
            datetime.combine(end_day, row.end_time)
            if row.end_time
            else start + timedelta(minutes=DEFAULT_MINUTES)
        )
        body["start"] = {"dateTime": start.isoformat(), "timeZone": row.timezone}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": row.timezone}
    return body


def _parse_edge(edge: dict[str, Any]) -> tuple[datetime, bool]:
    if "date" in edge:
        d = date.fromisoformat(edge["date"])
        return datetime.combine(d, time.min, tzinfo=UTC), True
    raw = str(edge["dateTime"]).replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(str(edge.get("timeZone") or "UTC")))
    return dt.astimezone(UTC), False


def parse_external(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        starts, all_day = _parse_edge(item["start"])
        ends, _ = _parse_edge(item["end"])
    except (KeyError, ValueError):
        return None
    if ends < starts:
        ends = starts
    return {
        "external_id": str(item["id"])[:300],
        "etag": str(item.get("etag", ""))[:200] or None,
        "title": str(item.get("summary") or "(No title)")[:300],
        "starts_at": starts,
        "ends_at": ends,
        "all_day": all_day,
    }


def _is_ours(item: dict[str, Any]) -> bool:
    private = (item.get("extendedProperties") or {}).get("private") or {}
    return google.EXT_PROP in private


# ---------- pull ------------------------------------------------------------------------


def _apply_pull(
    db: Session, conn: CalendarConnection, items: list[dict[str, Any]], *, full: bool
) -> tuple[int, int]:
    existing = {
        r.external_id: r for r in db.query(CalendarExternalEvent).filter_by(connection_id=conn.id)
    }
    seen: set[str] = set()
    pulled = removed = 0
    for item in items:
        ext_id = str(item.get("id", ""))[:300]
        if not ext_id or _is_ours(item):
            continue
        if item.get("status") == "cancelled":
            row = existing.pop(ext_id, None)
            if row is not None:
                db.delete(row)
                removed += 1
            continue
        data = parse_external(item)
        if data is None:
            continue
        seen.add(ext_id)
        row = existing.get(ext_id)
        if row is None:
            db.add(CalendarExternalEvent(connection_id=conn.id, **data))
            pulled += 1
        elif row.etag != data["etag"]:
            for k, v in data.items():
                setattr(row, k, v)
            pulled += 1
    if full:
        # A full listing is the whole truth: anything not in it is gone from Google.
        for ext_id, row in existing.items():
            if ext_id not in seen:
                db.delete(row)
                removed += 1
    db.flush()
    return pulled, removed


def pull(db: Session, conn: CalendarConnection, access: str) -> tuple[int, int]:
    now = _now()
    time_min = (now - timedelta(days=PULL_PAST_DAYS)).isoformat()
    time_max = (now + timedelta(days=PULL_FUTURE_DAYS)).isoformat()
    full = conn.sync_token is None
    try:
        items, nxt = google.list_events(
            access,
            conn.calendar_id,
            sync_token=conn.sync_token,
            time_min=time_min,
            time_max=time_max,
        )
    except google.GoogleGone:
        full = True
        items, nxt = google.list_events(
            access, conn.calendar_id, sync_token=None, time_min=time_min, time_max=time_max
        )
    result = _apply_pull(db, conn, items, full=full)
    conn.sync_token = nxt
    return result


# ---------- push ------------------------------------------------------------------------


def _pushable(db: Session, conn: CalendarConnection) -> list[CalendarEvent]:
    cutoff = _now().date() - timedelta(days=PUSH_PAST_DAYS)
    invited = db.query(CalendarAttendee.event_id).filter(CalendarAttendee.user_id == conn.user_id)
    return (
        db.query(CalendarEvent)
        .filter(
            CalendarEvent.startup_id == conn.startup_id,
            CalendarEvent.event_date >= cutoff,
            or_(CalendarEvent.created_by == conn.user_id, CalendarEvent.id.in_(invited)),
            # A private event stays out of everyone else's calendar.
            or_(CalendarEvent.visibility == "team", CalendarEvent.created_by == conn.user_id),
        )
        .all()
    )


def push(db: Session, conn: CalendarConnection, access: str) -> int:
    links = {
        link.event_id: link for link in db.query(CalendarEventLink).filter_by(connection_id=conn.id)
    }
    pushed = 0
    pushable = _pushable(db, conn)
    keep = {ev.id for ev in pushable}
    for event_id, stale in list(links.items()):
        if event_id not in keep:
            # No longer shareable (went private, or this user was removed from the invite):
            # take the copy off their Google calendar, titles included.
            try:
                google.delete_event(access, conn.calendar_id, stale.external_id)
            except google.GoogleNotFound:
                log.info(f"[calendar] copy {stale.external_id} already gone from Google")
            db.delete(stale)
            del links[event_id]
    for ev in pushable:
        link = links.get(ev.id)
        body = event_body(ev)
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if link is not None and link.pushed_hash == digest:
            continue
        if link is None:
            res = google.insert_event(access, conn.calendar_id, body)
            db.add(
                CalendarEventLink(
                    event_id=ev.id,
                    connection_id=conn.id,
                    external_id=str(res["id"]),
                    etag=str(res.get("etag", "")) or None,
                    pushed_hash=digest,
                )
            )
        else:
            try:
                res = google.patch_event(access, conn.calendar_id, link.external_id, body)
            except (google.GoogleGone, google.GoogleNotFound):
                # The user deleted our copy in Google: recreate it rather than failing every cycle.
                res = google.insert_event(access, conn.calendar_id, body)
                link.external_id = str(res["id"])
            link.etag = str(res.get("etag", "")) or link.etag
            link.pushed_hash = digest
        pushed += 1
    db.flush()
    return pushed


def push_deletes(db: Session, connection_id: uuid.UUID, external_ids: list[str]) -> int:
    """Remove the Google copies of events deleted in Cofoundaz (enqueued by `delete_event`)."""
    conn = db.get(CalendarConnection, connection_id)
    if conn is None or conn.status != "active" or conn.direction == "pull_only":
        return 0
    try:
        access = connections.access_token(db, conn)
        for ext_id in external_ids:
            google.delete_event(access, conn.calendar_id, ext_id)
    except (google.GoogleAuthError, google.GoogleApiError) as exc:
        log.warning(f"[calendar] push delete failed for {connection_id}: {exc}")
        return 0
    return len(external_ids)


# ---------- orchestration ---------------------------------------------------------------


def sync_connection(db: Session, connection_id: uuid.UUID) -> dict[str, Any]:
    """One full cycle for one connection. Never raises for provider failures: they are recorded
    on the connection (`needs_attention` for a rejected token, `last_error` otherwise) so the
    worker does not retry-storm a dead grant."""
    conn = db.query(CalendarConnection).filter_by(id=connection_id).with_for_update().one_or_none()
    if conn is None or conn.status != "active":
        return {"skipped": True}
    summary: dict[str, Any] = {"pulled": 0, "removed": 0, "pushed": 0}
    still_member = (
        db.query(Membership.id)
        .filter_by(user_id=conn.user_id, startup_id=conn.startup_id, status=MembershipStatus.active)
        .first()
    )
    if still_member is None:
        # A removed member must stop reading from and writing to the workspace's calendar.
        connections.wipe(db, conn)
        return {"skipped": True, "disconnected": "not_a_member"}
    try:
        access = connections.access_token(db, conn)
        if conn.direction in ("two_way", "pull_only"):
            summary["pulled"], summary["removed"] = pull(db, conn, access)
        if conn.direction in ("two_way", "push_only"):
            summary["pushed"] = push(db, conn, access)
    except google.GoogleAuthError:
        connections.mark_needs_attention(conn)
        summary["error"] = "reconnect"
    except google.GoogleApiError as exc:
        conn.last_error = "Google could not be reached. We will retry shortly."
        summary["error"] = "provider"
        log.warning(f"[calendar] sync {connection_id} failed: {exc}")
    else:
        conn.last_error = None
        conn.last_sync_at = _now()
        conn.last_sync_summary = dict(summary)
    db.flush()
    return summary
