from app.db.models.enums import MarketingGenerationKind
from app.db.models.marketing import BrandPositioning, SeoKeyword, TrackedPage
from tests.factories import create_startup, create_user


def test_content_gap_kind_exists():
    assert MarketingGenerationKind.content_gap.value == "content_gap"


def test_seo_keyword_persists(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    row = SeoKeyword(
        startup_id=s.id,
        keyword="automated daily savings",
        volume="2.4K",
        difficulty=45,
        current_rank=12,
        target_page="/features/daily-saving",
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.volume == "2.4K" and row.difficulty == 45 and row.current_rank == 12


def test_tracked_page_and_positioning_persist(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    page = TrackedPage(startup_id=s.id, url="/pricing", checklist={"h1": True})
    pos = BrandPositioning(
        startup_id=s.id,
        audience="gig workers",
        need="save on irregular income",
        product="Kolo",
        category="savings app",
        differentiator="saves automatically",
        statement="For gig workers who save on irregular income, Kolo is the savings app that saves automatically.",
    )
    db.add_all([page, pos])
    db.flush()
    db.refresh(page)
    db.refresh(pos)
    assert page.checklist == {"h1": True}
    assert pos.statement.startswith("For gig workers")
