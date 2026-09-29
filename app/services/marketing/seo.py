import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models.marketing import SeoKeyword, TrackedPage
from app.schemas.marketing import (
    KeywordCreate,
    KeywordResponse,
    KeywordUpdate,
    TrackedPageCreate,
    TrackedPageResponse,
    TrackedPageUpdate,
)
from app.services.marketing.service import _validation


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


ON_PAGE_ITEMS: tuple[str, ...] = (
    "title_tag",
    "meta_description",
    "h1",
    "keyword_in_intro",
    "image_alt",
    "internal_links",
    "url_slug",
    "mobile_friendly",
)


def _seed_checklist() -> dict[str, bool]:
    return dict.fromkeys(ON_PAGE_ITEMS, False)


def serialize_page(row: TrackedPage) -> TrackedPageResponse:
    checklist = {item: bool((row.checklist or {}).get(item, False)) for item in ON_PAGE_ITEMS}
    completed = sum(1 for v in checklist.values() if v)
    return TrackedPageResponse(
        id=row.id,
        url=row.url,
        checklist=checklist,
        completed=completed,
        total=len(ON_PAGE_ITEMS),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_page(db: Session, *, startup_id: uuid.UUID, data: TrackedPageCreate) -> TrackedPage:
    row = TrackedPage(startup_id=startup_id, url=data.url, checklist=_seed_checklist())
    try:
        # add() must be INSIDE the SAVEPOINT so a dup-url rollback discards the pending
        # row; otherwise it lingers in session.new and re-flushes (poisoning the session)
        # on the next query. Mirrors list_channels' seeding.
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise _validation("url", "A tracked page with this url already exists.") from exc
    return row


def get_page(db: Session, *, startup_id: uuid.UUID, page_id: uuid.UUID) -> TrackedPage:
    row = db.query(TrackedPage).filter_by(id=page_id, startup_id=startup_id).one_or_none()
    if row is None:
        raise NotFound()
    return row


def list_pages(db: Session, *, startup_id: uuid.UUID) -> list[TrackedPage]:
    return (
        db.query(TrackedPage)
        .filter_by(startup_id=startup_id)
        .order_by(TrackedPage.created_at.desc())
        .all()
    )


def update_page_checklist(
    db: Session, *, startup_id: uuid.UUID, page_id: uuid.UUID, data: TrackedPageUpdate
) -> TrackedPage:
    row = get_page(db, startup_id=startup_id, page_id=page_id)
    unknown = [k for k in data.checklist if k not in ON_PAGE_ITEMS]
    if unknown:
        raise _validation("checklist", f"Unknown checklist item(s): {', '.join(sorted(unknown))}.")
    merged = {item: bool((row.checklist or {}).get(item, False)) for item in ON_PAGE_ITEMS}
    merged.update({k: bool(v) for k, v in data.checklist.items()})
    row.checklist = merged
    db.flush()
    return row


def delete_page(db: Session, *, startup_id: uuid.UUID, page_id: uuid.UUID) -> None:
    row = get_page(db, startup_id=startup_id, page_id=page_id)
    db.delete(row)
    db.flush()
