from typing import Protocol


class AIPanel(Protocol):
    def reply(self, context: dict) -> str: ...


class StubAIPanel:
    def reply(self, context: dict) -> str:
        stage = context.get("stage", "your")
        industry = context.get("industry", "startup")
        return (
            f"Got it — a {industry} startup at the {stage} stage. Let's calibrate your workspace."
        )


ai_panel: AIPanel = StubAIPanel()
