from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin


class ScheduledRun(UUIDMixin, TimestampMixin, Base):
    """Append-only ledger: 'this scheduled task fired for this scope in this period'.

    The unique (task_key, scope_key, period_key) is the whole once-per-period guard —
    the first worker to insert wins; a concurrent/repeat insert raises IntegrityError.
    scope_key is a generic string (a workspace id, or a milestone id for overdue), so
    there is no FK.
    """

    __tablename__ = "scheduled_runs"

    task_key: Mapped[str] = mapped_column(String(60), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period_key: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("task_key", "scope_key", "period_key", name="uq_scheduled_runs_task_scope_period"),
    )
