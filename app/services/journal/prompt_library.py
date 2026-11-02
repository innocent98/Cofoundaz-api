"""Static prompt library for PRD 21.4: reflection prompts by theme. No entry text is involved."""

THEMES: tuple[str, ...] = ("decisions", "energy", "team", "wins")

LIBRARY: dict[str, tuple[str, ...]] = {
    "decisions": (
        "What is one decision you made this week that you would make again?",
        "Which decision are you avoiding, and what would make it easier to take?",
        "What did you decide today without enough information, and what would you check first next time?",
        "If you had to choose between two priorities tomorrow, which one would you drop?",
        "What decision feels reversible that you are treating as permanent?",
    ),
    "energy": (
        "What gave you energy today?",
        "What drained you today, and was it avoidable?",
        "When did you feel most like yourself this week?",
        "What would a lighter day look like tomorrow?",
        "What did you do today only because you felt you should?",
    ),
    "team": (
        "Who helped you move forward this week, and have you told them?",
        "What conversation with your team do you need to have?",
        "Where did you take on work that someone else could own?",
        "What did you learn about a teammate this week?",
        "What is one thing you could ask for help with today?",
    ),
    "wins": (
        "What went well today that you do not want to overlook?",
        "What did you finish this week that you were unsure you could?",
        "What is a small win from today worth remembering?",
        "What did a customer say that made the work feel real?",
        "What are you proud of from the last month?",
    ),
}


def library(theme: str | None = None) -> dict[str, list[str]]:
    if theme is not None:
        return {theme: list(LIBRARY[theme])}
    return {t: list(LIBRARY[t]) for t in THEMES}
