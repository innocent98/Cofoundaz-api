from app.db.models.enums import StartupStage, TaskEffort
from app.services.roadmap.templates import ROADMAP_TEMPLATE_VERSION, STAGE_TEMPLATES


def test_every_stage_has_a_template():
    for stage in StartupStage:
        assert stage.value in STAGE_TEMPLATES, stage.value


def test_templates_are_well_formed():
    assert ROADMAP_TEMPLATE_VERSION >= 1
    valid_efforts = {e.value for e in TaskEffort}
    for stage, tmpl in STAGE_TEMPLATES.items():
        assert tmpl["key"].startswith("stage.")
        assert tmpl["phases"], stage
        for ph in tmpl["phases"]:
            assert ph["end_week"] >= ph["start_week"]
            assert ph["milestones"], (stage, ph["name"])
            for ms in ph["milestones"]:
                assert ms["due_week"] >= 0
                for tk in ms["tasks"]:
                    assert tk["effort"] in valid_efforts
