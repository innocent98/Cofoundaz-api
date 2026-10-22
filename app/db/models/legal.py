import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import ComplianceStatus, IpAssetStatus, IpAssetType, Recurrence


def _enum(enum_cls: type, length: int) -> Enum:
    """Store the enum VALUE, matching the migration labels and the lowercase strings the FE uses."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )


class LegalFormationProgress(UUIDMixin, TimestampMixin, Base):
    """One row per completed formation step, per jurisdiction.

    The catalog of steps lives in code (`app/services/legal/catalog.py`); only progress is
    stored. A row's existence IS the tick (spec D7) — there is no nullable completed flag that
    could disagree with `completed_at`, and unticking deletes the row.

    Keying by jurisdiction is deliberate (D8): switching jurisdiction does not wipe ticks,
    because the other jurisdiction's rows are simply not read. The FE prototype's
    reset-on-switch is a mock shortcut.
    """

    __tablename__ = "legal_formation_progress"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=False)
    step_key: Mapped[str] = mapped_column(String(60), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # SET NULL, not CASCADE: the tick belongs to the workspace, not to whoever clicked it.
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "startup_id",
            "jurisdiction",
            "step_key",
            name="uq_legal_formation_progress_step",
        ),
    )


class IpAsset(UUIDMixin, TimestampMixin, Base):
    """A trademark, patent, copyright or domain, with an optional renewal date.

    `status` holds three values only; `expired` is derived from `renewal_on` against today
    (spec D9), because the FE's add drawer cannot produce it.
    """

    __tablename__ = "ip_assets"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[IpAssetType] = mapped_column(_enum(IpAssetType, 20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # A free string, not a closed set of codes: it has to hold "Global" and "EU" as well as a
    # country. Separate vocabulary from the formation jurisdictions (spec D10).
    jurisdiction: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[IpAssetStatus] = mapped_column(_enum(IpAssetStatus, 20), nullable=False)
    renewal_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class ComplianceItem(UUIDMixin, TimestampMixin, Base):
    """A filing with a due date, an optional proof of filing, and optional recurrence.

    Stored status is only `open | done`; `upcoming | due_soon | overdue` are derived from
    `due_on` against today (spec D12), so the badge, the overdue count and the reminders cannot
    disagree.
    """

    __tablename__ = "compliance_items"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The FE calls this `filing`; stated in the integration guide.
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Nullable (spec D15): no create form exists yet to confirm it is always supplied, and
    # requiring a field the FE may omit is the wrong risk on a build-ahead shape.
    authority: Mapped[str | None] = mapped_column(String(120), nullable=True)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ComplianceStatus] = mapped_column(
        _enum(ComplianceStatus, 10),
        nullable=False,
        default=ComplianceStatus.open,
        server_default=ComplianceStatus.open.value,
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proof_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    proof_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence: Mapped[Recurrence] = mapped_column(
        _enum(Recurrence, 12),
        nullable=False,
        default=Recurrence.none,
        server_default=Recurrence.none.value,
    )
    # The series' anchor day-of-month, so occurrences cannot drift (spec D16/D24). Set from
    # due_on.day on create, reset whenever a PATCH moves due_on, and never client-settable.
    anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    # Unique, so a second spawn for the same predecessor is a no-op rather than a duplicate
    # occurrence (spec D16). SET NULL so a successor survives its predecessor's deletion.
    previous_item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("compliance_items.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (Index("ix_compliance_items_startup_id_due_on", "startup_id", "due_on"),)
