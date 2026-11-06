"""One lock for the money-shaped parts of Funding in a workspace."""

import uuid

from sqlalchemy.orm import Session

from app.db.models.startup import Startup


def lock_workspace(db: Session, startup_id: uuid.UUID) -> None:
    """`SELECT ... FOR UPDATE` on the workspace row. Cap table writes and round creation or
    currency changes take it, so they serialise instead of racing (an entry inserted while the
    round currency flips would otherwise be silently relabelled)."""
    db.query(Startup.id).filter(Startup.id == startup_id).with_for_update().one()
