from typing import Any

import pytest

from app.services.sales.coach_prompts import call_prep_schema, scorecard_schema


def _objects(node: Any) -> list[dict[str, Any]]:
    """Every object-typed node in a JSON schema, depth first."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_objects(value))
    return found


@pytest.mark.parametrize("schema_fn", [scorecard_schema, call_prep_schema])
def test_schema_is_valid_for_strict_structured_output(schema_fn: Any) -> None:
    # OpenAI strict json_schema rejects the whole request (400) unless every object forbids
    # extra keys and lists every property as required; that broke real scorecards and call preps.
    objects = _objects(schema_fn())
    assert objects
    for obj in objects:
        assert obj.get("additionalProperties") is False
        assert set(obj["required"]) == set(obj["properties"])
