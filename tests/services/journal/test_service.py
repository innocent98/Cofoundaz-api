from app.db.models.enums import JournalMood
from app.services.journal.service import JournalService


def test_mood_to_score_uses_five_point_scale():
    assert JournalService.mood_to_score(JournalMood.rough) == 1
    assert JournalService.mood_to_score(JournalMood.meh) == 2
    assert JournalService.mood_to_score(JournalMood.okay) == 3
    assert JournalService.mood_to_score(JournalMood.good) == 4
    assert JournalService.mood_to_score(JournalMood.great) == 5


def test_score_to_mood_uses_five_point_scale():
    assert JournalService.score_to_mood(1) == JournalMood.rough
    assert JournalService.score_to_mood(2) == JournalMood.meh
    assert JournalService.score_to_mood(3) == JournalMood.okay
    assert JournalService.score_to_mood(4) == JournalMood.good
    assert JournalService.score_to_mood(5) == JournalMood.great
