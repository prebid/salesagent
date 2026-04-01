"""Cross-reference BDD inspector flags with test results.

Joins inspector output (which steps are weak?) with test results (which tests
pass/fail/xfail?) to produce an actionable report showing:

1. Flagged steps whose tests PASS — weak assertions hiding problems
2. Flagged steps whose tests XFAIL — already known, lower priority
3. Flagged steps whose tests FAIL — already caught, may need step fix
4. Unflagged steps whose tests FAIL — failure not due to assertion weakness

Usage:
    python .claude/scripts/cross_reference_audit.py \\
        --inspector .claude/reports/bdd-step-audit-YYYYMMDD_HHMM.json \\
        --results test-results/010426_1104/bdd.json \\
        --output .claude/reports/cross-reference-audit.md
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class InspectorFlag:
    function: str
    file: str
    line: int
    step_text: str
    reason: str
    severity: str = ""


@dataclass
class TestOutcome:
    nodeid: str
    outcome: str  # passed, failed, xfailed, xpassed
    transport: str | None = None
    error: str = ""


def parse_inspector_json(path: Path) -> list[InspectorFlag]:
    """Parse inspector JSON output."""
    data = json.loads(path.read_text())
    flags = []
    for entry in data:
        flags.append(
            InspectorFlag(
                function=entry["function"],
                file=entry.get("file", ""),
                line=entry.get("line", 0),
                step_text=entry.get("step_text", ""),
                reason=entry.get("reason", ""),
                severity=entry.get("severity", ""),
            )
        )
    return flags


def parse_test_results(path: Path) -> list[TestOutcome]:
    """Parse bdd.json test results."""
    data = json.loads(path.read_text())
    outcomes = []
    for t in data["tests"]:
        nodeid = t["nodeid"]
        # Extract transport
        transport = None
        m = re.search(r"\[(impl|a2a|mcp|rest)", nodeid)
        if m:
            transport = m.group(1)

        # Extract error
        error = ""
        longrepr = t.get("call", {}).get("longrepr", "")
        for line in longrepr.split("\n"):
            if line.strip().startswith("E "):
                error = line.strip()[2:].strip()
                break

        outcomes.append(
            TestOutcome(
                nodeid=nodeid,
                outcome=t["outcome"],
                transport=transport,
                error=error,
            )
        )
    return outcomes


def map_steps_to_functions(steps_dir: Path) -> dict[str, list[str]]:
    """Map step function names to the Gherkin patterns they handle.

    Returns dict: function_name -> list of step text patterns.
    """
    func_to_patterns: dict[str, list[str]] = defaultdict(list)

    for py_file in steps_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    # Match @given("..."), @when("..."), @then("...")
                    if isinstance(decorator, ast.Call) and decorator.args:
                        arg = decorator.args[0]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            func_to_patterns[node.name].append(arg.value)
    return func_to_patterns


def find_tests_using_step(
    step_function: str,
    step_patterns: list[str],
    test_outcomes: list[TestOutcome],
) -> list[TestOutcome]:
    """Find tests that likely use a given step function.

    Heuristic: match on function name components or step text keywords.
    """
    # Extract keywords from function name (e.g., "then_budget_validated" -> ["budget", "validated"])
    func_keywords = set(step_function.replace("then_", "").replace("given_", "").replace("when_", "").split("_"))
    # Remove very common words
    func_keywords -= {
        "the",
        "is",
        "a",
        "an",
        "and",
        "or",
        "not",
        "should",
        "be",
        "has",
        "have",
        "with",
        "for",
        "in",
        "of",
        "to",
    }

    # Extract UC from file path
    matching = []
    for outcome in test_outcomes:
        nodeid_lower = outcome.nodeid.lower()
        # Check if multiple keywords from function name appear in test nodeid
        matches = sum(1 for kw in func_keywords if kw in nodeid_lower)
        if matches >= 2 or (matches >= 1 and len(func_keywords) <= 2):
            matching.append(outcome)

    return matching


def extract_uc(path: str) -> str:
    """Extract UC from file path."""
    m = re.search(r"uc(\d+)", path)
    return f"UC-{m.group(1)}" if m else "GENERIC"


def generate_report(
    flags: list[InspectorFlag],
    outcomes: list[TestOutcome],
    output_path: Path,
) -> str:
    """Generate cross-reference report."""
    lines: list[str] = []
    lines.append("# BDD Inspector × Test Results Cross-Reference")
    lines.append("")

    # Summary
    outcome_counts = defaultdict(int)
    for o in outcomes:
        outcome_counts[o.outcome] += 1

    lines.append("## Data Sources")
    lines.append("")
    lines.append(f"- **Inspector flags**: {len(flags)} step functions flagged")
    lines.append(
        f"- **Test results**: {len(outcomes)} tests ({outcome_counts['passed']} passed, {outcome_counts['failed']} failed, {outcome_counts['xfailed']} xfailed, {outcome_counts['xpassed']} xpassed)"
    )
    lines.append("")

    # Group flags by UC
    by_uc: dict[str, list[InspectorFlag]] = defaultdict(list)
    for flag in flags:
        uc = extract_uc(flag.file)
        by_uc[uc].append(flag)

    # Group test outcomes by UC
    outcomes_by_uc: dict[str, list[TestOutcome]] = defaultdict(list)
    for o in outcomes:
        m = re.search(r"test_uc(\d+)", o.nodeid)
        uc = f"UC-{m.group(1)}" if m else "GENERIC"
        outcomes_by_uc[uc].append(o)

    # Cross-reference: for each UC, show flags vs test health
    lines.append("## Cross-Reference by Use Case")
    lines.append("")

    lines.append("| UC | Flags | Tests Passing | Tests Failing | Tests Xfailed | Risk |")
    lines.append("|----|-------|--------------|---------------|---------------|------|")

    for uc in sorted(set(list(by_uc.keys()) + list(outcomes_by_uc.keys()))):
        uc_flags = by_uc.get(uc, [])
        uc_outcomes = outcomes_by_uc.get(uc, [])
        passing = sum(1 for o in uc_outcomes if o.outcome == "passed")
        failing = sum(1 for o in uc_outcomes if o.outcome == "failed")
        xfailing = sum(1 for o in uc_outcomes if o.outcome == "xfailed")

        # Risk: flags with passing tests = highest risk (hidden problems)
        if uc_flags and passing > 0 and failing == 0:
            risk = "HIGH — weak assertions on passing tests"
        elif uc_flags and failing > 0:
            risk = "MEDIUM — flagged + failures exist"
        elif uc_flags:
            risk = "LOW — flagged but all xfailed"
        else:
            risk = "OK — no flags"
        lines.append(f"| {uc} | {len(uc_flags)} | {passing} | {failing} | {xfailing} | {risk} |")
    lines.append("")

    # Detailed flags grouped by severity
    lines.append("## Flagged Steps Detail")
    lines.append("")

    # Group by severity
    by_severity: dict[str, list[InspectorFlag]] = defaultdict(list)
    for flag in flags:
        sev = flag.severity if flag.severity else "UNCLASSIFIED"
        by_severity[sev].append(flag)

    for severity in ["MISSING", "WEAK", "COSMETIC", "UNCLASSIFIED"]:
        sev_flags = by_severity.get(severity, [])
        if not sev_flags:
            continue

        lines.append(f"### {severity} ({len(sev_flags)} steps)")
        lines.append("")

        for flag in sorted(sev_flags, key=lambda f: (f.file, f.line)):
            uc = extract_uc(flag.file)
            lines.append(f"- **`{flag.function}`** [{uc}] ({flag.file}:{flag.line})")
            lines.append(f"  - Step: {flag.step_text[:80]}")
            lines.append(f"  - Issue: {flag.reason[:150]}")
        lines.append("")

    # Action summary
    lines.append("## Action Summary")
    lines.append("")
    high_risk = [
        uc for uc, flags in by_uc.items() if flags and any(o.outcome == "passed" for o in outcomes_by_uc.get(uc, []))
    ]
    lines.append(f"- **High-risk UCs** (flags + passing tests): {', '.join(sorted(high_risk)) or 'none'}")
    lines.append(f"- **Total flags to address**: {len(flags)}")
    lines.append(f"- **Zero test failures**: {'YES' if outcome_counts['failed'] == 0 else 'NO'}")
    lines.append("")

    report = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"Report written to {output_path}", file=sys.stderr)
    return report


def main():
    parser = argparse.ArgumentParser(description="Cross-reference inspector flags with test results")
    parser.add_argument("--inspector", "-i", required=True, help="Inspector JSON report path")
    parser.add_argument("--results", "-r", required=True, help="bdd.json test results path")
    parser.add_argument("--output", "-o", default=".claude/reports/cross-reference-audit.md", help="Output path")
    args = parser.parse_args()

    print("Parsing inspector report...", file=sys.stderr)
    flags = parse_inspector_json(Path(args.inspector))
    print(f"  {len(flags)} flags", file=sys.stderr)

    print("Parsing test results...", file=sys.stderr)
    outcomes = parse_test_results(Path(args.results))
    print(f"  {len(outcomes)} tests", file=sys.stderr)

    print("Generating cross-reference...", file=sys.stderr)
    report = generate_report(flags, outcomes, Path(args.output))

    # Also print to stdout
    print(report)


if __name__ == "__main__":
    main()
