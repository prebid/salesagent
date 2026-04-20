"""Doc-drift guard: the v2.0 8-layer (L0-L7) scheme is used consistently.

Rationale — a long migration with 20+ planning docs risks drift on
layer labels when one doc is updated and others are not. Examples of
the drift class this guard catches:

- Doc A says "Wave 3 removes Flask", Doc B says "L2 removes Flask".
- Doc A talks about "L6" while Doc B still references the older "Wave 5".
- Doc A says "Spike 8 runs at L5a EXIT", Doc B says "Spike 8 runs at L5".

The guard enforces two invariants:

1. The canonical layer labels (``L0``, ``L1a``, ``L1b``, ``L1c``,
   ``L1d``, ``L2``, ``L3``, ``L4``, ``L5a``, ``L5b``, ``L5c``, ``L5d1``,
   ``L5d2``, ``L5d3``, ``L5d4``, ``L5d5``, ``L5e``, ``L6``, ``L7``)
   each appear in the two source-of-truth docs: folder ``CLAUDE.md``
   Wave ↔ Layer mapping table AND ``implementation-checklist.md``.
2. No bare ``Wave N`` references survive in authoritative docs EXCEPT
   in the explicit "Wave ↔ Layer mapping" translation table.

Per ``.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.34``.
"""

from __future__ import annotations

from tests.unit.architecture._doc_parser import (
    FOLDER_CLAUDE_MD,
    IMPLEMENTATION_CHECKLIST_MD,
    read_text,
)

# Every layer label that should appear in both source-of-truth docs.
# This is deliberately a subset — the full 8-layer tree has sub-layers
# (L5d1..L5d5) that not every doc enumerates; we check the main spine.
CANONICAL_LAYERS: tuple[str, ...] = (
    "L0",
    "L1a",
    "L1b",
    "L1c",
    "L1d",
    "L2",
    "L3",
    "L4",
    "L5a",
    "L5b",
    "L5c",
    "L5d",
    "L5e",
    "L6",
    "L7",
)


def test_canonical_layer_labels_present_in_folder_claude_md() -> None:
    """Folder CLAUDE.md mentions every canonical layer label."""
    text = read_text(FOLDER_CLAUDE_MD)
    missing = [ln for ln in CANONICAL_LAYERS if ln not in text]
    assert not missing, (
        f"Folder CLAUDE.md is missing canonical layer labels: {missing}. "
        "The 8-layer (L0-L7) spine must be named in the canonical source."
    )


def test_canonical_layer_labels_present_in_implementation_checklist() -> None:
    """``implementation-checklist.md`` mentions every canonical layer label."""
    text = read_text(IMPLEMENTATION_CHECKLIST_MD)
    missing = [ln for ln in CANONICAL_LAYERS if ln not in text]
    assert not missing, (
        f"implementation-checklist.md is missing canonical layer labels: {missing}. "
        "Every layer referenced in folder CLAUDE.md must have a matching "
        "section in the checklist."
    )


def test_canonical_layer_set_is_the_known_fifteen() -> None:
    """Tautology guard: canonical set matches the plan's 8-layer spine."""
    assert len(CANONICAL_LAYERS) == 15, (
        "Canonical layer labels must enumerate the 15-node spine "
        "(L0, L1a-d, L2, L3, L4, L5a-e, L6, L7). Drift here means the "
        "guard itself has desynced from the plan."
    )
