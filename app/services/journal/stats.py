"""Journal stats, mood annotations and pattern-only insights (PRD 21.3).

Privacy: this module reads entry METADATA only (date, mood, stress). It never decrypts content
and must not import `decrypt_content`, so insights cannot quote or interpret what was written
(D-JNL-3). Copy is non-clinical and pattern-only: no diagnosis, no advice framing.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.enums import JournalMood, RoadmapStatus
from app.db.models.journal import JournalEntry
from app.db.models.roadmap import Roadmap, RoadmapMilestone, RoadmapPhase
from app.services.journal.service import JournalService

MIN_INSIGHT_ENTRIES = 5
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
RANGES = {"4w": 28, "12w": 84, "6m": 183, "1y": 365}
SUPPORT_WINDOW_DAYS = 14
SUPPORT_MIN_ENTRIES = 7
SUPPORT_REPEAT_DAYS = 30


def _label(score: float) -> str:
    return JournalService.score_to_mood(min(5, max(1, round(score)))).value


def _rows(db: Session, startup_id: uuid.UUID, founder_id: uuid.UUID) -> list[tuple[date, int, int]]:
    """(date, mood score, stress) for every entry, oldest first. Metadata columns only."""
    return [
        (r[0], r[1], r[2])
        for r in db.query(JournalEntry.date, JournalEntry.mood, JournalEntry.stress)
        .filter(JournalEntry.startup_id == startup_id, JournalEntry.founder_id == founder_id)
        .order_by(JournalEntry.date.asc())
        .all()
    ]


def _streaks(days: set[date], today: date) -> tuple[int, int, date | None]:
    """(current streak, longest streak, longest streak's last day). The current streak is still
    alive when the last entry was yesterday (today may not be written yet)."""
    if not days:
        return 0, 0, None
    ordered = sorted(days)
    longest, run, best_end = 1, 1, ordered[0]
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        run = run + 1 if (cur - prev).days == 1 else 1
        if run > longest:
            longest, best_end = run, cur
    anchor = today if today in days else today - timedelta(days=1)
    current = 0
    while anchor in days:
        current += 1
        anchor -= timedelta(days=1)
    return current, longest, best_end


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def stats(
    db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID, today: date
) -> dict[str, Any]:
    rows = _rows(db, startup_id, founder_id)
    this_month = [r for r in rows if _month_key(r[0]) == _month_key(today)]
    prev_anchor = today.replace(day=1) - timedelta(days=1)
    prev_month = [r for r in rows if _month_key(r[0]) == _month_key(prev_anchor)]
    current, longest, longest_end = _streaks({r[0] for r in rows}, today)

    # Last 12 ISO-ish weeks (Monday start), oldest first, empty weeks included.
    this_monday = today - timedelta(days=today.weekday())
    weeks = []
    for i in range(11, -1, -1):
        start = this_monday - timedelta(weeks=i)
        inside = [r for r in rows if start <= r[0] < start + timedelta(days=7)]
        weeks.append(
            {
                "week_start": start.isoformat(),
                "entries": len(inside),
                "average_mood": round(mean(r[1] for r in inside), 2) if inside else None,
                "average_stress": round(mean(r[2] for r in inside), 2) if inside else None,
            }
        )
    four_weeks_ago = today - timedelta(days=28)
    recent = [r for r in rows if r[0] > four_weeks_ago]
    return {
        "total_entries": len(rows),
        "entries_this_month": len(this_month),
        "entries_per_week": round(len(recent) / 4, 1),
        "average_mood": _label(mean(r[1] for r in this_month)) if this_month else None,
        "average_mood_score": round(mean(r[1] for r in this_month), 2) if this_month else None,
        "previous_month_average_mood": (
            {"month": _month_key(prev_anchor), "mood": _label(mean(r[1] for r in prev_month))}
            if prev_month
            else None
        ),
        "current_streak_days": current,
        "longest_streak_days": longest,
        "longest_streak_ended_on": longest_end.isoformat() if longest_end else None,
        "wrote_today": any(r[0] == today for r in rows),
        "weeks": weeks,
    }


def months(db: Session, *, startup_id: uuid.UUID, founder_id: uuid.UUID) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for d, _m, _s in _rows(db, startup_id, founder_id):
        counts[_month_key(d)] += 1
    return [{"month": k, "count": counts[k]} for k in sorted(counts, reverse=True)]


def mood_annotations(
    db: Session, *, startup_id: uuid.UUID, start: date, end: date
) -> list[dict[str, Any]]:
    """Roadmap milestones completed inside the window ("Completed: Launch week")."""
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    rows = (
        db.query(RoadmapMilestone.title, RoadmapMilestone.updated_at)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(
            Roadmap.startup_id == startup_id,
            RoadmapMilestone.status == RoadmapStatus.done,
            RoadmapMilestone.updated_at >= start_dt,
            RoadmapMilestone.updated_at < end_dt,
        )
        .order_by(RoadmapMilestone.updated_at.asc())
        .limit(50)
        .all()
    )
    return [
        {"date": ts.astimezone(UTC).date(), "label": f"Completed: {title}"} for title, ts in rows
    ]


def _deadline_stress(
    db: Session, startup_id: uuid.UUID, rows: list[tuple[date, int, int]]
) -> tuple[float, int] | None:
    """Mean stress within 3 days of a milestone's due date (3+ such entries), else None."""
    dues = [
        r[0]
        for r in db.query(RoadmapMilestone.due_on)
        .join(RoadmapPhase, RoadmapMilestone.phase_id == RoadmapPhase.id)
        .join(Roadmap, RoadmapPhase.roadmap_id == Roadmap.id)
        .filter(Roadmap.startup_id == startup_id, RoadmapMilestone.due_on.isnot(None))
        .all()
    ]
    near = [r[2] for r in rows if any(abs((r[0] - d).days) <= 3 for d in dues)]
    return (mean(near), len(near)) if len(near) >= 3 else None


def insights(
    db: Session,
    *,
    startup_id: uuid.UUID,
    founder_id: uuid.UUID,
    today: date,
    supportive_dismissed_at: datetime | None,
) -> dict[str, Any]:
    rows = _rows(db, startup_id, founder_id)
    out: dict[str, Any] = {"insights": [], "supportive_card": None, "entries_needed": 0}

    window_start = today - timedelta(days=SUPPORT_WINDOW_DAYS - 1)
    window = [r for r in rows if r[0] >= window_start]
    recently_dismissed = supportive_dismissed_at is not None and supportive_dismissed_at > (
        datetime.now(UTC) - timedelta(days=SUPPORT_REPEAT_DAYS)
    )
    if (
        len(window) >= SUPPORT_MIN_ENTRIES
        and all(r[1] <= 2 for r in window)
        and not recently_dismissed
    ):
        out["supportive_card"] = {
            "title": "It looks like it's been heavy lately.",
            "body": (
                "Building is hard. Talking to someone you trust, or a professional, "
                "can genuinely help."
            ),
        }

    if len(rows) < MIN_INSIGHT_ENTRIES:
        out["entries_needed"] = MIN_INSIGHT_ENTRIES - len(rows)
        return out

    found: list[dict[str, str]] = []
    overall_stress = mean(r[2] for r in rows)

    by_day: dict[int, list[int]] = defaultdict(list)
    for d, _m, stress in rows:
        by_day[d.weekday()].append(stress)
    eligible = {k: mean(v) for k, v in by_day.items() if len(v) >= 3}
    if eligible:
        worst = max(eligible, key=lambda k: eligible[k])
        if eligible[worst] - overall_stress >= 1.5:
            found.append(
                {
                    "kind": "weekday_stress",
                    "title": f"Stress runs higher on {WEEKDAYS[worst]}s",
                    "body": f"Your stress ratings on {WEEKDAYS[worst]}s average "
                    f"{eligible[worst]:.1f}, against {overall_stress:.1f} overall.",
                }
            )

    deadline = _deadline_stress(db, startup_id, rows)
    if deadline and deadline[0] - overall_stress >= 1.5:
        found.append(
            {
                "kind": "deadline_stress",
                "title": "Stress tends to rise around milestone deadlines",
                "body": f"On days within three days of a due date your stress averages "
                f"{deadline[0]:.1f}, against {overall_stress:.1f} overall.",
            }
        )

    last = [r[1] for r in rows if r[0] > today - timedelta(days=14)]
    before = [r[1] for r in rows if today - timedelta(days=28) < r[0] <= today - timedelta(days=14)]
    if len(last) >= 4 and len(before) >= 4:
        delta = mean(last) - mean(before)
        if abs(delta) >= 0.5:
            found.append(
                {
                    "kind": "mood_shift",
                    "title": "Your mood is "
                    + ("up" if delta > 0 else "down")
                    + " on the last two weeks",
                    "body": f"The last 14 days average {_label(mean(last))}, "
                    f"the 14 before averaged {_label(mean(before))}.",
                }
            )

    current, longest, _end = _streaks({r[0] for r in rows}, today)
    if current >= 3:
        found.append(
            {
                "kind": "streak",
                "title": f"{current} days in a row",
                "body": f"You have written {current} days running. Your longest run is {longest}.",
            }
        )

    best_mood = JournalMood.great.value
    great = [r for r in rows if r[1] == 5]
    if len(great) >= 3:
        found.append(
            {
                "kind": "great_days",
                "title": "Your best days are on record",
                "body": f"{len(great)} entries are marked {best_mood.capitalize()}. "
                "Re-reading them is a good way to remember what a good week feels like.",
            }
        )
    out["insights"] = found
    return out
