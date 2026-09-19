from app.db.models.enums import CanvasType
from app.services.business.plan_context import build_plan_context
from app.services.business.plan_defs import PLAN_SECTIONS
from app.services.business.plan_prompt import build_section_messages
from tests.factories import create_startup, create_user


def test_plan_sections_unique_and_nonempty():
    assert len(PLAN_SECTIONS) >= 8
    keys = [s.key for s in PLAN_SECTIONS]
    assert len(keys) == len(set(keys))
    assert all(s.heading and s.guidance for s in PLAN_SECTIONS)


def test_build_plan_context_gathers_business_data_no_pii(db):
    from app.db.models.business import BusinessCanvas

    u = create_user(db)
    s = create_startup(db, owner=u)  # confirm create_startup sets name/industry/stage
    db.add(BusinessCanvas(startup_id=s.id, type=CanvasType.business_model, blocks={"value_propositions": ["Fast"]}))
    db.flush()
    ctx = build_plan_context(db, s)
    blob = str(ctx)
    assert "Fast" in blob  # canvas content present
    assert "@" not in blob  # no emails/PII


def test_build_section_messages_includes_guidance_and_context():
    ctx = {
        "profile": {"name": "Acme", "industry": "Fintech", "stage": "validation"},
        "canvases": {},
        "records": {},
        "assessment": None,
        "roadmap": [],
    }
    msgs = build_section_messages(PLAN_SECTIONS[0], ctx)
    assert [m.role for m in msgs] == ["system", "user"]
    assert PLAN_SECTIONS[0].guidance[:12] in msgs[1].content or PLAN_SECTIONS[0].heading in msgs[1].content
    assert "Acme" in msgs[1].content
