import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
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
from app.db.models.enums import (
    AdvisorRequestStatus,
    ComplianceStatus,
    IpAssetStatus,
    IpAssetType,
    Recurrence,
)


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


class AdvisorRequest(UUIDMixin, TimestampMixin, Base):
    """A founder asking a legal advisor to review one document (PRD 13.6, gap rows 41 to 47).

    The attachment is a rich `Document` OR an uploaded `DocumentFile`, never both, which is how
    `document_shares` already solves the same problem (D-LEG-16). CASCADE on both: a request to
    review a deleted document means nothing, and SET NULL would leave the CHECK with two empty
    columns. Documents soft-deletes, so a hard delete is rare.

    Access to the content is NOT a grant and NOT a share. It is this row, read through
    `GET /legal/advisor-requests/{id}/document`, which answers the named advisor (while their
    `legal_advisor` membership is live) and the workspace's writers, and 404s for anyone else, a
    foreign workspace, or a cancelled request (D-LEG-16, lead conditions 2026-11-19).
    """

    __tablename__ = "advisor_requests"

    startup_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("startups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_files.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # SET NULL, like `support_tickets.assigned_to`: the request survives the account, and the
    # notification's recipient function already tells nobody when there is no advisor.
    advisor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    # The note to the advisor (row 43). Optional: the subject plus the document may be enough.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AdvisorRequestStatus] = mapped_column(
        _enum(AdvisorRequestStatus, 12),
        nullable=False,
        default=AdvisorRequestStatus.requested,
        server_default=AdvisorRequestStatus.requested.value,
    )
    # The reviewed draft (row 45). One column covers both attachment kinds: since an advisor can
    # author no `DocumentVersion` (ShareAccess has no edit level and every document write route
    # is founder + team member), a returned draft is always a new file. A second upload moves
    # this pointer; earlier drafts stay in the Documents library, so nothing is lost.
    returned_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        # Named `target`, not `ck_advisor_requests_target`: the metadata naming convention is
        # `ck_%(table_name)s_%(constraint_name)s`, so this comes out as
        # `ck_advisor_requests_target`. `document_shares` declares its full name here and so
        # carries a doubled one in its metadata, which its migration had to work around.
        CheckConstraint(
            "(document_id IS NOT NULL) <> (file_id IS NOT NULL)",
            name="target",
        ),
    )
