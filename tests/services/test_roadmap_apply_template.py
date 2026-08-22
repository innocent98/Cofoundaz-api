from app.db.models.roadmap import RoadmapPhase
from app.services.roadmap.gallery import GALLERY_TEMPLATES, template_counts
from app.services.roadmap.service import apply_template
from tests.factories import create_roadmap, create_startup, create_user


def test_apply_appends_pack(db):
    startup = create_startup(db, owner=create_user(db))
    roadmap = create_roadmap(db, startup)
    before = db.query(RoadmapPhase).filter_by(roadmap_id=roadmap.id).count()
    tmpl = GALLERY_TEMPLATES["mvp-build"]

    added = apply_template(db, roadmap, tmpl)
    db.flush()

    mc, tc = template_counts(tmpl)
    assert added == {"phases": len(tmpl["phases"]), "milestones": mc, "tasks": tc}
    assert db.query(RoadmapPhase).filter_by(roadmap_id=roadmap.id).count() == before + len(
        tmpl["phases"]
    )
    assert "mvp-build" in roadmap.applied_template_keys
