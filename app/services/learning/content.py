"""Academy content loader (D-ACA-1): markdown in ``content/academy/`` -> the database, by slug.

Layout::

    content/academy/courses/<slug>/course.md        front-matter + the course description
    content/academy/courses/<slug>/NN-<name>.md      one lesson each; the body is the transcript
    content/academy/paths/<slug>.md                  an ordered list of course slugs
    content/academy/articles/<slug>.md               front-matter + the article body

``load_content`` parses and validates everything and raises ``ContentError`` listing every problem,
so a bad file fails the sync (and CI) rather than shipping. ``sync_content`` upserts the result
and is idempotent: running it twice changes nothing. The Module 25.4 CMS replaces the files later;
the read endpoints do not change.

Run it with ``python -m app.services.learning.content`` (the deploy ``migrate`` step does).
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from sqlalchemy.orm import Session

from app.db.models.enums import CourseLevel, ReviewStatus, StartupStage
from app.db.models.learning import (
    AcademyArticle,
    AcademyCourse,
    AcademyLesson,
    AcademyPath,
    Certificate,
    Enrollment,
)

CONTENT_DIR = Path(__file__).resolve().parents[3] / "content" / "academy"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,78}$")
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Vocabulary the recommender understands (assessment dimensions + validation signals).
GAP_TAGS = frozenset(
    {"product", "market", "money", "pricing", "legal", "team", "customers", "demand"}
)
_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com"}
)
_WORDS_PER_MINUTE = 200
# Built from code points so this file itself carries no em or en dash.
_DASHES = (chr(0x2014), chr(0x2013))


class ContentError(Exception):
    """One or more content files are invalid; ``problems`` lists every one."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


def parse_youtube_id(value: Any) -> str | None:
    """A YouTube URL or bare id -> the 11-character id; empty -> ``None``; anything else raises.

    Strict on purpose: only an 11-character ``[A-Za-z0-9_-]`` id is ever stored, and URLs are only
    accepted from the YouTube hosts below, so the embed URL built from the id cannot be steered.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if YOUTUBE_ID_RE.fullmatch(text):
        return text
    url = urlparse(text)
    host = (url.hostname or "").lower()
    candidate: str | None = None
    if url.scheme == "https" and host == "youtu.be":
        candidate = url.path.lstrip("/")
    elif url.scheme == "https" and host in _YOUTUBE_HOSTS:
        parts = [p for p in url.path.split("/") if p]
        if url.path == "/watch":
            candidate = (parse_qs(url.query).get("v") or [""])[0]
        elif len(parts) == 2 and parts[0] in {"embed", "shorts", "live"}:
            candidate = parts[1]
    if candidate is None or not YOUTUBE_ID_RE.fullmatch(candidate):
        raise ValueError(f"not a YouTube video URL or 11-character id: {text!r}")
    return candidate


def video_payload(video_id: str | None) -> dict[str, str] | None:
    """The API shape for a lesson's video, or ``None`` when the lesson has none."""
    if not video_id or not YOUTUBE_ID_RE.fullmatch(video_id):
        return None
    return {
        "provider": "youtube",
        "id": video_id,
        "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}",
    }


@dataclass
class LessonDoc:
    id: str
    title: str
    position: int
    duration_min: int
    transcript: str
    video_id: str | None
    review_status: ReviewStatus


@dataclass
class CourseDoc:
    id: str
    title: str
    summary: str
    description: str
    outcomes: list[str]
    level: CourseLevel
    stage_tags: list[str]
    gap_tags: list[str]
    position: int
    review_status: ReviewStatus
    lessons: list[LessonDoc] = field(default_factory=list)


@dataclass
class PathDoc:
    id: str
    title: str
    summary: str
    stage: str
    course_ids: list[str]
    position: int
    review_status: ReviewStatus


@dataclass
class ArticleDoc:
    id: str
    title: str
    summary: str
    body: str
    tags: list[str]
    read_min: int
    position: int
    review_status: ReviewStatus


@dataclass
class ContentBundle:
    courses: list[CourseDoc]
    paths: list[PathDoc]
    articles: list[ArticleDoc]


def _split_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", raw, re.DOTALL)
    if match is None:
        raise ValueError("missing front-matter block")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("front-matter must be a mapping")
    return meta, match.group(2).strip()


class _Reader:
    """Typed accessors over one file's front-matter that record problems instead of raising."""

    def __init__(self, path: Path, root: Path, problems: list[str]) -> None:
        self.label = str(path.relative_to(root))
        self.problems = problems

    def fail(self, message: str) -> None:
        self.problems.append(f"{self.label}: {message}")

    def text(self, meta: dict[str, Any], key: str) -> str:
        value = meta.get(key)
        if not isinstance(value, str) or not value.strip():
            self.fail(f"`{key}` is required (non-empty text)")
            return ""
        return value.strip()

    def number(self, meta: dict[str, Any], key: str, low: int, high: int) -> int:
        value = meta.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            self.fail(f"`{key}` must be a whole number from {low} to {high}")
            return low
        return value

    def strings(
        self, meta: dict[str, Any], key: str, *, allowed: frozenset[str] | None
    ) -> list[str]:
        value = meta.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            self.fail(f"`{key}` must be a non-empty list of text")
            return []
        out = [v.strip() for v in value]
        bad = [v for v in out if allowed is not None and v not in allowed]
        if bad:
            self.fail(f"`{key}` has unknown value(s) {bad}; allowed: {sorted(allowed or [])}")
        return out

    def slug(self, meta: dict[str, Any]) -> str:
        value = self.text(meta, "slug")
        if value and not SLUG_RE.fullmatch(value):
            self.fail(f"`slug` {value!r} must be lowercase letters, digits and hyphens")
        return value

    def review(self, meta: dict[str, Any]) -> ReviewStatus:
        value = meta.get("review_status")
        try:
            return ReviewStatus(str(value))
        except ValueError:
            self.fail("`review_status` is required (draft or reviewed)")
            return ReviewStatus.draft

    def video(self, meta: dict[str, Any]) -> str | None:
        try:
            return parse_youtube_id(meta.get("video"))
        except ValueError as exc:
            self.fail(f"`video`: {exc}")
            return None

    def clean_prose(self, *texts: str) -> None:
        for text in texts:
            if "[Placeholder]" in text or "placeholder://" in text:
                self.fail("placeholder text must not ship")
            if any(dash in text for dash in _DASHES):
                self.fail("contains an em or en dash; use a comma, colon or full stop")


def _load_lesson(path: Path, root: Path, problems: list[str]) -> LessonDoc | None:
    reader = _Reader(path, root, problems)
    try:
        meta, body = _split_front_matter(path)
    except (ValueError, yaml.YAMLError) as exc:
        reader.fail(str(exc))
        return None
    lesson = LessonDoc(
        id=reader.slug(meta),
        title=reader.text(meta, "title"),
        position=reader.number(meta, "order", 1, 100),
        duration_min=reader.number(meta, "duration", 1, 180),
        transcript=body,
        video_id=reader.video(meta),
        review_status=reader.review(meta),
    )
    if not body:
        reader.fail("the lesson body (its transcript) is empty")
    reader.clean_prose(lesson.title, body)
    return lesson


def _load_course(course_dir: Path, root: Path, problems: list[str]) -> CourseDoc | None:
    course_file = course_dir / "course.md"
    reader = _Reader(course_file, root, problems)
    if not course_file.exists():
        problems.append(f"{course_dir.relative_to(root)}: course.md is missing")
        return None
    try:
        meta, body = _split_front_matter(course_file)
    except (ValueError, yaml.YAMLError) as exc:
        reader.fail(str(exc))
        return None
    try:
        level = CourseLevel(str(meta.get("level")))
    except ValueError:
        reader.fail("`level` must be beginner, intermediate or advanced")
        level = CourseLevel.beginner
    course = CourseDoc(
        id=reader.slug(meta),
        title=reader.text(meta, "title"),
        summary=reader.text(meta, "summary"),
        description=body,
        outcomes=reader.strings(meta, "outcomes", allowed=None),
        level=level,
        stage_tags=reader.strings(
            meta, "stage_tags", allowed=frozenset(s.value for s in StartupStage)
        ),
        gap_tags=reader.strings(meta, "gap_tags", allowed=GAP_TAGS),
        position=reader.number(meta, "order", 1, 1000),
        review_status=reader.review(meta),
    )
    if course.id and course.id != course_dir.name:
        reader.fail(f"`slug` {course.id!r} must match the folder name {course_dir.name!r}")
    if not body:
        reader.fail("the course description (the file body) is empty")
    reader.clean_prose(course.title, course.summary, body, *course.outcomes)
    for lesson_file in sorted(p for p in course_dir.glob("*.md") if p.name != "course.md"):
        lesson = _load_lesson(lesson_file, root, problems)
        if lesson is not None:
            course.lessons.append(lesson)
    course.lessons.sort(key=lambda lesson: lesson.position)
    if not course.lessons:
        reader.fail("a course needs at least one lesson")
    positions = [lesson.position for lesson in course.lessons]
    if len(set(positions)) != len(positions):
        reader.fail("lesson `order` values must be unique within the course")
    if course.review_status == ReviewStatus.reviewed and any(
        lesson.review_status != ReviewStatus.reviewed for lesson in course.lessons
    ):
        course.review_status = ReviewStatus.draft  # reviewed only when every lesson is too
    return course


def _load_path(path: Path, root: Path, problems: list[str]) -> PathDoc | None:
    reader = _Reader(path, root, problems)
    try:
        meta, body = _split_front_matter(path)
    except (ValueError, yaml.YAMLError) as exc:
        reader.fail(str(exc))
        return None
    stage = reader.text(meta, "stage")
    if stage and stage not in {s.value for s in StartupStage}:
        reader.fail(f"`stage` {stage!r} is not a startup stage")
    doc = PathDoc(
        id=reader.slug(meta),
        title=reader.text(meta, "title"),
        summary=body or reader.text(meta, "summary"),
        stage=stage,
        course_ids=reader.strings(meta, "courses", allowed=None),
        position=reader.number(meta, "order", 1, 1000),
        review_status=reader.review(meta),
    )
    reader.clean_prose(doc.title, doc.summary)
    return doc


def _load_article(path: Path, root: Path, problems: list[str]) -> ArticleDoc | None:
    reader = _Reader(path, root, problems)
    try:
        meta, body = _split_front_matter(path)
    except (ValueError, yaml.YAMLError) as exc:
        reader.fail(str(exc))
        return None
    doc = ArticleDoc(
        id=reader.slug(meta),
        title=reader.text(meta, "title"),
        summary=reader.text(meta, "summary"),
        body=body,
        tags=reader.strings(meta, "tags", allowed=None),
        read_min=max(1, round(len(body.split()) / _WORDS_PER_MINUTE)),
        position=reader.number(meta, "order", 1, 1000),
        review_status=reader.review(meta),
    )
    if not body:
        reader.fail("the article body is empty")
    reader.clean_prose(doc.title, doc.summary, body)
    return doc


def _duplicates(label: str, ids: list[str], problems: list[str]) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            problems.append(f"duplicate {label} slug {item!r}")
        seen.add(item)


def load_content(root: Path | None = None) -> ContentBundle:
    """Parse and validate every file under ``root`` (default ``content/academy``)."""
    base = root or CONTENT_DIR
    problems: list[str] = []
    courses = [
        c
        for d in sorted((base / "courses").glob("*/"))
        if d.is_dir() and (c := _load_course(d, base, problems)) is not None
    ]
    paths = [
        p
        for f in sorted((base / "paths").glob("*.md"))
        if (p := _load_path(f, base, problems)) is not None
    ]
    articles = [
        a
        for f in sorted((base / "articles").glob("*.md"))
        if (a := _load_article(f, base, problems)) is not None
    ]
    _duplicates("course", [c.id for c in courses], problems)
    _duplicates("lesson", [lesson.id for c in courses for lesson in c.lessons], problems)
    _duplicates("path", [p.id for p in paths], problems)
    _duplicates("article", [a.id for a in articles], problems)
    known = {c.id for c in courses}
    for path_doc in paths:
        missing = [cid for cid in path_doc.course_ids if cid not in known]
        if missing:
            problems.append(f"path {path_doc.id!r} lists unknown course(s) {missing}")
    if problems:
        raise ContentError(problems)
    courses.sort(key=lambda c: c.position)
    paths.sort(key=lambda p: p.position)
    articles.sort(key=lambda a: a.position)
    return ContentBundle(courses=courses, paths=paths, articles=articles)


def _sync_lessons(db: Session, row: AcademyCourse, doc: CourseDoc) -> None:
    existing = {lesson.id: lesson for lesson in row.lessons}
    wanted = {lesson.id for lesson in doc.lessons}
    for lesson_id in set(existing) - wanted:
        row.lessons.remove(existing[lesson_id])
    db.flush()
    moved = [
        existing[lesson.id]
        for lesson in doc.lessons
        if lesson.id in existing and existing[lesson.id].position != lesson.position
    ]
    for lesson_row in moved:  # park moved rows so a reorder cannot trip the (course, position) key
        lesson_row.position = -lesson_row.position
    db.flush()
    for lesson in doc.lessons:
        target = existing.get(lesson.id)
        if target is None:
            target = AcademyLesson(id=lesson.id, course_id=row.id)
            row.lessons.append(target)
        target.title = lesson.title
        target.position = lesson.position
        target.duration_min = lesson.duration_min
        target.transcript = lesson.transcript
        target.video_id = lesson.video_id


def sync_content(db: Session, bundle: ContentBundle) -> dict[str, int]:
    """Upsert the bundle by slug and prune what the files no longer contain. No commit.

    A removed course that learners already enrolled in or earned a certificate for is kept, so
    their records never dangle.
    """
    for doc in bundle.courses:
        row = db.get(AcademyCourse, doc.id)
        if row is None:
            row = AcademyCourse(id=doc.id)
            db.add(row)
        row.title = doc.title
        row.summary = doc.summary
        row.description = doc.description
        row.outcomes = list(doc.outcomes)
        row.level = doc.level
        row.stage_tags = list(doc.stage_tags)
        row.gap_tags = list(doc.gap_tags)
        row.position = doc.position
        row.review_status = doc.review_status
        db.flush()
        _sync_lessons(db, row, doc)
    for path_doc in bundle.paths:
        path_row = db.get(AcademyPath, path_doc.id) or AcademyPath(id=path_doc.id)
        path_row.title = path_doc.title
        path_row.summary = path_doc.summary
        path_row.stage = path_doc.stage
        path_row.course_ids = list(path_doc.course_ids)
        path_row.position = path_doc.position
        path_row.review_status = path_doc.review_status
        db.add(path_row)
    for art in bundle.articles:
        art_row = db.get(AcademyArticle, art.id) or AcademyArticle(id=art.id)
        art_row.title = art.title
        art_row.summary = art.summary
        art_row.body = art.body
        art_row.tags = list(art.tags)
        art_row.read_min = art.read_min
        art_row.position = art.position
        art_row.review_status = art.review_status
        db.add(art_row)
    db.flush()
    pruned = _prune(db, bundle)
    db.flush()
    return {
        "courses": len(bundle.courses),
        "lessons": sum(len(c.lessons) for c in bundle.courses),
        "paths": len(bundle.paths),
        "articles": len(bundle.articles),
        "pruned": pruned,
    }


def _prune(db: Session, bundle: ContentBundle) -> int:
    pruned = 0
    course_ids = {c.id for c in bundle.courses}
    for course in db.query(AcademyCourse).filter(AcademyCourse.id.notin_(course_ids)).all():
        used = (
            db.query(Enrollment.id).filter_by(course_id=course.id).first() is not None
            or db.query(Certificate.id).filter_by(course_id=course.id).first() is not None
        )
        if not used:
            db.delete(course)
            pruned += 1
    path_ids = {p.id for p in bundle.paths}
    for path_row in db.query(AcademyPath).filter(AcademyPath.id.notin_(path_ids)).all():
        db.delete(path_row)
        pruned += 1
    article_ids = {a.id for a in bundle.articles}
    for art_row in db.query(AcademyArticle).filter(AcademyArticle.id.notin_(article_ids)).all():
        db.delete(art_row)
        pruned += 1
    return pruned


def main() -> int:
    """CLI entry: validate the files, upsert them, commit. Non-zero exit on invalid content."""
    from app.db.session import SessionLocal  # pylint: disable=import-outside-toplevel

    try:
        bundle = load_content()
    except ContentError as exc:
        print("Academy content is invalid:\n" + "\n".join(f"  - {p}" for p in exc.problems))
        return 1
    with SessionLocal() as db:
        counts = sync_content(db, bundle)
        db.commit()
    print(f"Academy content synced: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
