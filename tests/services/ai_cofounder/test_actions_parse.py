import json
import uuid

from app.services.ai_cofounder.actions import MAX_PER_REPLY, extract_actions

KEYS = {"M1": uuid.uuid4(), "M2": uuid.uuid4()}
ALL = ("add_task", "update_roadmap", "draft_document")


def _wrap(items) -> str:
    return "Text before.\n<actions>" + json.dumps(items) + "</actions>"


def test_no_block_means_no_actions_and_the_text_is_untouched():
    text, actions = extract_actions("Just advice.", ALL, KEYS)
    assert text == "Just advice." and actions == []


def test_at_most_three_valid_proposals_even_after_junk():
    junk = [{"kind": "nope"}] * 4
    good = [{"kind": "add_task", "title": f"t{i}", "milestone": "M1"} for i in range(5)]
    text, actions = extract_actions(_wrap(junk + good), ALL, KEYS)
    assert text == "Text before." and len(actions) == MAX_PER_REPLY
    assert [a.payload["title"] for a in actions] == ["t0", "t1", "t2"]


def test_a_kind_the_agent_may_not_propose_is_dropped():
    items = [
        {"kind": "add_task", "title": "x", "milestone": "M1"},
        {
            "kind": "draft_document",
            "doc_kind": "one_pager",
            "title": "D",
            "sections": [{"heading": "h", "body": "b"}],
        },
    ]
    _t, actions = extract_actions(_wrap(items), ("draft_document",), KEYS)
    assert [a.kind for a in actions] == ["draft_document"]


def test_fields_are_clipped_and_normalised():
    items = [
        {"kind": "add_task", "title": "  " + "t" * 400 + "  ", "milestone": "M2", "effort": "huge"},
        {"kind": "update_roadmap", "milestone": "M1", "status": "in_progress"},
        {"kind": "update_roadmap", "milestone": "M1", "status": "banana"},
        {"kind": "update_roadmap", "milestone": "M1"},  # changes nothing
        {
            "kind": "draft_document",
            "doc_kind": "script",
            "title": "x",
            "sections": [{"heading": "h", "body": "b"}],
        },  # not an allowed document kind
        {"kind": "draft_document", "doc_kind": "nda", "title": "NDA", "sections": []},
    ]
    _t, actions = extract_actions(_wrap(items), ALL, KEYS)
    assert [a.kind for a in actions] == ["add_task", "update_roadmap"]
    assert len(actions[0].payload["title"]) == 160 and actions[0].payload["effort"] == "medium"
    assert actions[0].payload["milestone_id"] == str(KEYS["M2"])


def test_malformed_blocks_never_raise_and_are_always_stripped():
    for text in (
        "A\n<actions>not json</actions>",
        'A\n<actions>{"kind": "add_task"}</actions>',  # an object, not a list
        'A\n<actions>[{"kind":',  # cut off mid block
        "A\n<ACTIONS>[]</ACTIONS>",
    ):
        clean, actions = extract_actions(text, ALL, KEYS)
        assert clean == "A" and actions == []


def test_a_block_in_the_middle_is_removed_and_the_last_one_wins():
    first = [{"kind": "add_task", "title": "first", "milestone": "M1"}]
    second = [{"kind": "add_task", "title": "second", "milestone": "M1"}]
    text = f"<actions>{json.dumps(first)}</actions> middle <actions>{json.dumps(second)}</actions>"
    clean, actions = extract_actions(text, ALL, KEYS)
    assert "<actions>" not in clean and [a.payload["title"] for a in actions] == ["second"]
