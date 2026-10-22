from pydantic import BaseModel


class FormationStepPatch(BaseModel):
    """`PATCH /legal/formation/steps/{step_key}` — the desired state, not a delta.

    The FE sends where it wants the tick to be, so a repeat is idempotent (spec D7).
    """

    jurisdiction: str
    completed: bool
