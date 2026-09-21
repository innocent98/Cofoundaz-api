from app.db.models.document import Document
from app.db.models.enums import DocumentKind, DocumentStatus
from tests.factories import create_startup, create_user


def test_document_defaults(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    doc = Document(startup_id=s.id, created_by_id=u.id, title="Plan")
    db.add(doc)
    db.flush()
    db.refresh(doc)
    assert doc.kind == DocumentKind.custom
    assert doc.status == DocumentStatus.draft
    assert doc.ai_generated is False
    assert doc.sections == []
    assert doc.version == 1


def test_document_stores_sections(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    doc = Document(
        startup_id=s.id,
        created_by_id=u.id,
        title="Plan",
        sections=[{"id": "s1", "heading": "Problem", "body": "text"}],
    )
    db.add(doc)
    db.flush()
    db.refresh(doc)
    assert doc.sections[0]["heading"] == "Problem"
