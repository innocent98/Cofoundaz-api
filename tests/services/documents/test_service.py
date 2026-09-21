import uuid

import pytest

from app.core.errors import AppError, DocumentVersionConflict, NotFound
from app.db.models.enums import DocumentKind, DocumentStatus
from app.services.documents.service import (
    create_document,
    delete_document,
    get_document,
    list_documents,
    serialize_document,
    serialize_summary,
    update_document,
    validate_sections,
)
from tests.factories import create_membership, create_startup, create_user


def _ctx(db):
    u = create_user(db)
    s = create_startup(db, owner=u)
    m = create_membership(db, u, s)
    db.flush()
    return u, s, m


def _new(db, s, u, **kw):
    kw.setdefault("kind", DocumentKind.custom)
    kw.setdefault("title", "Doc")
    kw.setdefault("sections", [])
    kw.setdefault("folder", None)
    kw.setdefault("template_key", None)
    return create_document(db, s, created_by_id=u.id, **kw)


def test_validate_sections_assigns_ids_and_rejects_bad_shape(db):
    clean = validate_sections([{"heading": "H", "body": "B"}])
    assert clean[0]["heading"] == "H" and clean[0]["id"]
    with pytest.raises(AppError) as e:
        validate_sections([{"heading": 1, "body": "B"}])
    assert e.value.http_status == 422
    with pytest.raises(AppError) as e2:
        validate_sections(["not-an-object"])
    assert e2.value.http_status == 422


def test_create_blank(db):
    u, s, _m = _ctx(db)
    doc = _new(db, s, u, title="Blank")
    assert doc.title == "Blank" and doc.kind == DocumentKind.custom
    assert doc.ai_generated is False and doc.version == 1


def test_create_from_template_key_seeds_sections(db):
    u, s, _m = _ctx(db)
    from app.services.documents.template_defs import instantiate

    kind, title, sections = instantiate("business_plan")
    doc = _new(db, s, u, kind=kind, title=title, sections=sections, template_key="business_plan")
    assert doc.kind == DocumentKind.business_plan
    assert [x["heading"] for x in doc.sections][0] == "Executive Summary"


def test_create_seam_ai_generated(db):
    u, s, _m = _ctx(db)
    doc = _new(db, s, u, kind=DocumentKind.business_plan, ai_generated=True)
    assert doc.ai_generated is True


def test_list_filters_and_summary_has_no_sections(db):
    u, s, _m = _ctx(db)
    _new(
        db, s, u, kind=DocumentKind.one_pager, folder="A", sections=[{"heading": "H", "body": "B"}]
    )
    _new(db, s, u, kind=DocumentKind.pitch_deck, folder="B")
    assert len(list_documents(db, s, kind=DocumentKind.one_pager, folder=None, status=None)) == 1
    assert len(list_documents(db, s, kind=None, folder="B", status=None)) == 1
    assert "sections" not in serialize_summary(
        list_documents(db, s, kind=DocumentKind.one_pager, folder=None, status=None)[0]
    )


def test_get_cross_tenant_404(db):
    u, s, m = _ctx(db)
    with pytest.raises(NotFound):
        get_document(db, m, uuid.uuid4())


def test_update_full_replace_and_version_bump(db):
    u, s, m = _ctx(db)
    doc = _new(db, s, u, title="v1", sections=[{"heading": "Old", "body": "x"}])
    updated = update_document(
        db,
        doc,
        title="v2",
        sections=[{"heading": "New", "body": "y"}],
        status=DocumentStatus.final,
        folder="F",
        expected_version=1,
    )
    assert updated.title == "v2" and updated.version == 2
    assert updated.status == DocumentStatus.final
    assert [x["heading"] for x in updated.sections] == ["New"]


def test_update_stale_version_409(db):
    u, s, m = _ctx(db)
    doc = _new(db, s, u)
    with pytest.raises(DocumentVersionConflict):
        update_document(
            db,
            doc,
            title="x",
            sections=[],
            status=DocumentStatus.draft,
            folder=None,
            expected_version=99,
        )


def test_delete_removes(db):
    u, s, m = _ctx(db)
    doc = _new(db, s, u)
    delete_document(db, doc)
    with pytest.raises(NotFound):
        get_document(db, m, doc.id)


def test_serialize_document_has_sections(db):
    u, s, _m = _ctx(db)
    doc = _new(db, s, u, sections=[{"heading": "H", "body": "B"}])
    out = serialize_document(doc)
    assert out["sections"][0]["heading"] == "H" and out["version"] == 1


def test_create_publishes_event_once(db, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.services.documents.service.event_bus.publish",
        lambda db, e, p: events.append((e, p)),
    )
    u, s, _m = _ctx(db)
    doc = _new(db, s, u)
    assert events == [
        (
            "document.created",
            {"startup_id": str(s.id), "document_id": str(doc.id), "kind": doc.kind.value},
        )
    ]
