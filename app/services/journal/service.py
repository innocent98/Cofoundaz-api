from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, Forbidden, NotFound
from app.db.models.enums import (
    EnrichmentStatus,
    JournalMood,
    MembershipRole,
    MembershipStatus,
)
from app.db.models.journal import JournalEntry, JournalPrompt, MoodLog
from app.db.models.membership import Membership
from app.platform.jobs import job_dispatcher
from app.schemas.journal import JournalEntryCreate, JournalEntryUpdate
from app.services.journal.encryption import (
    decrypt_content,
    encrypt_content,
)


class JournalService:
    """Founder-only journal service.

    Journal content is encrypted at rest.
    Access is restricted to an active founder membership
    for the requested startup.
    """

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @staticmethod
    def check_founder_access(
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
    ) -> None:
        membership = (
            db.query(Membership)
            .filter(
                Membership.user_id == user_id,
                Membership.startup_id == startup_id,
                Membership.status == MembershipStatus.active,
                Membership.role == MembershipRole.founder,
            )
            .first()
        )

        if membership is None:
            raise Forbidden()

    # ------------------------------------------------------------------
    # Mood mapping
    # ------------------------------------------------------------------

    @staticmethod
    def mood_to_score(mood: JournalMood) -> int:
        """Convert the five PRD moods to their persisted integer values."""
        mapping = {
            JournalMood.rough: 1,
            JournalMood.meh: 2,
            JournalMood.okay: 3,
            JournalMood.good: 4,
            JournalMood.great: 5,
        }

        return mapping[mood]

    @staticmethod
    def score_to_mood(score: int) -> JournalMood:
        """Convert persisted mood score back to the API enum."""
        mapping = {
            1: JournalMood.rough,
            2: JournalMood.meh,
            3: JournalMood.okay,
            4: JournalMood.good,
            5: JournalMood.great,
        }

        if score not in mapping:
            raise ValueError(f"Invalid journal mood score: {score}")

        return mapping[score]

    # ------------------------------------------------------------------
    # Entry lookup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_entry(
        db: Session,
        *,
        entry_id: uuid.UUID,
        startup_id: uuid.UUID,
        founder_id: uuid.UUID,
    ) -> JournalEntry:
        entry = (
            db.query(JournalEntry)
            .filter(
                JournalEntry.id == entry_id,
                JournalEntry.startup_id == startup_id,
                JournalEntry.founder_id == founder_id,
            )
            .first()
        )
        if entry is None:
            raise NotFound()

        return entry

    @staticmethod
    def _get_entry_by_date(
        db: Session,
        *,
        startup_id: uuid.UUID,
        founder_id: uuid.UUID,
        entry_date: date,
    ) -> JournalEntry | None:
        return (
            db.query(JournalEntry)
            .filter(
                JournalEntry.startup_id == startup_id,
                JournalEntry.founder_id == founder_id,
                JournalEntry.date == entry_date,
            )
            .first()
        )

    # ------------------------------------------------------------------
    # Mood log
    # ------------------------------------------------------------------

    @classmethod
    def _upsert_mood_log(
        cls,
        db: Session,
        *,
        startup_id: uuid.UUID,
        founder_id: uuid.UUID,
        entry_date: date,
        mood: JournalMood,
        stress: int,
    ) -> MoodLog:
        mood_score = cls.mood_to_score(mood)

        insert_stmt = pg_insert(MoodLog).values(
            id=uuid.uuid4(),
            startup_id=startup_id,
            founder_id=founder_id,
            date=entry_date,
            mood=mood_score,
            stress=stress,
        )

        return db.execute(
            insert_stmt.on_conflict_do_update(
                constraint="uq_mood_logs_startup_founder_date",
                set_={
                    "mood": insert_stmt.excluded.mood,
                    "stress": insert_stmt.excluded.stress,
                    "updated_at": func.now(),
                },
            ).returning(MoodLog)
        ).scalar_one()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @classmethod
    def create_entry(
        cls,
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        data: JournalEntryCreate,
    ) -> JournalEntry:
        cls.check_founder_access(
            db,
            user_id=user_id,
            startup_id=startup_id,
        )

        insert_stmt = pg_insert(JournalEntry).values(
            id=uuid.uuid4(),
            startup_id=startup_id,
            founder_id=user_id,
            date=data.date,
            content_encrypted=encrypt_content(data.content),
            mood=cls.mood_to_score(data.mood),
            stress=data.stress,
        )

        entry = db.execute(
            insert_stmt.on_conflict_do_update(
                constraint="uq_journal_entries_startup_founder_date",
                set_={
                    "content_encrypted": insert_stmt.excluded.content_encrypted,
                    "mood": insert_stmt.excluded.mood,
                    "stress": insert_stmt.excluded.stress,
                    "updated_at": func.now(),
                },
            ).returning(JournalEntry)
        ).scalar_one()

        cls._upsert_mood_log(
            db,
            startup_id=startup_id,
            founder_id=user_id,
            entry_date=data.date,
            mood=data.mood,
            stress=data.stress,
        )

        db.flush()

        return entry

    # ------------------------------------------------------------------
    # Read one
    # ------------------------------------------------------------------

    @classmethod
    def get_entry(
        cls,
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        entry_id: uuid.UUID,
    ) -> JournalEntry:
        cls.check_founder_access(
            db,
            user_id=user_id,
            startup_id=startup_id,
        )

        return cls._get_entry(
            db,
            entry_id=entry_id,
            startup_id=startup_id,
            founder_id=user_id,
        )

    @staticmethod
    def get_entry_content(entry: JournalEntry) -> str:
        """Decrypt journal content only when explicitly requested."""
        return decrypt_content(entry.content_encrypted)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    @classmethod
    def update_entry(
        cls,
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        entry_id: uuid.UUID,
        data: JournalEntryUpdate,
    ) -> JournalEntry:
        cls.check_founder_access(
            db,
            user_id=user_id,
            startup_id=startup_id,
        )

        entry = cls._get_entry(
            db,
            entry_id=entry_id,
            startup_id=startup_id,
            founder_id=user_id,
        )

        update_data = data.model_dump(exclude_unset=True)

        if "content" in update_data:
            if update_data["content"] is None:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Content cannot be null.",
                    422,
                    field_errors=[{"field": "content", "message": "Omit the field instead."}],
                )

            entry.content_encrypted = encrypt_content(update_data["content"])

        if "mood" in update_data:
            if update_data["mood"] is None:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Mood cannot be null.",
                    422,
                    field_errors=[{"field": "mood", "message": "Omit the field instead."}],
                )

            entry.mood = cls.mood_to_score(update_data["mood"])

        if "stress" in update_data:
            if update_data["stress"] is None:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Content cannot be null.",
                    422,
                    field_errors=[{"field": "content", "message": "Omit the field instead."}],
                )

            entry.stress = update_data["stress"]

        mood = cls.score_to_mood(entry.mood)

        cls._upsert_mood_log(
            db,
            startup_id=startup_id,
            founder_id=user_id,
            entry_date=entry.date,
            mood=mood,
            stress=entry.stress,
        )

        db.flush()

        return entry

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    @classmethod
    def delete_entry(
        cls,
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        entry_id: uuid.UUID,
    ) -> None:
        cls.check_founder_access(
            db,
            user_id=user_id,
            startup_id=startup_id,
        )

        entry = cls._get_entry(
            db,
            entry_id=entry_id,
            startup_id=startup_id,
            founder_id=user_id,
        )

        mood_log = (
            db.query(MoodLog)
            .filter(
                MoodLog.startup_id == startup_id,
                MoodLog.founder_id == user_id,
                MoodLog.date == entry.date,
            )
            .first()
        )

        if mood_log is not None:
            db.delete(mood_log)

        db.delete(entry)
        db.flush()

    # ------------------------------------------------------------------
    # List entries
    # ------------------------------------------------------------------

    @classmethod
    def list_entries(
        cls,
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JournalEntry], int]:
        cls.check_founder_access(
            db,
            user_id=user_id,
            startup_id=startup_id,
        )

        base_query = db.query(JournalEntry).filter(
            JournalEntry.startup_id == startup_id,
            JournalEntry.founder_id == user_id,
        )

        total = base_query.count()

        entries = (
            base_query.order_by(
                JournalEntry.date.desc(),
                JournalEntry.created_at.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )

        return entries, total

    # ------------------------------------------------------------------
    # Search own entries
    # ------------------------------------------------------------------

    @classmethod
    def search_entries(
        cls,
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JournalEntry], int]:
        """Search metadata belonging to the founder's own entries.

        Encrypted content cannot be searched directly in the database.
        Therefore search is performed after fetching the founder's own
        encrypted entries and decrypting them in application memory.
        """

        cls.check_founder_access(
            db,
            user_id=user_id,
            startup_id=startup_id,
        )

        entries = (
            db.query(JournalEntry)
            .filter(
                JournalEntry.startup_id == startup_id,
                JournalEntry.founder_id == user_id,
            )
            .order_by(JournalEntry.date.desc())
            .all()
        )

        needle = query.strip().lower()

        if not needle:
            return entries[offset : offset + limit], len(entries)

        matches: list[JournalEntry] = []

        for entry in entries:
            content = decrypt_content(entry.content_encrypted)

            if needle in content.lower():
                matches.append(entry)

        total = len(matches)

        return matches[offset : offset + limit], total

    # ------------------------------------------------------------------
    # Mood trends
    # ------------------------------------------------------------------

    @classmethod
    def get_mood_trend(
        cls,
        db: Session,
        *,
        user_id: uuid.UUID,
        startup_id: uuid.UUID,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 90,
    ) -> list[MoodLog]:
        cls.check_founder_access(
            db,
            user_id=user_id,
            startup_id=startup_id,
        )

        query = db.query(MoodLog).filter(
            MoodLog.startup_id == startup_id,
            MoodLog.founder_id == user_id,
        )

        if start_date is not None:
            query = query.filter(MoodLog.date >= start_date)

        if end_date is not None:
            query = query.filter(MoodLog.date <= end_date)

        return query.order_by(MoodLog.date.asc()).limit(limit).all()

    # ------------------------------------------------------------------
    # Today's prompt
    # ------------------------------------------------------------------

    @staticmethod
    def get_prompt(
        *,
        today: date | None = None,
    ) -> str:
        """Return a deterministic rotating daily prompt.

        This keeps the prompt endpoint functional without requiring
        another database table. Context-aware milestone prompts can be
        added later when milestone data is wired into the journal service.
        """

        current_date = today or date.today()

        prompts = (
            "What moved forward today, and what surprised you about it?",
            "What felt heavier than expected today?",
            "What is one decision you made today that matters?",
            "What went well today that you do not want to overlook?",
            "What would you change if you could replay today?",
            "What gave you energy today?",
            "What is the most important thing on your mind right now?",
        )

        return prompts[current_date.toordinal() % len(prompts)]

    @staticmethod
    def get_or_create_today_prompt(
        db: Session,
        *,
        startup_id: uuid.UUID,
        founder_id: uuid.UUID,
        today: date | None = None,
    ) -> JournalPrompt:
        """Return today's prompt row, seeding the static fallback + enqueuing the AI job on
        first read. Idempotent per (startup, founder, day); tolerates the unique-constraint
        race the same way ``get_or_create_enrollment``/``get_or_create_recommendation_reason``
        do: insert inside a SAVEPOINT, and on ``IntegrityError`` re-select the winner's
        now-committed row."""
        d = today or date.today()
        row = (
            db.query(JournalPrompt)
            .filter_by(startup_id=startup_id, founder_id=founder_id, date=d)
            .first()
        )
        if row is not None:
            return row
        try:
            with db.begin_nested():
                row = JournalPrompt(
                    startup_id=startup_id,
                    founder_id=founder_id,
                    date=d,
                    prompt=JournalService.get_prompt(today=d),
                    status=EnrichmentStatus.generating,
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            # A concurrent caller won the race -- their row is now committed and visible.
            return (
                db.query(JournalPrompt)
                .filter_by(startup_id=startup_id, founder_id=founder_id, date=d)
                .one()
            )
        job_dispatcher.enqueue(
            db,
            "ai.journal.prompt",
            {"startup_id": str(startup_id), "founder_id": str(founder_id), "date": d.isoformat()},
            startup_id,
        )
        return row
