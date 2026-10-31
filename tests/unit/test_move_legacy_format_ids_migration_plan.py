"""The row plan of revision ddb20e636cee, which moves legacy-only format contracts into
``format_options`` (canonical-formats-migration.mdx § Seller migration, step 1).

The SQL either runs or does not; what can be wrong is which declaration each stored
format_id becomes and which rows are left alone, so that is what is graded here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.core.format_resolver import format_contract

_REVISION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "ddb20e636cee_move_legacy_format_ids_into_format_options.py"
)
_spec = importlib.util.spec_from_file_location("ddb20e636cee", _REVISION)
assert _spec and _spec.loader
revision = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(revision)

AAO = "https://creative.adcontextprotocol.org"
DISPLAY = {"agent_url": AAO, "id": "display_300x250_image"}
VIDEO = {"agent_url": AAO, "id": "video_standard_30s"}
CUSTOM = {"agent_url": "https://creatives.publisher.example", "id": "homepage_takeover"}


def test_a_projectable_row_moves_and_keeps_serving_the_ids_it_carried():
    moves, unmovable, held_back = revision.plan_canonical_moves([(("t1", "p1"), [DISPLAY, VIDEO])])

    assert unmovable == held_back == []
    [(key, declarations)] = moves
    assert key == ("t1", "p1")
    assert [(d["format_kind"], d["params"]) for d in declarations] == [
        ("image", {"width": 300, "height": 250}),
        ("video_hosted", {"duration_ms_exact": 30000}),
    ]
    # The legacy projection a 3.x buyer is served after the move is exactly the stored arm.
    served = format_contract(format_options=declarations, format_ids=None).format_ids
    assert [(str(ref.agent_url).rstrip("/"), ref.id) for ref in served] == [
        (AAO, "display_300x250_image"),
        (AAO, "video_standard_30s"),
    ]


def test_a_row_with_any_unprojectable_id_is_left_whole():
    moves, unmovable, held_back = revision.plan_canonical_moves([(("t1", "p2"), [DISPLAY, CUSTOM])])

    assert moves == held_back == []
    assert unmovable == [("t1", "p2")]


def test_a_product_served_by_a_legacy_profile_is_held_back_even_when_its_own_ids_project():
    """Its profile's ``format_ids`` are what it is served (``linked_format_contract``), and a
    legacy-only profile yields to a product's own declarations: moving the product would
    switch it from the profile's formats to its own stored, never-served ones."""
    linked, free = ("t1", "p5"), ("t1", "p6")

    moves, unmovable, held_back = revision.plan_canonical_moves([(linked, [DISPLAY]), (free, [DISPLAY])], held={linked})

    assert [key for key, _ in moves] == [free]
    assert unmovable == []
    assert held_back == [linked]


def test_a_row_with_no_legacy_arm_is_neither_moved_nor_reported():
    assert revision.plan_canonical_moves([(("t1", "p3"), None), (("t1", "p4"), [])]) == ([], [], [])


def test_the_downgrade_restores_exactly_what_the_upgrade_moved_and_nothing_authored():
    [(_, migrated)] = revision.plan_canonical_moves([((7,), [DISPLAY, VIDEO])])[0]
    authored = [{"format_kind": "image", "params": {"width": 300, "height": 250}}]

    restores = revision.plan_legacy_restores([((7,), migrated), ((8,), authored)])

    assert restores == [((7,), [DISPLAY, VIDEO])]
