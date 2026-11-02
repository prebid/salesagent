"""Structural guard: the coverage map may not publish a claim as a measurement.

Two defects in ``scripts/audit/storyboard_coverage_map.py``, both of the shape this
epic (salesagent-v03pe) exists for — an instrument answering a question it cannot
answer, confidently, while consumers treat the answer as complete.

**The domain was declared, not derived.** ``ADVERTISED_TOOLS`` was a hand-maintained
set, and it decided ON-PATH vs OFF-PATH through the storyboard schema's ``required_tools``
any-of gate — i.e. it decided the DENOMINATOR every count downstream is quoted over, and
the domain of the one ``make quality`` gate wired to this module
(``test_architecture_storyboard_issue_map.py::test_every_on_path_storyboard_is_triaged``).
Measured at the 3.1.1 pin it had drifted in both directions: it claimed
``activate_signal``, ``get_signals`` and ``list_authorized_properties``, which the tool
registry does not implement, and omitted ``complete_task``, ``get_task_status`` and
``list_tasks``, which it does. The three phantom signals tools put three storyboards
ON-PATH — 48 checks — so the triage gate enforced a conformance path wider than the
agent has tools for, and defended the difference with its own green.

**A tag claim was published as coverage.** ``covered_by`` is presence of a
``@storyboard-v3.1`` tag plus a resolvable ``@source`` footer. A tagged scenario with
zero bound step definitions counted as coverage — the exact defect
``scripts/audit/scenario_liveness_join.py`` was written to fix and
``storyboard_check_index.py`` already consumes. This module grades the join and the
three-state verdict it produces: LIVE, CLAIMED-ONLY, and NOT-MEASURED when no BDD run
was joined at all. NOT-MEASURED is a verdict, never folded into a passing side.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX = REPO_ROOT / "tests" / "fixtures" / "adcp_storyboards_pinned" / "index.json"

sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import storyboard_coverage_map  # noqa: E402


def _index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def test_the_coverage_map_advertises_exactly_what_the_registry_declares() -> None:
    """One set, derived on both sides — so no hand-edit can reintroduce a second list.

    This is deliberately an EQUALITY over two derivations rather than a check of the
    current values. The defect it locks out is not "the list is wrong today", it is "a
    list exists at all": the previous hand-maintained set had drifted three names in
    each direction while both sides held fourteen entries, so every count-based check
    read 14 = 14 and agreed. Only comparing the MEMBERS finds that, and only comparing
    them against the registry keeps finding it.

    It stays live against the obvious ways a second list creeps back —
    ``frozenset(TOOLS) | {"get_signals"}``, or a literal reinstated wholesale — because
    either changes the members.
    """
    from src.core.tools.registry import TOOLS

    assert storyboard_coverage_map.ADVERTISED_TOOLS == set(TOOLS), (
        "the advertised-tool set the coverage map classifies with is not the tool registry's "
        "keys. src/core/tools/registry.py::TOOLS is the one declaration every transport is "
        "generated from, so it is what a buyer can actually reach; anything else here is a "
        "second list, and the last one drifted three names in each direction unnoticed."
    )


def test_the_advertised_tool_set_is_derived_in_source_not_written_out() -> None:
    """The members can agree today and still be a list. This reads the assignment.

    ``ADVERTISED_TOOLS = frozenset({...fourteen strings...})`` would pass the equality
    above on the day it was written and drift the day after — which is the entire
    history of this constant. The assignment must name ``TOOLS``.
    """
    source = Path(storyboard_coverage_map.__file__).read_text(encoding="utf-8")
    assignments = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "ADVERTISED_TOOLS" for t in node.targets)
    ]
    assert len(assignments) == 1, f"expected exactly one ADVERTISED_TOOLS assignment, found {len(assignments)}"

    names = {n.id for n in ast.walk(assignments[0].value) if isinstance(n, ast.Name)}
    assert "TOOLS" in names, (
        "ADVERTISED_TOOLS must be DERIVED from the tool registry, not written out. "
        f"Its assignment references {sorted(names) or 'no name at all'} — a literal set of tool "
        "names is a second declaration of what this agent serves, and it drifts."
    )
    literals = [
        node
        for node in ast.walk(assignments[0].value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert literals == [], (
        f"ADVERTISED_TOOLS names tool(s) literally: {[n.value for n in literals]}. Adding to or "
        "subtracting from the registry here is exactly the divergence deriving it removes."
    )


def test_no_on_path_storyboard_is_gated_only_by_tools_we_do_not_implement() -> None:
    """The ON-PATH set the ``make quality`` triage gate enforces, graded against src/.

    Deliberately NOT written as "the derived set equals the derived set", which would be
    a tautology over whatever ``advertised_tools`` happens to return. It grades the
    classifier's OUTPUT: a storyboard whose ``required_tools`` gate is satisfied by no
    tool in ``TOOLS`` cannot be on our conformance path, however the tool set is spelled.
    Three storyboards failed this before the derivation landed —
    ``universal/error-compliance-signals.yaml``,
    ``universal/get-signals-pagination-integrity.yaml``,
    ``universal/schema-validation-signals.yaml`` — all three on ``get_signals`` /
    ``activate_signal``, and the real runner baseline quoted in the coverage map's
    docstring had already observed us advertising neither.
    """
    from src.core.tools.registry import TOOLS

    index = _index()
    implemented = set(TOOLS)
    offenders = sorted(
        rel
        for rel in storyboard_coverage_map.on_path_from_vendored_index(REPO_ROOT, index)
        if (required := set(index["storyboards"][rel].get("required_tools", []))) and not (required & implemented)
    )
    assert offenders == [], (
        f"{len(offenders)} storyboard(s) are classified ON-PATH while this agent implements none of "
        "their required_tools. The any-of gate cannot be satisfied, so the pinned runner would skip "
        "them as `not_applicable` — and every count quoted over the on-path set, plus the issue-map "
        "triage gate, is enforcing a conformance path the agent has no tools for:\n"
        + "\n".join(
            f"  {rel}: required_tools {sorted(index['storyboards'][rel]['required_tools'])}" for rel in offenders
        )
    )


# ── The claim/coverage split ────────────────────────────────────────────────


def _row(status: str, coverage: str, covered_by: list[str] | None = None) -> dict:
    return {
        "storyboard": f"universal/{coverage.lower()}.yaml",
        "status": status,
        "coverage": coverage,
        "covered_by": covered_by if covered_by is not None else (["T-X"] if coverage != "NONE" else []),
    }


def test_an_unclassified_storyboard_is_counted_rather_than_dropped() -> None:
    """``classify_gates`` can return UNKNOWN, and no total used to hold it.

    An UNKNOWN row was in ``storyboards`` and in none of the other numbers, so a tier
    the classifier does not recognise vanished between two totals that both read as
    complete. It is now its own number, on neither the on-path nor the off-path side.
    """
    rows = [
        _row("ON-PATH", storyboard_coverage_map.COVERAGE_NONE),
        _row("UNKNOWN", storyboard_coverage_map.COVERAGE_NONE),
    ]
    totals = storyboard_coverage_map.coverage_totals(rows, liveness_measured=True)

    assert totals["unknown"] == 1, "an unclassified storyboard must be counted as unclassified"
    assert totals["on_path"] == 1, "an unclassified storyboard must not be counted as on path"
    assert totals["off_path"] == 0, "an unclassified storyboard must not be counted as off path"
    assert totals["storyboards"] == totals["on_path"] + totals["off_path"] + totals["gated"] + totals["unknown"], (
        "the four statuses must partition the examined storyboards — a verdict that is in the "
        "denominator and in no numerator is exactly how a row goes missing without a trace"
    )


def test_an_unmeasured_claim_is_never_counted_as_coverage() -> None:
    """No BDD run joined means no claim on the page has been shown to grade anything."""
    rows = [
        _row("ON-PATH", storyboard_coverage_map.COVERAGE_NOT_MEASURED),
        _row("ON-PATH", storyboard_coverage_map.COVERAGE_NONE),
    ]
    totals = storyboard_coverage_map.coverage_totals(rows, liveness_measured=False)

    assert totals["liveness_measured"] is False
    assert totals["on_path_coverage_not_measured"] == 1
    assert totals["on_path_covered_live"] == 0, "an unmeasured claim must not count as live coverage"
    assert totals["on_path_claimed_not_live"] == 0, (
        "an unmeasured claim must not count as a claim-only finding either — that is a statement "
        "about the scenario, and nothing was measured about it"
    )
    assert totals["on_path_uncovered"] == 1, "only the storyboard with no claim at all is uncovered"


def test_the_four_coverage_verdicts_partition_the_on_path_set() -> None:
    """Every on-path storyboard lands in exactly one verdict, and they sum to the whole."""
    rows = [
        _row("ON-PATH", storyboard_coverage_map.COVERAGE_LIVE),
        _row("ON-PATH", storyboard_coverage_map.COVERAGE_CLAIMED),
        _row("ON-PATH", storyboard_coverage_map.COVERAGE_NOT_MEASURED),
        _row("ON-PATH", storyboard_coverage_map.COVERAGE_NONE),
        _row("OFF-PATH", storyboard_coverage_map.COVERAGE_NONE),
    ]
    totals = storyboard_coverage_map.coverage_totals(rows, liveness_measured=True)

    verdict_sum = (
        totals["on_path_covered_live"]
        + totals["on_path_claimed_not_live"]
        + totals["on_path_coverage_not_measured"]
        + totals["on_path_uncovered"]
    )
    assert verdict_sum == totals["on_path"] == 4, (
        f"the coverage verdicts sum to {verdict_sum} over an on-path set of {totals['on_path']}. "
        "A verdict outside the partition is a storyboard whose coverage nobody reports."
    )


def test_the_rendered_cell_never_shows_an_unmeasured_claim_as_plain_coverage() -> None:
    """The report is what a reader acts on; the verdict has to be IN the cell.

    A bare list of scenario ids is what made this column read as coverage.
    """
    live = storyboard_coverage_map.coverage_cell(storyboard_coverage_map.COVERAGE_LIVE, ["T-A"])
    claimed = storyboard_coverage_map.coverage_cell(storyboard_coverage_map.COVERAGE_CLAIMED, ["T-A"])
    unmeasured = storyboard_coverage_map.coverage_cell(storyboard_coverage_map.COVERAGE_NOT_MEASURED, ["T-A"])
    none = storyboard_coverage_map.coverage_cell(storyboard_coverage_map.COVERAGE_NONE, [])

    assert live == "`T-A` (LIVE)"
    assert "claim only" in claimed and "T-A" in claimed
    assert "NOT MEASURED" in unmeasured and "T-A" in unmeasured
    assert "NO SCENARIO" in none
    assert len({live, claimed, unmeasured, none}) == 4, "the four verdicts must render distinguishably"
