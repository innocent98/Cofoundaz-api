import uuid
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import SoftDeleteMixin, TimestampMixin, UUIDMixin


class _Sample(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "sample_mixin_rows"
    label: Mapped[str] = mapped_column()


def test_mixins_populate_defaults(db):
    row = _Sample(label="x")
    db.add(row)
    db.flush()
    db.refresh(row)
    assert isinstance(row.id, uuid.UUID)
    assert isinstance(row.created_at, datetime)
    assert isinstance(row.updated_at, datetime)
    assert row.deleted_at is None
