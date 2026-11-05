"""A scripted investor and scorer standing in for `get_llm_client()` in the Mock Q&A tests."""

import json


class ScriptedLLM:
    last_usage_tokens = 1500

    def __init__(self, score: dict | None = None, *, boom_on: str | None = None):
        self.questions = [f"Investor question {n}?" for n in range(1, 40)]
        self.turn_calls: list[list] = []
        self.score_calls: list[list] = []
        self.boom_on = boom_on
        self.score = score or {
            "answers": [
                {"i": 1, "rating": 4, "feedback": "Specific and numbers-backed."},
                {"i": 2, "rating": 2, "feedback": "Vague about competitors."},
                {"i": 3, "rating": 5, "feedback": "Owned the weak spot."},
            ],
            "weakest_i": 2,
            "model_answer": "We compete with X on workflow and Y on data, and own the gap between.",
        }

    def complete(self, messages, *, max_tokens, temperature=0.7):
        self.turn_calls.append(messages)
        if self.boom_on == "turn":
            raise RuntimeError("provider down")
        return self.questions[len(self.turn_calls) - 1]

    def complete_json(self, messages, *, schema, max_tokens):
        self.score_calls.append(messages)
        if self.boom_on == "score":
            raise RuntimeError("provider down")
        return json.loads(json.dumps(self.score))
