from app.db.models.enums import CanvasType
from app.services.business.canvas_defs import CANVAS_BLOCKS, empty_blocks


def test_every_canvas_type_has_blocks():
    assert set(CANVAS_BLOCKS) == set(CanvasType)
    assert len(CANVAS_BLOCKS[CanvasType.business_model]) == 9
    assert len(CANVAS_BLOCKS[CanvasType.lean]) == 9
    assert len(CANVAS_BLOCKS[CanvasType.value_prop]) == 6
    assert len(CANVAS_BLOCKS[CanvasType.swot]) == 4
    assert len(CANVAS_BLOCKS[CanvasType.mission_vision]) == 2


def test_block_keys_unique_per_type_and_kinds_valid():
    for canvas_type, blocks in CANVAS_BLOCKS.items():
        keys = [b.key for b in blocks]
        assert len(keys) == len(set(keys)), f"dup keys in {canvas_type}"
        for b in blocks:
            assert b.kind in ("text", "list")


def test_empty_blocks_scaffold_matches_kinds():
    scaffold = empty_blocks(CanvasType.business_model)
    assert scaffold == {b.key: [] for b in CANVAS_BLOCKS[CanvasType.business_model]}
    mv = empty_blocks(CanvasType.mission_vision)
    assert mv == {"mission": "", "vision": ""}
