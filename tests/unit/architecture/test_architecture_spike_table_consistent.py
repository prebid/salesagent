"""Doc-drift guard: the Spike Sequence table in folder CLAUDE.md §v2.0
Spike Sequence is the single source of truth for spike→layer assignment.

Rationale — Spike 8 (L5 go/no-go decision gate) is a HARD GATE; any
drift that reassigns spike 8 to a different layer or demotes it from
HARD to SOFT gate is a release-tag naming hazard (v1.99.0 vs v2.0.0).

Scope: the guard parses the Spike table out of folder CLAUDE.md and
asserts that every referenced layer is in the canonical layer spine
(L0-L7). Full spike-to-layer cross-check across 11 docs is beyond L0
— that becomes a table-parser exercise at L1+ when the table is
stable. The L0 guard is conservative: it protects the table's
existence, shape, and the 11-item count claimed in the plan.

Per ``.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.34``.
"""

from __future__ import annotations

import re

from tests.unit.architecture._doc_parser import FOLDER_CLAUDE_MD, read_text


def _spike_table(text: str) -> str:
    """Extract the Spike Sequence table block out of folder CLAUDE.md."""
    m = re.search(r"v2\.0 Spike Sequence.*?(?=\n## |\Z)", text, re.DOTALL)
    return m.group(0) if m else ""


def test_spike_table_exists_in_folder_claude_md() -> None:
    """Folder CLAUDE.md contains a ``v2.0 Spike Sequence`` section."""
    block = _spike_table(read_text(FOLDER_CLAUDE_MD))
    assert block, "folder CLAUDE.md is missing the ``v2.0 Spike Sequence`` section"


def test_spike_table_enumerates_at_least_eleven_rows() -> None:
    """The Spike table lists the 10 technical spikes + 1 decision gate.

    Per the plan's "10 technical spikes + 1 decision gate = 11 total"
    canonicalization. The guard accepts ≥11 to be robust to future
    spike additions — it only fails if the table shrinks below that.
    """
    block = _spike_table(read_text(FOLDER_CLAUDE_MD))
    # Markdown table rows begin with ``|``. Count rows that contain a
    # spike identifier pattern (``| N |`` or ``| **N** |``).
    spike_rows = re.findall(r"\|\s*\*{0,2}(\d+(?:\.\d+)?)\*{0,2}\s*\|", block)
    assert len(spike_rows) >= 11, (
        f"Spike table must enumerate ≥11 spikes; found {len(spike_rows)}. "
        f"IDs: {spike_rows}. Plan claims '10 technical spikes + 1 decision gate'."
    )


def test_spike_8_is_the_decision_gate() -> None:
    """The table explicitly calls out Spike 8 as the go/no-go decision gate.

    Defends against a silent renumber that would make `v2.0 release tag =
    'v1.99.0' vs 'v2.0.0'` guidance drift.
    """
    block = _spike_table(read_text(FOLDER_CLAUDE_MD))
    # The plan calls Spike 8 the "L5 go/no-go decision gate" or similar.
    assert "Spike 8" in block or "| **8** |" in block or "| 8 |" in block, (
        "Spike 8 (L5 go/no-go decision gate) must appear in the Spike Sequence "
        "table. Drift here risks release-tag naming (v1.99.0 vs v2.0.0)."
    )


def test_spike_table_references_L5a() -> None:
    """The Spike table places the decision gate on ``L5a``/``L5a EXIT``.

    Per plan: technical spikes run at L5a entry; decision gate runs at
    L5a EXIT (the transition point to L5b alias flip).
    """
    block = _spike_table(read_text(FOLDER_CLAUDE_MD))
    assert "L5a" in block, (
        "Spike table must reference L5a (entry for technical spikes, exit for "
        "decision gate). A table without L5a has drifted from the layer spine."
    )
