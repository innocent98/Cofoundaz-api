"""A canned model and a sample term sheet for the term sheet tests."""

import json

SHEET_TEXT = [
    "\n".join(
        [
            "SERIES SEED TERM SHEET",
            "Pre-money valuation of NGN 400,000,000 and an investment of NGN 100,000,000.",
            "The option pool will be 12.5 percent of the pre-money fully diluted capitalisation.",
            "Liquidation preference 2x, participating, capped at 3x in total.",
        ]
    ),
    "\n".join(
        [
            "Board: the investor appoints two directors and the founders appoint two.",
            "Exclusivity: sixty days. Pro rata: super pro rata rights. Anti-dilution: full ratchet.",
        ]
    ),
]


class TermLLM:
    """Stands in for the model: an extraction payload, and wording that echoes the fixed text."""

    last_usage_tokens = 3000

    def __init__(self, extraction=None, boom=None, wording=None, on_wording=None):
        self.extraction = (
            extraction
            if extraction is not None
            else {
                "currency": "NGN",
                "stage": "seed",
                "pre_money": 400_000_000,
                "investment": 100_000_000,
                "option_pool_pre_percent": 12.5,
                "liquidation_preference_multiple": 2,
                "participating": True,
                "participation_cap_multiple": 3,
                "investor_board_seats": 2,
                "founder_board_seats": 2,
                "pro_rata": "super",
                "exclusivity_days": 60,
                "anti_dilution": "full_ratchet",
                "quotes": {"pre_money": "Pre-money valuation of NGN 400,000,000"},
            }
        )
        self.boom, self.wording, self.calls = boom, wording, []
        self.on_wording = on_wording  # runs while the wording call is in flight (a racing edit)

    def complete_json(self, messages, *, schema, max_tokens):
        self.calls.append(schema)
        if self.boom:
            raise self.boom
        if "quotes" in schema["properties"]:
            return json.loads(json.dumps(self.extraction))
        if self.on_wording is not None:
            self.on_wording()
        if self.wording is not None:
            return self.wording
        flags = []
        for line in messages[1].content.splitlines():
            if line.startswith("- rule "):
                rule = line.split(":")[0].removeprefix("- rule ")
                facts = line.split("Facts: ", 1)[1]
                flags.append({"rule": rule, "text": f"In plain words: {facts}"})
        return {"flags": flags}
