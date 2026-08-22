from app.db.models.enums import StartupStage, TaskEffort
from app.services.roadmap.gallery import (
    GALLERY_TEMPLATE_VERSION,
    GALLERY_TEMPLATES,
    template_counts,
)


def test_gallery_is_well_formed():
    assert GALLERY_TEMPLATE_VERSION >= 1
    assert len(GALLERY_TEMPLATES) >= 6
    stages = {s.value for s in StartupStage}
    efforts = {e.value for e in TaskEffort}
    for tid, tmpl in GALLERY_TEMPLATES.items():
        assert tmpl["id"] == tid
        assert tmpl["title"] and tmpl["category"]
        assert tmpl["stage"] is None or tmpl["stage"] in stages
        assert tmpl["phases"]
        for ph in tmpl["phases"]:
            assert ph["end_week"] >= ph["start_week"]
            assert ph["milestones"]
            for ms in ph["milestones"]:
                assert ms["due_week"] >= 0
                for tk in ms["tasks"]:
                    assert tk["effort"] in efforts


def test_template_counts():
    mc, tc = template_counts(GALLERY_TEMPLATES["mvp-build"])
    assert mc >= 1 and tc >= 1
