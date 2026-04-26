#!/usr/bin/env python3
"""Detect BDD scenarios with misrouted transport dispatch.

Finds scenarios that are tagged @rest/@mcp/@a2a (transport-specific) which
causes pytest_generate_tests to SKIP parametrization. These scenarios then
run WITHOUT ctx["transport"] set, causing the When step to dispatch through
IMPL instead of the tagged transport.

Also finds scenarios that SHOULD be parametrized across all 5 transports
but aren't — e.g., scenarios missing from test results entirely, or
scenarios present on fewer transports than expected.

Usage:
  python3 scripts/detect_misrouted_transport.py
  python3 scripts/detect_misrouted_transport.py test-results/240426_1116/bdd.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TRANSPORT_SPECIFIC_TAGS = {"rest", "mcp", "a2a"}
ALL_TRANSPORTS = {"impl", "a2a", "mcp", "rest", "e2e_rest"}
IN_PROCESS_TRANSPORTS = {"impl", "a2a", "mcp", "rest"}


def parse_feature_files() -> list[dict]:
    """Parse all feature files and extract scenarios with their tags."""
    features_dir = Path("tests/bdd/features")
    scenarios = []

    for ff in sorted(features_dir.glob("BR-UC-*.feature")):
        uc_match = re.search(r"UC-(\d+)", ff.name)
        uc = f"UC-{uc_match.group(1)}" if uc_match else "unknown"

        current_tags: set[str] = set()
        for line in ff.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("@"):
                current_tags = {t.lstrip("@") for t in stripped.split() if t.startswith("@")}
            elif stripped.startswith("Scenario:") or stripped.startswith("Scenario Outline:"):
                name = stripped.split(":", 1)[1].strip()
                scenarios.append({
                    "uc": uc,
                    "name": name,
                    "tags": current_tags.copy(),
                    "transport_tags": current_tags & TRANSPORT_SPECIFIC_TAGS,
                    "file": ff.name,
                })

    return scenarios


def load_test_results(json_path: str | None) -> dict[str, dict]:
    """Load test results and index by test function name."""
    if json_path is None:
        # Find latest
        results_dir = Path("test-results")
        latest = sorted(results_dir.iterdir())[-1] if results_dir.exists() else None
        if latest and (latest / "bdd.json").exists():
            json_path = str(latest / "bdd.json")
        else:
            return {}

    data = json.loads(Path(json_path).read_text())
    index: dict[str, dict] = defaultdict(lambda: {"transports": set(), "outcomes": {}})

    for t in data["tests"]:
        nodeid = t["nodeid"]
        func_with_param = nodeid.split("::")[-1]
        # Extract base function name (without parametrize suffix)
        func_base = func_with_param.split("[")[0]
        # Extract transport from parametrize
        transport = None
        if "[" in func_with_param:
            param = func_with_param.split("[")[1].rstrip("]")
            for tr in ALL_TRANSPORTS:
                if param.startswith(tr):
                    transport = tr
                    break

        entry = index[func_base]
        if transport:
            entry["transports"].add(transport)
            entry["outcomes"][transport] = t["outcome"]
        else:
            # Non-parametrized test
            entry["transports"].add("none")
            entry["outcomes"]["none"] = t["outcome"]

    return dict(index)


def scenario_to_test_name(scenario_name: str) -> str:
    """Convert Gherkin scenario name to pytest function name."""
    return "test_" + re.sub(r"[^a-zA-Z0-9]+", "_", scenario_name).strip("_").lower()


def detect_issues(scenarios: list[dict], results: dict[str, dict]) -> dict[str, list]:
    """Detect transport routing issues."""
    issues: dict[str, list] = {
        "misrouted": [],       # Tagged @rest/@mcp but dispatching through IMPL
        "missing_transports": [],  # Parametrized but fewer than expected transports
        "not_in_results": [],  # Scenario exists but no test found in results
    }

    # Which UCs have test files?
    test_files = set()
    for f in Path("tests/bdd").glob("test_uc*.py"):
        uc_match = re.search(r"uc(\d+)", f.name)
        if uc_match:
            test_files.add(f"UC-{uc_match.group(1).zfill(3)}")

    for scenario in scenarios:
        uc = scenario["uc"]

        # Skip UCs without test files
        if uc not in test_files:
            continue

        test_name = scenario_to_test_name(scenario["name"])

        # Find matching test in results (fuzzy match on first 30 chars)
        matched_key = None
        for key in results:
            if test_name[:30] in key or key[:30] in test_name:
                matched_key = key
                break

        if matched_key is None:
            issues["not_in_results"].append({
                "scenario": scenario["name"],
                "uc": uc,
                "file": scenario["file"],
                "tags": scenario["tags"],
                "expected_test": test_name,
            })
            continue

        result = results[matched_key]
        transports = result["transports"]

        # Check: transport-specific scenario running without parametrization
        if scenario["transport_tags"]:
            if "none" in transports:
                issues["misrouted"].append({
                    "scenario": scenario["name"],
                    "uc": uc,
                    "file": scenario["file"],
                    "tagged_transport": scenario["transport_tags"],
                    "actual_dispatch": "IMPL (no parametrization)",
                    "test_name": matched_key,
                    "outcome": result["outcomes"].get("none", "?"),
                })
        else:
            # Should be parametrized across all transports
            expected = IN_PROCESS_TRANSPORTS.copy()
            if "e2e_rest" in transports:
                expected.add("e2e_rest")

            missing = expected - transports
            if missing and "none" not in transports:
                issues["missing_transports"].append({
                    "scenario": scenario["name"],
                    "uc": uc,
                    "file": scenario["file"],
                    "expected": sorted(expected),
                    "actual": sorted(transports),
                    "missing": sorted(missing),
                    "test_name": matched_key,
                })

    return issues


def main():
    json_path = sys.argv[1] if len(sys.argv) > 1 else None

    print("Parsing feature files...")
    scenarios = parse_feature_files()
    print(f"  {len(scenarios)} scenarios across {len({s['uc'] for s in scenarios})} UCs")

    print("Loading test results...")
    results = load_test_results(json_path)
    print(f"  {len(results)} unique test functions")

    print("\nDetecting transport routing issues...\n")
    issues = detect_issues(scenarios, results)

    # Report
    total = sum(len(v) for v in issues.values())

    misrouted = issues["misrouted"]
    if misrouted:
        print(f"MISROUTED ({len(misrouted)}) — tagged @rest/@mcp/@a2a but dispatching through IMPL:")
        for m in misrouted:
            print(f"  {m['outcome']:8s} {m['uc']} {m['scenario'][:60]}")
            print(f"           tagged: {m['tagged_transport']} → actual: {m['actual_dispatch']}")
        print()

    missing = issues["missing_transports"]
    if missing:
        print(f"MISSING TRANSPORTS ({len(missing)}) — parametrized but fewer transports than expected:")
        for m in missing:
            print(f"  {m['uc']} {m['scenario'][:60]}")
            print(f"           missing: {m['missing']}")
        print()

    not_found = issues["not_in_results"]
    if not_found:
        print(f"NOT IN RESULTS ({len(not_found)}) — scenario exists but no test found:")
        by_uc = defaultdict(int)
        for n in not_found:
            by_uc[n["uc"]] += 1
        for uc, count in sorted(by_uc.items()):
            print(f"  {uc}: {count} scenarios")
        print()

    if total == 0:
        print("No transport routing issues found.")
    else:
        print(f"TOTAL: {total} issues ({len(misrouted)} misrouted, {len(missing)} missing transports, {len(not_found)} not in results)")


if __name__ == "__main__":
    main()
