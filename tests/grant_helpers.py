"""A canned model for the grants tests."""


class GrantLLM:
    last_usage_tokens = 2500

    def __init__(
        self, boom=None, match=None, answer="We help clinics book visits. [add your traction]"
    ):
        self.boom, self.match, self.answer, self.calls = boom, match, answer, []

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls.append((schema, messages))
        if self.boom:
            raise self.boom
        if "matches" in schema["properties"]:
            if self.match is not None:
                return self.match(messages)
            slugs = [
                line.split("slug=")[1].split(" |")[0]
                for line in messages[-1].content.splitlines()
                if line.startswith("- slug=")
            ]
            return {
                "matches": [
                    {"slug": s, "score": 95 - i * 3, "why": f"Fits your clinic startup, {s}."}
                    for i, s in enumerate(slugs)
                ]
            }
        return {"answer": self.answer}
