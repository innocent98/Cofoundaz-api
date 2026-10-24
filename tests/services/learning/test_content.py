from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models.enums import ReviewStatus, StartupStage
from app.db.models.learning import (
    AcademyArticle,
    AcademyCourse,
    AcademyLesson,
    AcademyPath,
    Enrollment,
)
from app.services.learning.content import (
    ContentError,
    load_content,
    parse_youtube_id,
    sync_content,
    video_payload,
)
from tests.factories import create_startup, create_user

VIDEO = "dQw4w9WgXcQ"


# --- the shipped content ----------------------------------------------------------------


def test_shipped_content_is_valid_and_covers_every_stage():
    bundle = load_content()
    assert 6 <= len(bundle.courses) <= 10
    assert len(bundle.articles) >= 10
    covered = {stage for course in bundle.courses for stage in course.stage_tags}
    assert covered == {s.value for s in StartupStage}
    assert {p.stage for p in bundle.paths} == {s.value for s in StartupStage}


def test_every_item_ships_as_a_draft_with_no_video_and_no_placeholder_text():
    bundle = load_content()
    for course in bundle.courses:
        assert course.review_status == ReviewStatus.draft
        for lesson in course.lessons:
            assert lesson.review_status == ReviewStatus.draft
            assert lesson.video_id is None  # no videos exist yet (D-ACA-2)
            assert len(lesson.transcript.split()) > 100
        assert "placeholder" not in (course.title + course.description).lower()
    for article in bundle.articles:
        assert article.review_status == ReviewStatus.draft
        assert len(article.body.split()) > 150
        assert "[placeholder]" not in article.title.lower()


def test_shipped_content_has_no_em_or_en_dashes():
    for path in Path("content/academy").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text, path


def test_lesson_ids_are_unique_and_paths_reference_real_courses():
    bundle = load_content()
    lesson_ids = [lesson.id for c in bundle.courses for lesson in c.lessons]
    assert len(lesson_ids) == len(set(lesson_ids))
    known = {c.id for c in bundle.courses}
    for path in bundle.paths:
        assert path.course_ids and set(path.course_ids) <= known


# --- validation -------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


COURSE_MD = """---
slug: tiny-course
title: Tiny course
summary: A tiny course
level: beginner
stage_tags: [idea]
gap_tags: [product]
order: 1
review_status: draft
outcomes:
  - Learn a thing
---
Description.
"""
LESSON_MD = """---
slug: tiny-course-1
title: First
order: 1
duration: 5
video:
review_status: draft
---
Body text.
"""


def _tiny(root: Path) -> None:
    _write(root, "courses/tiny-course/course.md", COURSE_MD)
    _write(root, "courses/tiny-course/01-first.md", LESSON_MD)


def test_a_minimal_tree_loads(tmp_path):
    _tiny(tmp_path)
    bundle = load_content(tmp_path)
    assert [c.id for c in bundle.courses] == ["tiny-course"]
    assert bundle.courses[0].lessons[0].video_id is None


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("review_status: draft\n---\nBody", "---\nBody", "review_status"),
        ("video:", "video: https://example.com/x", "video"),
        ("video:", "video: not-an-id", "video"),
        ("duration: 5", "duration: 0", "duration"),
        ("slug: tiny-course-1", "slug: Bad Slug", "slug"),
        ("Body text.", "Body — text.", "dash"),
        ("Body text.", "[Placeholder] text", "placeholder"),
        ("Body text.", "", "empty"),
    ],
)
def test_bad_lessons_are_reported(tmp_path, old, new, needle):
    _write(tmp_path, "courses/tiny-course/course.md", COURSE_MD)
    _write(tmp_path, "courses/tiny-course/01-first.md", LESSON_MD.replace(old, new))
    with pytest.raises(ContentError) as exc:
        load_content(tmp_path)
    assert needle in str(exc.value)


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ("level: beginner", "level: expert", "level"),
        ("stage_tags: [idea]", "stage_tags: [moon]", "stage_tags"),
        ("gap_tags: [product]", "gap_tags: [vibes]", "gap_tags"),
        ("slug: tiny-course", "slug: other-name", "folder name"),
        ("outcomes:\n  - Learn a thing\n", "", "outcomes"),
    ],
)
def test_bad_courses_are_reported(tmp_path, old, new, needle):
    _write(tmp_path, "courses/tiny-course/course.md", COURSE_MD.replace(old, new))
    _write(tmp_path, "courses/tiny-course/01-first.md", LESSON_MD)
    with pytest.raises(ContentError) as exc:
        load_content(tmp_path)
    assert needle in str(exc.value)


def test_missing_front_matter_and_missing_lessons_are_reported(tmp_path):
    _write(tmp_path, "courses/tiny-course/course.md", COURSE_MD)
    with pytest.raises(ContentError) as exc:
        load_content(tmp_path)
    assert "at least one lesson" in str(exc.value)
    _write(tmp_path, "courses/tiny-course/01-first.md", "no front matter here")
    with pytest.raises(ContentError) as exc2:
        load_content(tmp_path)
    assert "front-matter" in str(exc2.value)


def test_a_path_with_an_unknown_course_and_duplicate_slugs_are_reported(tmp_path):
    _tiny(tmp_path)
    _write(
        tmp_path,
        "paths/p.md",
        "---\nslug: p\ntitle: P\nstage: idea\norder: 1\ncourses: [ghost]\nreview_status: draft\n---\nSummary.\n",
    )
    with pytest.raises(ContentError) as exc:
        load_content(tmp_path)
    assert "unknown course" in str(exc.value)


def test_a_reviewed_course_with_a_draft_lesson_stays_a_draft(tmp_path):
    _write(
        tmp_path,
        "courses/tiny-course/course.md",
        COURSE_MD.replace("review_status: draft", "review_status: reviewed"),
    )
    _write(tmp_path, "courses/tiny-course/01-first.md", LESSON_MD)
    assert load_content(tmp_path).courses[0].review_status == ReviewStatus.draft


def test_a_lesson_video_url_is_stored_as_its_id(tmp_path):
    _write(tmp_path, "courses/tiny-course/course.md", COURSE_MD)
    _write(
        tmp_path,
        "courses/tiny-course/01-first.md",
        LESSON_MD.replace("video:", f"video: https://youtu.be/{VIDEO}"),
    )
    assert load_content(tmp_path).courses[0].lessons[0].video_id == VIDEO


# --- YouTube ids ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        VIDEO,
        f"https://youtu.be/{VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO}&t=42s",
        f"https://m.youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com/embed/{VIDEO}",
        f"https://www.youtube-nocookie.com/embed/{VIDEO}",
        f"https://www.youtube.com/shorts/{VIDEO}",
        f"  {VIDEO}  ",
    ],
)
def test_youtube_forms_resolve_to_the_eleven_character_id(value):
    assert parse_youtube_id(value) == VIDEO


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_video_is_none(value):
    assert parse_youtube_id(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "short",
        VIDEO + "x",
        "placeholder://course/1",
        f"http://youtu.be/{VIDEO}",  # not https
        f"https://evil.example.com/watch?v={VIDEO}",
        f"https://youtube.com.evil.example/watch?v={VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO[:-1]}",
        f"https://www.youtube.com/playlist?list={VIDEO}",
        f"https://youtu.be/{VIDEO}/extra",
        "javascript:alert(1)",
        "dQw4w9WgXc!",
    ],
)
def test_anything_else_is_rejected(value):
    with pytest.raises(ValueError, match="YouTube"):
        parse_youtube_id(value)


def test_video_payload_shape():
    assert video_payload(VIDEO) == {
        "provider": "youtube",
        "id": VIDEO,
        "embed_url": f"https://www.youtube-nocookie.com/embed/{VIDEO}",
    }
    assert video_payload(None) is None
    assert video_payload("bad") is None


# --- sync -------------------------------------------------------------------------------


def test_sync_loads_everything_by_slug(db: Session):
    bundle = load_content()
    counts = sync_content(db, bundle)
    assert counts["courses"] == db.query(AcademyCourse).count() == len(bundle.courses)
    assert db.query(AcademyLesson).count() == counts["lessons"]
    assert db.query(AcademyPath).count() == len(bundle.paths)
    assert db.query(AcademyArticle).count() == len(bundle.articles)


def test_sync_is_idempotent(db: Session):
    bundle = load_content()
    sync_content(db, bundle)
    stamps = {c.id: c.updated_at for c in db.query(AcademyCourse)}
    again = sync_content(db, bundle)
    assert again["pruned"] == 0
    assert db.query(AcademyCourse).count() == len(bundle.courses)
    assert db.query(AcademyLesson).count() == sum(len(c.lessons) for c in bundle.courses)
    assert {c.id: c.updated_at for c in db.query(AcademyCourse)} == stamps


def test_sync_updates_changes_reorders_lessons_and_prunes(tmp_path, db: Session):
    _write(tmp_path, "courses/tiny-course/course.md", COURSE_MD)
    _write(tmp_path, "courses/tiny-course/01-first.md", LESSON_MD)
    second = (
        LESSON_MD.replace("tiny-course-1", "tiny-course-2")
        .replace("First", "Second")
        .replace("order: 1", "order: 2")
    )
    _write(tmp_path, "courses/tiny-course/02-second.md", second)
    _write(
        tmp_path,
        "articles/art-one.md",
        "---\nslug: art-one\ntitle: A\nsummary: S\ntags: [t]\norder: 1\nreview_status: draft\n---\nWords.\n",
    )
    sync_content(db, load_content(tmp_path))
    assert [lesson.id for lesson in db.get(AcademyCourse, "tiny-course").lessons] == [
        "tiny-course-1",
        "tiny-course-2",
    ]

    # swap the order, retitle, attach a video, drop the article
    _write(
        tmp_path,
        "courses/tiny-course/01-first.md",
        LESSON_MD.replace("order: 1", "order: 2").replace("video:", f"video: {VIDEO}"),
    )
    _write(tmp_path, "courses/tiny-course/02-second.md", second.replace("order: 2", "order: 1"))
    (tmp_path / "articles/art-one.md").unlink()
    counts = sync_content(db, load_content(tmp_path))
    db.expire_all()
    lessons = db.get(AcademyCourse, "tiny-course").lessons
    assert [lesson.id for lesson in lessons] == ["tiny-course-2", "tiny-course-1"]
    assert lessons[1].video_id == VIDEO
    assert db.get(AcademyArticle, "art-one") is None
    assert counts["pruned"] >= 1

    # a lesson removed from the files is removed from the course
    (tmp_path / "courses/tiny-course/02-second.md").unlink()
    sync_content(db, load_content(tmp_path))
    db.expire_all()
    assert [lesson.id for lesson in db.get(AcademyCourse, "tiny-course").lessons] == [
        "tiny-course-1"
    ]


def test_a_course_with_enrolments_survives_being_removed_from_the_files(tmp_path, db: Session):
    _tiny(tmp_path)
    sync_content(db, load_content(tmp_path))
    user = create_user(db)
    startup = create_startup(db, owner=user)
    db.add(Enrollment(startup_id=startup.id, user_id=user.id, course_id="tiny-course"))
    db.flush()
    (tmp_path / "courses/tiny-course/01-first.md").unlink()
    (tmp_path / "courses/tiny-course/course.md").unlink()
    (tmp_path / "courses/tiny-course").rmdir()
    _write(
        tmp_path,
        "courses/other/course.md",
        COURSE_MD.replace("tiny-course", "other"),
    )
    _write(tmp_path, "courses/other/01-first.md", LESSON_MD.replace("tiny-course-1", "other-1"))
    sync_content(db, load_content(tmp_path))
    assert db.get(AcademyCourse, "tiny-course") is not None  # kept: learners reference it
    assert db.get(AcademyCourse, "other") is not None


def test_cli_main_reports_invalid_content(monkeypatch, capsys):
    from app.services.learning import content

    def boom(*_a, **_k):
        raise ContentError(["x.md: broken"])

    monkeypatch.setattr(content, "load_content", boom)
    assert content.main() == 1
    assert "x.md: broken" in capsys.readouterr().out
