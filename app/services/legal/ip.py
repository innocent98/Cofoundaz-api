import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.enums import IpAssetStatus, IpAssetType
from app.db.models.legal import IpAsset
from app.schemas.legal import IpAssetCreate, IpAssetUpdate

EXPIRED = "expired"


def derived_ip_status(status: IpAssetStatus, renewal_on: date | None, today: date) -> str:
    """The badge the FE shows: the raw status, or `expired` once the renewal date has passed.

    `expired` is derived, never stored (spec D9). The FE table displays it, but its add drawer
    offers only drafting / filed / registered — so it was never a status anyone chooses, it is a
    consequence of `renewal_on` passing.

    A renewal due *today* has not passed, so it is not yet expired. `<` not `<=`.
    """
    if renewal_on is not None and renewal_on < today:
        return EXPIRED
    return status.value


def serialize_asset(row: IpAsset, *, today: date) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": row.type.value,
        "name": row.name,
        "jurisdiction": row.jurisdiction,
        "status": row.status.value,
        "derived_status": derived_ip_status(row.status, row.renewal_on, today),
        # ISO, never the mock's "Renews Feb 2027" / "No renewal" / "Renew now".
        "renewal_on": row.renewal_on.isoformat() if row.renewal_on else None,
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def create_asset(
    db: Session, *, startup_id: uuid.UUID, created_by: uuid.UUID | None, data: IpAssetCreate
) -> IpAsset:
    row = IpAsset(startup_id=startup_id, created_by=created_by, **data.model_dump())
    db.add(row)
    db.flush()
    return row


def get_asset(db: Session, *, startup_id: uuid.UUID, asset_id: uuid.UUID) -> IpAsset:
    row = db.query(IpAsset).filter_by(id=asset_id, startup_id=startup_id).one_or_none()
    if row is None:
        # Uniform 404, never 403: a workspace must not be able to detect another's row ids.
        raise NotFound()
    return row


def list_assets(
    db: Session,
    *,
    startup_id: uuid.UUID,
    type: IpAssetType | None = None,
    status: IpAssetStatus | None = None,
) -> list[IpAsset]:
    """The register, newest first.

    Filters are on the **stored** status, not the derived one: filtering on `expired` would need
    the same date arithmetic in SQL and in Python, and the two would drift.
    """
    q = db.query(IpAsset).filter_by(startup_id=startup_id)
    if type is not None:
        q = q.filter(IpAsset.type == type)
    if status is not None:
        q = q.filter(IpAsset.status == status)
    return q.order_by(IpAsset.created_at.desc(), IpAsset.id.desc()).all()


def update_asset(
    db: Session, *, startup_id: uuid.UUID, asset_id: uuid.UUID, data: IpAssetUpdate
) -> IpAsset:
    row = get_asset(db, startup_id=startup_id, asset_id=asset_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.flush()
    return row


def delete_asset(db: Session, *, startup_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    row = get_asset(db, startup_id=startup_id, asset_id=asset_id)
    db.delete(row)
    db.flush()
