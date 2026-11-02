"""Minimal RFC 5545 builder for meeting invites (REQUEST) and cancellations (CANCEL).

Times are written in UTC so no VTIMEZONE block is needed. One VEVENT, stable UID per event,
SEQUENCE bumped on every update so mail clients replace the earlier invite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.db.models.calendar import CalendarEvent

PRODID = "-//Cofoundaz//Calendar//EN"
DEFAULT_MINUTES = 60


def _esc(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> str:
    """Fold at 75 octets without splitting a UTF-8 sequence."""
    out: list[str] = []
    cur = b""
    for ch in line:
        enc = ch.encode()
        limit = 75 if not out else 74  # continuation lines lose one octet to the leading space
        if len(cur) + len(enc) > limit:
            out.append(cur.decode())
            cur = b""
        cur += enc
    out.append(cur.decode())
    return "\r\n ".join(out)


def _clean(value: str) -> str:
    """A header value must never carry a line break (property injection)."""
    return value.replace("\r", " ").replace("\n", " ")


def utc_range(ev: CalendarEvent | SimpleNamespace) -> tuple[datetime, datetime]:
    zone = ZoneInfo(ev.timezone)
    start_t = ev.start_time or time(9, 0)
    start = datetime.combine(ev.event_date, start_t, tzinfo=zone)
    end_day = ev.end_date or ev.event_date
    end = (
        datetime.combine(end_day, ev.end_time, tzinfo=zone)
        if ev.end_time
        else start + timedelta(minutes=DEFAULT_MINUTES)
    )
    return start.astimezone(UTC), end.astimezone(UTC)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    ev: CalendarEvent | SimpleNamespace,
    *,
    method: str,
    sequence: int,
    organizer_email: str,
    organizer_name: str,
    attendee_email: str,
    attendee_name: str | None = None,
    now: datetime | None = None,
) -> bytes:
    start, end = utc_range(ev)
    desc = ev.description or ""
    if ev.video_url:
        desc = (desc + "\n" if desc else "") + ev.video_url
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{ev.id}@cofoundaz.com",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_stamp(now or datetime.now(UTC))}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{_esc(_clean(ev.title))}",
        f"STATUS:{'CANCELLED' if method == 'CANCEL' else 'CONFIRMED'}",
        f"ORGANIZER;CN={_esc(_clean(organizer_name))}:mailto:{_clean(organizer_email)}",
        f"ATTENDEE;CN={_esc(_clean(attendee_name or attendee_email))};RSVP=TRUE:"
        f"mailto:{_clean(attendee_email)}",
    ]
    if desc:
        lines.append(f"DESCRIPTION:{_esc(desc)}")
    if ev.location:
        lines.append(f"LOCATION:{_esc(_clean(ev.location))}")
    if ev.video_url:
        lines.append(f"URL:{_clean(ev.video_url)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(_fold(ln) for ln in lines) + "\r\n").encode()


def day_label(d: date) -> str:
    return d.strftime("%a %d %b %Y")
