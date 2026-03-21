#!/usr/bin/env python3
"""Graduate @pending BDD tests that genuinely pass on all transports.

Reads pytest JSON report, identifies xpassed tests, and determines which
@pending tags can be safely graduated (all valid rows pass on all 4 transports).

Usage:
    # Generate report first:
    DATABASE_URL=... uv run pytest tests/bdd/ --json-report --json-report-file=/tmp/bdd-results.json -q --tb=no

    # Then analyze:
    uv run python scripts/graduate_pending.py /tmp/bdd-results.json

    # Apply graduations:
    uv run python scripts/graduate_pending.py /tmp/bdd-results.json --apply
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

TRANSPORTS = {"impl", "a2a", "mcp", "rest"}


def _parse_transport_and_row(param_part: str) -> tuple[str, str]:
    """Extract transport and row key from pytest param string."""
    parts = param_part.split("-", 1)
    transport = parts[0] if parts[0] in TRANSPORTS else "unknown"
    row = parts[1] if len(parts) > 1 else ""
    return transport, row


def _extract_tags(test: dict) -> set[str]:
    """Extract marker/tag names from a test result."""
    tags = set()
    for marker in test.get("markers", []):
        name = marker if isinstance(marker, str) else marker.get("name", "")
        tags.add(name)
    # Also extract from keywords
    for kw in test.get("keywords", []):
        if kw.startswith("T-UC-"):
            tags.add(kw)
    return tags


def analyze(report_path: str) -> dict:
    """Analyze JSON report and return graduation candidates."""
    with open(report_path) as f:
        data = json.load(f)

    # Collect per-scenario, per-row, per-transport outcomes
    # Key: (scenario_name, row_key) -> {transport: outcome}
    results: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    test_tags: dict[str, set[str]] = {}  # scenario_name -> tags

    for test in data["tests"]:
        nodeid = test["nodeid"]
        outcome = test["outcome"]

        if "[" not in nodeid:
            continue

        scenario_name = nodeid.split("[")[0].split("::")[-1]
        param_part = nodeid.split("[")[-1].rstrip("]")
        transport, row = _parse_transport_and_row(param_part)

        if transport == "unknown":
            continue

        results[(scenario_name, row)][transport] = outcome

        # Collect tags from keywords
        for kw in test.get("keywords", []):
            if kw.startswith("T-UC-") or kw == "pending":
                if scenario_name not in test_tags:
                    test_tags[scenario_name] = set()
                test_tags[scenario_name].add(kw)

    # Categorize
    graduate_all_transports = []  # (scenario, row) where all 4 xpass
    graduate_partial = []  # (scenario, row, transports) where some xpass
    xfailed_all = []  # all 4 xfail — no action needed

    for (scenario, row), transport_outcomes in sorted(results.items()):
        xpass_transports = {t for t, o in transport_outcomes.items() if o == "xpassed"}
        xfail_transports = {t for t, o in transport_outcomes.items() if o == "xfailed"}
        pass_transports = {t for t, o in transport_outcomes.items() if o == "passed"}
        fail_transports = {t for t, o in transport_outcomes.items() if o == "failed"}

        if not xpass_transports:
            continue

        if xpass_transports == TRANSPORTS or (xpass_transports | pass_transports) == TRANSPORTS:
            graduate_all_transports.append((scenario, row))
        else:
            graduate_partial.append((scenario, row, transport_outcomes))

    # Group by tag for tag-level graduation
    tag_candidates: dict[str, list[tuple[str, str]]] = defaultdict(list)
    tag_blockers: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)

    for scenario, row in graduate_all_transports:
        tags = test_tags.get(scenario, set())
        pending_tags = {t for t in tags if t.startswith("T-UC-")}
        for tag in pending_tags:
            tag_candidates[tag].append((scenario, row))

    for scenario, row, outcomes in graduate_partial:
        tags = test_tags.get(scenario, set())
        pending_tags = {t for t in tags if t.startswith("T-UC-")}
        for tag in pending_tags:
            tag_blockers[tag].append((scenario, row, outcomes))

    return {
        "graduate_all": graduate_all_transports,
        "graduate_partial": graduate_partial,
        "tag_candidates": dict(tag_candidates),
        "tag_blockers": dict(tag_blockers),
        "test_tags": test_tags,
    }


def print_report(analysis: dict) -> None:
    """Print human-readable graduation report."""
    grad_all = analysis["graduate_all"]
    grad_partial = analysis["graduate_partial"]
    tag_candidates = analysis["tag_candidates"]
    tag_blockers = analysis["tag_blockers"]

    print(f"{'=' * 70}")
    print("BDD @pending Graduation Report")
    print(f"{'=' * 70}")
    print(f"\nAll-transport xpass (ready to graduate): {len(grad_all)}")
    print(f"Partial xpass (mixed outcomes):           {len(grad_partial)}")

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
            print(f"\n  {tag}: UNSAFE ({len(candidates)}/{total} rows pass all transports)")
            for _sc, row, outcomes in blockers[:3]:
                outcomes_str = " ".join(f"{t}={o}" for t, o in sorted(outcomes.items()))
                print(f"    BLOCKER: {row[:60]} → {outcomes_str}")
            if len(blockers) > 3:
                print(f"    ... and {len(blockers) - 3} more blockers")
        else:
            safe_tags.append(tag)
            print(f"\n  {tag}: SAFE ({len(candidates)} rows, all pass all transports)")
            for _sc, row in candidates[:5]:
                print(f"    OK: {row[:70]}")
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

    # Individual rows that pass all transports but belong to unsafe tags
    orphan_rows = []
    for sc, row in grad_all:
        tags = analysis["test_tags"].get(sc, set())
        pending_tags = {t for t in tags if t.startswith("T-UC-")}
        if any(t in unsafe_tags for t in pending_tags):
            orphan_rows.append((sc, row, pending_tags))

    if orphan_rows:
        print(f"\n  Rows passing all transports in UNSAFE tags: {len(orphan_rows)}")
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
