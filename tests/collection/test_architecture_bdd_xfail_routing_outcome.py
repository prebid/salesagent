"""Guard: which rows the per-transport xfail exemption really keeps.

The parse behind the exemption is graded in
``tests/unit/test_architecture_bdd_xfail_reason_tokens.py``. This grades the routing outcome,
which needs the record a real session published.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._collection_manifest import load, manifest_dir

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UC003_MODULE = _REPO_ROOT / "tests" / "bdd" / "test_uc003_update_media_buy.py"


@pytest.mark.arch_guard
class TestTheRoutingOutcome:
    """What actually reaches the runner, rather than the shape of the code that decides it."""

    def test_a_per_transport_reason_keeps_its_mcp_and_rest_rows(self):
        """The exemption still SELECTS the rows a per-transport reason protects.

        Every other test here asks an AST-shape question — is the old literal gone, does
        the parser get called, is the raise guarded. None of them observes the routing
        OUTCOME, and a verify pass showed the consequence: inverting the predicate (route
        per-transport rows to deselection and everything else to selection) reds nothing,
        because no test in the lane runs collection and asserts which rows survive.

        So this reads which rows survived a REAL collection. It needs an UNFILTERED
        record: `[testenv:bdd_e2e]` runs `pytest tests/bdd/ -k e2e_rest`, where every
        mcp/rest row is deselected by the `-k` expression rather than by the exemption
        this grades, so `selected` there answers a different question. `filter=""`
        demands a record with no `-k`/`-m` narrowing — a shard on the sharded path,
        `[testenv:bdd]` on the plain one — and `target=None` because a shard's target is
        its file list, which moves whenever a bdd file is added.
        """
        rows = load(manifest_dir(), target=None, filter="")
        uc003 = f"{_UC003_MODULE.relative_to(_REPO_ROOT).as_posix()}::"
        module_rows = [row for row in rows if row["nodeid"].startswith(uc003)]
        assert module_rows, (
            f"no collected rows for {uc003} in any unfiltered collection record. Without them this test grades nothing."
        )

        def _is_mcp_or_rest(nodeid: str) -> bool:
            return any(token in nodeid for token in ("[mcp-", "[rest-", "[mcp]", "[rest]"))

        _CONFLICT_TOKENS = ("stale_revision", "ahead_revision", "revision below current", "revision above current")

        # The CONFLICT rows carry cause=production-gap scope=per-transport ref=#1607, so
        # the exemption must keep their mcp and rest copies. If the predicate inverts,
        # these are the first things to vanish.
        # Narrowed to the rows the exemption actually protects, and COUNTED. A first
        # version matched any mcp/rest revision-concurrency row and passed under an
        # inverted predicate, because 8 of those rows carry no strict xfail at all and
        # survive either way — the assertion was reading the wrong population, which is
        # this child's own defect in its own grader.
        protected = [
            row["nodeid"]
            for row in module_rows
            if row["selected"]
            and _is_mcp_or_rest(row["nodeid"])
            and any(token in row["nodeid"] for token in _CONFLICT_TOKENS)
        ]

        assert len(protected) == 8, (
            f"expected the 8 mcp/rest CONFLICT rows to survive collection, got "
            f"{len(protected)}. These carry `cause=production-gap scope=per-transport "
            f"ref=#1607`, so the exemption must keep them; losing them means the "
            f"predicate is inverted or gone. No AST-shape assertion in this file can see "
            f"that — only reading a real collection can.\n" + "\n".join(protected)
        )
