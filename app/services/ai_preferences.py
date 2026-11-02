"""AI preferences (PRD 03.5): the toggles and the tone, and the single place their effects read from.

Per member per workspace. Defaults apply until a member saves: suggestions on, briefing on, tone
"encouraging". What each setting controls (keep in sync with the FE guide):

- `daily_briefing` off: that member's dashboard shows the briefing blocks as "off" and never triggers
  generation; the `dashboard.briefing.ready` notification skips them.
- `proactive_suggestions` off: the dashboard briefing's suggested next steps (`actions`) and
  `opportunities` are hidden for them, and the `learning.course.recommended` notification skips them.
- `tone`: one fixed sentence (never free text) added to the system prompt of their AI chat replies and,
  for the shared daily briefing, of the workspace founder's choice.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.ai_preference import AiPreference
from app.db.models.enums import MembershipRole, MembershipStatus
from app.db.models.membership import Membership

TONES: tuple[str, ...] = ("straight_shooter", "encouraging")
DEFAULT_TONE = "encouraging"

_TONE_INSTRUCTION = {
    "straight_shooter": (
        "Voice: a straight shooter. Lead with the hard truth, be blunt and brief, skip "
        "reassurance and softeners, and say plainly what is not working."
    ),
    "encouraging": (
        "Voice: encouraging. Acknowledge what is going well first, frame problems as next steps, "
        "and stay honest and specific."
    ),
}


@dataclass(frozen=True)
class AiPrefs:
    proactive_suggestions: bool = True
    daily_briefing: bool = True
    tone: str = DEFAULT_TONE


def tone_instruction(tone: str | None) -> str:
    """The fixed prompt sentence for a tone; unknown values fall back to the default voice."""
    return _TONE_INSTRUCTION.get(tone or DEFAULT_TONE, _TONE_INSTRUCTION[DEFAULT_TONE])


def _row(db: Session, user_id: uuid.UUID, startup_id: uuid.UUID) -> AiPreference | None:
    return db.query(AiPreference).filter_by(user_id=user_id, startup_id=startup_id).first()


def get_prefs(db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID) -> AiPrefs:
    row = _row(db, user_id, startup_id)
    if row is None:
        return AiPrefs()
    return AiPrefs(row.proactive_suggestions, row.daily_briefing, row.tone)


def update_prefs(
    db: Session, *, user_id: uuid.UUID, startup_id: uuid.UUID, changes: dict[str, Any]
) -> AiPrefs:
    row = _row(db, user_id, startup_id)
    if row is None:
        row = AiPreference(user_id=user_id, startup_id=startup_id)
        db.add(row)
    for field, value in changes.items():
        setattr(row, field, value)
    db.flush()
    return get_prefs(db, user_id=user_id, startup_id=startup_id)


def opted_out(
    db: Session, startup_id: uuid.UUID, field: str, user_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Members among `user_ids` who switched `field` off in this workspace."""
    if not user_ids:
        return set()
    column = getattr(AiPreference, field)
    rows = (
        db.query(AiPreference.user_id)
        .filter(
            AiPreference.startup_id == startup_id,
            AiPreference.user_id.in_(user_ids),
            column.is_(False),
        )
        .all()
    )
    return {r[0] for r in rows}


def founder_tone(db: Session, startup_id: uuid.UUID) -> str:
    """The tone for text shared by the whole workspace (the daily briefing): the earliest active
    founder's choice."""
    founder = (
        db.query(Membership.user_id)
        .filter(
            Membership.startup_id == startup_id,
            Membership.role == MembershipRole.founder,
            Membership.status == MembershipStatus.active,
        )
        .order_by(Membership.created_at, Membership.id)
        .first()
    )
    if founder is None:
        return DEFAULT_TONE
    return get_prefs(db, user_id=founder[0], startup_id=startup_id).tone
