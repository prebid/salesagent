#!/usr/bin/env python3
"""Graduate @pending BDD tests that genuinely pass on present transports.

Reads pytest JSON report, identifies xpassed tests, and determines which
@pending tags can be safely graduated using the shared ``grade_base`` seam
(same present-transport rule as ``audit_xfails`` / ``bdd_full_audit``).

Usage:
    # Generate report first:
    DATABASE_URL=... uv run pytest tests/bdd/ --json-report --json-report-file=/tmp/bdd-results.json -q --tb=no

    # Then analyze:
    uv run python scripts/graduate_pending.py /tmp/bdd-results.json

    # Apply graduations:
    uv run python scripts/graduate_pending.py /tmp/bdd-results.json --apply
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from bdd_audit_common import (  # noqa: E402
    extract_scenario_base,
    extract_transport,
    grade_base,
    load_bdd_artifact,
    load_e2e_rest_known_failure_bases,
    short_base,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_E2E_LEDGER_PATH = _REPO_ROOT / "tests" / "bdd" / "e2e_rest_known_failures.txt"


def analyze(report_path: str, *, ledger_path: Path | None = None) -> dict:
    """Analyze JSON report and return graduation candidates via shared grade_base."""
    loaded = load_bdd_artifact(Path(report_path))
    tests = loaded.tests
    nodeid_outcomes = loaded.nodeid_outcomes
    force_confirm = loaded.force_confirm
    ledger = load_e2e_rest_known_failure_bases(ledger_path or _E2E_LEDGER_PATH)

    # Collect tags per scenario base
    test_tags: dict[str, set[str]] = {}
    xpassed_bases: set[str] = set()
    for test in tests:
        nodeid = test["nodeid"]
        base = extract_scenario_base(nodeid)
        if test["outcome"] == "xpassed":
            xpassed_bases.add(base)
        for kw in test.get("keywords", []):
            if isinstance(kw, str) and (kw.startswith("T-UC-") or kw == "pending"):
                test_tags.setdefault(base, set()).add(kw)

    graduate_all: list[tuple[str, str]] = []
    graduate_confirm: list[tuple[str, str]] = []
    graduate_partial: list[tuple[str, str, dict[str, str]]] = []

    for base in sorted(xpassed_bases):
        grade = grade_base(
            base,
            nodeid_outcomes,
            force_confirm=force_confirm,
            ledger_member=base in ledger,
        )
        scenario = short_base(base)
        if grade.graduates:
            if grade.needs_confirmation:
                graduate_confirm.append((scenario, base))
            else:
                graduate_all.append((scenario, base))
        elif grade.passing or grade.mixed_examples:
            outcomes = {
                t: o
                for nid, o in nodeid_outcomes
                if extract_scenario_base(nid) == base and (t := extract_transport(nid))
            }
            graduate_partial.append((scenario, base, outcomes))

    # Group by tag for tag-level graduation
    tag_candidates: dict[str, list[tuple[str, str]]] = defaultdict(list)
    tag_blockers: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)

    for scenario, base in graduate_all:
        tags = test_tags.get(base, set())
        pending_tags = {t for t in tags if t.startswith("T-UC-")}
        for tag in pending_tags:
            tag_candidates[tag].append((scenario, base))

    for scenario, base, outcomes in graduate_partial:
        tags = test_tags.get(base, set())
        pending_tags = {t for t in tags if t.startswith("T-UC-")}
        for tag in pending_tags:
            tag_blockers[tag].append((scenario, base, outcomes))

    return {
        "graduate_all": graduate_all,
        "graduate_confirm": graduate_confirm,
        "graduate_partial": graduate_partial,
        "tag_candidates": dict(tag_candidates),
        "tag_blockers": dict(tag_blockers),
        "test_tags": test_tags,
        "force_confirm": force_confirm,
    }


def print_report(analysis: dict) -> None:
    """Print human-readable graduation report."""
    grad_all = analysis["graduate_all"]
    grad_confirm = analysis["graduate_confirm"]
    grad_partial = analysis["graduate_partial"]
    tag_candidates = analysis["tag_candidates"]
    tag_blockers = analysis["tag_blockers"]

    print(f"{'=' * 70}")
    print("BDD @pending Graduation Report")
    print(f"{'=' * 70}")
    print(f"\nAll-present-transport xpass (ready to graduate): {len(grad_all)}")
    print(f"Needs confirmation (single-/e2e incomplete):     {len(grad_confirm)}")
    print(f"Partial xpass (mixed outcomes):                  {len(grad_partial)}")

    # Tag-level analysis
    print(f"\n{'─' * 70}")
    print("TAG-LEVEL GRADUATION ANALYSIS")
    print(f"{'─' * 70}")

    all_tags = sorted(set(tag_candidates.keys()) | set(tag_blockers.keys()))
    safe_tags = []
    unsafe_tags = []

    for tag in all_tags:
        candidates = tag_candidates.get(tag, [])
        blockers = tag_blockers.get(tag, [])
        total = len(candidates) + len(blockers)

        if blockers:
            unsafe_tags.append(tag)
            print(f"\n  {tag}: UNSAFE ({len(candidates)}/{total} rows pass all present transports)")
            for _sc, _base, outcomes in blockers[:3]:
                outcomes_str = " ".join(f"{t}={o}" for t, o in sorted(outcomes.items()))
                print(f"    BLOCKER: {_sc[:60]} → {outcomes_str}")
            if len(blockers) > 3:
                print(f"    ... and {len(blockers) - 3} more blockers")
        else:
            safe_tags.append(tag)
            print(f"\n  {tag}: SAFE ({len(candidates)} rows, all pass all present transports)")
            for _sc, _base in candidates[:5]:
                print(f"    OK: {_sc[:70]}")
            if len(candidates) > 5:
                print(f"    ... and {len(candidates) - 5} more")

    print(f"\n{'─' * 70}")
    print("SUMMARY")
    print(f"{'─' * 70}")
    print(f"\n  SAFE tags (can graduate now):   {len(safe_tags)}")
    for tag in safe_tags:
        n = len(tag_candidates.get(tag, []))
        print(f"    {tag} ({n} rows)")
    print(f"\n  UNSAFE tags (have blockers):    {len(unsafe_tags)}")
    for tag in unsafe_tags:
        c = len(tag_candidates.get(tag, []))
        b = len(tag_blockers.get(tag, []))
        print(f"    {tag} ({c} pass, {b} blocked)")

    # Individual rows that pass all present transports but belong to unsafe tags
    orphan_rows = []
    for sc, base in grad_all:
        tags = analysis["test_tags"].get(base, set())
        pending_tags = {t for t in tags if t.startswith("T-UC-")}
        if any(t in unsafe_tags for t in pending_tags):
            orphan_rows.append((sc, base, pending_tags))

    if orphan_rows:
        print(f"\n  Rows passing all present transports in UNSAFE tags: {len(orphan_rows)}")
        print("  (These need row-level graduation, not tag-level)")


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/graduate_pending.py <json-report> [--apply]")
        sys.exit(1)

    report_path = sys.argv[1]
    apply = "--apply" in sys.argv

    analysis = analyze(report_path)
    print_report(analysis)

    if apply:
        print(f"\n{'=' * 70}")
        print("APPLYING GRADUATIONS")
        print(f"{'=' * 70}")
        # TODO: Modify conftest.py automatically
        safe_tags = [tag for tag in sorted(analysis["tag_candidates"].keys()) if tag not in analysis["tag_blockers"]]
        print("\nAdd these to _PENDING_GRADUATED_TAGS in tests/bdd/conftest.py:")
        for tag in safe_tags:
            print(f'    "{tag}",')


if __name__ == "__main__":
    main()
