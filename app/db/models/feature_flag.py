import uuid

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDMixin
from app.db.models.enums import FlagStage


class FeatureFlag(UUIDMixin, TimestampMixin, Base):
    """A platform-wide switch, evaluated per workspace (D-ADM-13).

    A workspace gets the feature when the flag is `enabled` and either its id is in
    `workspace_allowlist` or its stable bucket (0-99) is below `rollout_pct`. Not tenant data: no
    `startup_id`.
    """

    __tablename__ = "feature_flags"
    __table_args__ = (
        CheckConstraint("rollout_pct BETWEEN 0 AND 100", name="ck_feature_flags_rollout_pct"),
    )

    key: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    stage: Mapped[FlagStage] = mapped_column(
        Enum(FlagStage, native_enum=False, length=20), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rollout_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    workspace_allowlist: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list, server_default=text("'{}'")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
