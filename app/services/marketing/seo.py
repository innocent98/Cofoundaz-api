import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.marketing import SeoKeyword
from app.schemas.marketing import KeywordCreate, KeywordResponse, KeywordUpdate


def serialize_keyword(row: SeoKeyword) -> KeywordResponse:
    return KeywordResponse.model_validate(row, from_attributes=True)


def create_keyword(db: Session, *, startup_id: uuid.UUID, data: KeywordCreate) -> SeoKeyword:
    row = SeoKeyword(startup_id=startup_id, **data.model_dump())
    db.add(row)
    db.flush()
    return row


def get_keyword(db: Session, *, startup_id: uuid.UUID, keyword_id: uuid.UUID) -> SeoKeyword:
    row = db.query(SeoKeyword).filter_by(id=keyword_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def list_keywords(db: Session, *, startup_id: uuid.UUID) -> list[SeoKeyword]:
    return (
        db.query(SeoKeyword)
        .filter_by(startup_id=startup_id)
        .order_by(SeoKeyword.created_at.desc())
        .all()
    )


def update_keyword(
    db: Session, *, startup_id: uuid.UUID, keyword_id: uuid.UUID, data: KeywordUpdate
) -> SeoKeyword:
    row = get_keyword(db, startup_id=startup_id, keyword_id=keyword_id)
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    db.flush()
    return row


def delete_keyword(db: Session, *, startup_id: uuid.UUID, keyword_id: uuid.UUID) -> None:
    row = get_keyword(db, startup_id=startup_id, keyword_id=keyword_id)
    db.delete(row)
    db.flush()
