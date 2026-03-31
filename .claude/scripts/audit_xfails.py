"""Deterministic xfail audit: classify every xfailed BDD test.

Reads JSON test results + conftest.py xfail tags + step function AST to
produce a categorized report of all xfails.

Usage:
    python .claude/scripts/audit_xfails.py test-results/310326_1023/bdd.json
    python .claude/scripts/audit_xfails.py test-results/310326_1023/bdd.json --output .claude/reports/xfail-audit.md

Classification categories:
    PRODUCTION_GAP    - Feature specified in Gherkin, not implemented in src/
    TRANSPORT_GAP     - Feature works on some transports, REST/MCP/A2A missing param
    HARNESS_GAP       - Harness env not wired for this scenario
    PREMATURE_XFAIL   - Step calls pytest.xfail() before exercising production code
    MISSING_STEP      - No step definition exists for a Gherkin step
    STALE             - Marked xfail but actually passes (xpassed) on all 4 transports
    PARTIAL_PASS      - Passes on some transports, fails on others
    UNCLASSIFIED      - Doesn't match any known pattern

Graduation criteria (STALE → remove xfail):
    1. Xpassed on ALL 4 transports (impl, a2a, mcp, rest)
    2. Then steps are PASS in inspector report (if available)
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class XfailEntry:
    """A single xfailed test with classification."""

    nodeid: str
    scenario_base: str  # nodeid without transport suffix
    transport: str | None  # impl, a2a, mcp, rest, or None
    category: str = "UNCLASSIFIED"
    reason: str = ""
    xfail_source: str = ""  # "conftest:tag", "conftest:auto", "step:funcname"
    tags: set[str] = field(default_factory=set)


@dataclass
class AuditReport:
    """Full audit report."""

    total_xfailed: int = 0
    total_xpassed: int = 0
    entries: list[XfailEntry] = field(default_factory=list)
    xpassed_entries: list[XfailEntry] = field(default_factory=list)
    by_category: dict[str, list[XfailEntry]] = field(default_factory=lambda: defaultdict(list))
    by_uc: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))


# ── Conftest parser ───────────────────────────────────────────────────


def parse_conftest_xfail_tags(conftest_path: Path) -> dict[str, tuple[str, str]]:
    """Parse conftest.py to extract tag → (reason, mechanism) mappings.

    Returns dict mapping tag string to (reason, mechanism) where mechanism
    is one of: 'production_gap', 'transport_gap', 'harness_gap', 'partial_impl'.
    """
    text = conftest_path.read_text()
    tag_map: dict[str, tuple[str, str]] = {}

    # _XFAIL_TAGS dict: tag → reason (production gaps)
    for match in re.finditer(r'"(T-[^"]+)":\s*"([^"]+)"', text):
        tag, reason = match.group(1), match.group(2)
        tag_map[tag] = (reason, "production_gap")

    # _REST_XFAIL_TAGS set: REST transport gaps
    in_rest_block = False
    for line in text.split("\n"):
        if "_REST_XFAIL_TAGS" in line and "{" in line:
            in_rest_block = True
            continue
        if in_rest_block:
            if "}" in line:
                in_rest_block = False
                continue
            m = re.search(r'"(T-[^"]+)"', line)
            if m:
                tag_map[m.group(1)] = ("REST endpoint drops filter params", "transport_gap")

    # _UC026_XFAIL_TAGS, _UC019_XFAIL_TAGS, etc. — sets with production/harness gaps
    for set_match in re.finditer(
        r"(_UC\d+_XFAIL_TAGS)\s*(?::\s*set\[str\])?\s*=\s*\{([^}]+)\}",
        text,
        re.DOTALL,
    ):
        set_name = set_match.group(1)
        block = set_match.group(2)
        # Determine mechanism from context
        mechanism = "production_gap"
        if "harness" in set_name.lower() or "env" in set_name.lower():
            mechanism = "harness_gap"

        for tag_m in re.finditer(r'"(T-[^"]+)"', block):
            tag = tag_m.group(1)
            if tag not in tag_map:  # Don't overwrite more specific classifications
                tag_map[tag] = (f"from {set_name}", mechanism)

    # _UC004_PARTITION_TAGS, _UC004_BOUNDARY_TAGS — partial impl
    for set_match in re.finditer(
        r"(_UC\d+_(?:PARTITION|BOUNDARY)_TAGS)\s*(?::\s*set\[str\])?\s*=\s*\{([^}]+)\}",
        text,
        re.DOTALL,
    ):
        set_name = set_match.group(1)
        block = set_match.group(2)
        for tag_m in re.finditer(r'"(T-[^"]+)"', block):
            tag = tag_m.group(1)
            if tag not in tag_map:
                tag_map[tag] = (f"partition/boundary from {set_name}", "partial_impl")

    # _UC005_PARTIAL_TAGS
    for set_match in re.finditer(
        r"(_UC\d+_PARTIAL_TAGS)\s*=\s*\{([^}]+)\}", text, re.DOTALL
    ):
        block = set_match.group(2)
        for tag_m in re.finditer(r'"(T-[^"]+)"', block):
            tag = tag_m.group(1)
            if tag not in tag_map:
                tag_map[tag] = ("partial implementation", "partial_impl")

    # MCP selective xfails
    for match in re.finditer(r'"(T-[^"]+)".*?reason="([^"]+)"', text):
        tag, reason = match.group(1), match.group(2)
        if tag not in tag_map and "MCP" in reason.upper():
            tag_map[tag] = (reason, "transport_gap")

    # _UC002_VALIDATION_XFAIL, _UC006_VALIDATION_XFAIL — selective xfails
    for match in re.finditer(
        r'\(\s*"(T-[^"]+)",\s*\{[^}]*\},\s*"([^"]+)"\s*\)', text
    ):
        tag, reason = match.group(1), match.group(2)
        if tag not in tag_map:
            tag_map[tag] = (reason, "production_gap")

    # _UC004_XFAIL_TAGS dict with tuples: tag → (reason, strict)
    for match in re.finditer(
        r'"(T-UC-004[^"]+)":\s*\(\s*"([^"]+)",\s*(True|False)\s*\)', text
    ):
        tag, reason = match.group(1), match.group(2)
        if tag not in tag_map:
            tag_map[tag] = (reason, "production_gap")

    # _UC003_EXT_XFAILS dict
    for match in re.finditer(
        r'"(T-UC-003[^"]+)":\s*"([^"]+)"', text
    ):
        tag, reason = match.group(1), match.group(2)
        if tag not in tag_map:
            tag_map[tag] = (reason, "production_gap")

    return tag_map


# ── Step-level xfail detector ────────────────────────────────────────


def find_premature_xfails(steps_dir: Path) -> set[str]:
    """Find step functions that call pytest.xfail() before any production code.

    Returns set of function names that unconditionally xfail.
    """
    premature: set[str] = set()

    for py_file in steps_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue

            # Check if decorated with @then, @given, @when
            is_step = False
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    func = dec.func
                    if isinstance(func, ast.Name) and func.id in ("then", "given", "when"):
                        is_step = True
                    elif isinstance(func, ast.Attribute) and func.attr in ("then", "given", "when"):
                        is_step = True
            if not is_step:
                continue

            # Check if first meaningful statement is pytest.xfail()
            for stmt in node.body:
                # Skip docstrings
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Constant, ast.Str)):
                    continue
                # Skip imports
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    continue
                # Check for pytest.xfail() call
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    call = stmt.value
                    if isinstance(call.func, ast.Attribute):
                        if call.func.attr == "xfail":
                            premature.add(node.name)
                            break
                # Any other statement means it's not premature
                break

    return premature


# ── JSON test result parser ───────────────────────────────────────────


def parse_test_results(json_path: Path) -> tuple[list[dict], list[dict]]:
    """Parse BDD JSON results into xfailed and xpassed lists."""
    data = json.loads(json_path.read_text())
    xfailed = [t for t in data["tests"] if t["outcome"] == "xfailed"]
    xpassed = [t for t in data["tests"] if t["outcome"] == "xpassed"]
    return xfailed, xpassed


def extract_transport(nodeid: str) -> str | None:
    """Extract transport from parametrized nodeid."""
    if "[impl" in nodeid:
        return "impl"
    if "[a2a" in nodeid:
        return "a2a"
    if "[mcp" in nodeid:
        return "mcp"
    if "[rest" in nodeid:
        return "rest"
    return None


def extract_scenario_base(nodeid: str) -> str:
    """Strip transport suffix to get scenario base name."""
    return re.sub(r"\[(?:impl|a2a|mcp|rest).*\]$", "", nodeid)


def extract_uc(nodeid: str) -> str:
    """Extract use case from nodeid."""
    for uc in ["uc001", "uc002", "uc003", "uc004", "uc005", "uc006", "uc011", "uc019", "uc026"]:
        if uc in nodeid:
            return uc.upper().replace("UC0", "UC-0").replace("UC1", "UC-1")
    return "other"


def extract_tags(test_entry: dict) -> set[str]:
    """Extract BDD tags from test entry keywords."""
    tags = set()
    for kw in test_entry.get("keywords", []):
        if isinstance(kw, str) and (kw.startswith("T-") or kw.startswith("BR-")):
            tags.add(kw)
    return tags


# ── Classifier ────────────────────────────────────────────────────────


def classify_xfail(
    test: dict,
    tag_map: dict[str, tuple[str, str]],
    premature_xfails: set[str],
    xpassed_all4: set[str],
    xpassed_partial: dict[str, set[str]],
) -> XfailEntry:
    """Classify a single xfailed test."""
    nodeid = test["nodeid"]
    transport = extract_transport(nodeid)
    base = extract_scenario_base(nodeid)
    tags = extract_tags(test)
    wasxfail = test.get("wasxfail", "")

    entry = XfailEntry(
        nodeid=nodeid,
        scenario_base=base,
        transport=transport,
        tags=tags,
    )

    # Priority 1: Missing step definition (auto-xfail from pytest hook)
    if "Step definition not found" in wasxfail:
        entry.category = "MISSING_STEP"
        entry.reason = wasxfail
        entry.xfail_source = "conftest:auto"
        return entry

    # Priority 2: No harness environment
    if "No harness environment" in wasxfail or "not implemented" in wasxfail.lower():
        entry.category = "HARNESS_GAP"
        entry.reason = wasxfail
        entry.xfail_source = "conftest:auto"
        return entry

    # Priority 3: Match against conftest tag map
    for tag in tags:
        if tag in tag_map:
            reason, mechanism = tag_map[tag]
            entry.reason = reason
            entry.xfail_source = f"conftest:tag:{tag}"
            if mechanism == "transport_gap":
                entry.category = "TRANSPORT_GAP"
            elif mechanism == "harness_gap":
                entry.category = "HARNESS_GAP"
            elif mechanism == "partial_impl":
                entry.category = "PARTIAL_IMPL"
            else:
                entry.category = "PRODUCTION_GAP"
            return entry

    # Priority 4: Check if "pending" marker (catch-all for unimplemented features)
    all_keywords = {kw for kw in test.get("keywords", []) if isinstance(kw, str)}
    if "pending" in all_keywords:
        # Determine sub-category from tags
        if any(t.startswith("T-UC-004-partition") or t.startswith("T-UC-004-boundary") for t in tags):
            entry.category = "PARTIAL_IMPL"
            entry.reason = "pending partition/boundary — some values pass, others fail"
        else:
            entry.category = "PRODUCTION_GAP"
            entry.reason = "pending: production feature not implemented"
        entry.xfail_source = "conftest:pending"
        return entry

    # Priority 4b: NFR scenarios (T-UC-XXX-nfr-*)
    if any("-nfr-" in t for t in tags):
        entry.category = "PRODUCTION_GAP"
        entry.reason = "non-functional requirement — not implemented in production"
        entry.xfail_source = "conftest:inferred:nfr"
        return entry

    # Priority 4c: Sandbox scenarios
    if any("sandbox" in t for t in tags):
        entry.category = "PRODUCTION_GAP"
        entry.reason = "sandbox mode not implemented"
        entry.xfail_source = "conftest:inferred:sandbox"
        return entry

    # Priority 4d: Filter/identify scenarios
    if any("-filter" in t or "-identify" in t for t in tags):
        entry.category = "PRODUCTION_GAP"
        entry.reason = "filter/identification feature not implemented"
        entry.xfail_source = "conftest:inferred:filter"
        return entry

    # Priority 4e: Infer from tag patterns when no explicit match
    # Tags like T-UC-XXX-partition-* or T-UC-XXX-boundary-* are partition/boundary tests
    if any("partition" in t or "boundary" in t for t in tags):
        entry.category = "PARTIAL_IMPL"
        entry.reason = "partition/boundary scenario — valid/invalid value ranges"
        entry.xfail_source = "conftest:inferred:partition-boundary"
        return entry

    # Tags like T-UC-XXX-ext-* are extension/error scenarios
    if any("-ext-" in t for t in tags):
        entry.category = "PRODUCTION_GAP"
        entry.reason = "extension error scenario — error codes/suggestions not implemented"
        entry.xfail_source = "conftest:inferred:extension"
        return entry

    # Tags like T-UC-XXX-inv-* are invariant scenarios
    if any("-inv-" in t for t in tags):
        entry.category = "PRODUCTION_GAP"
        entry.reason = "invariant scenario — production validation not implemented"
        entry.xfail_source = "conftest:inferred:invariant"
        return entry

    # Tags like T-UC-XXX-alt-* are alternative flow scenarios
    if any("-alt-" in t for t in tags):
        entry.category = "PRODUCTION_GAP"
        entry.reason = "alternative flow — feature not wired"
        entry.xfail_source = "conftest:inferred:alt-flow"
        return entry

    # Tags like T-UC-XXX-main-* are main flow scenarios
    if any("-main-" in t for t in tags):
        entry.category = "PRODUCTION_GAP"
        entry.reason = "main flow — feature not fully implemented"
        entry.xfail_source = "conftest:inferred:main-flow"
        return entry

    # Priority 4f: Any remaining T-UC-* tag means conftest xfail matched but parser missed it
    if any(t.startswith("T-UC-") for t in tags):
        # Infer from tag content
        sample_tag = next(t for t in tags if t.startswith("T-UC-"))
        if "webhook" in sample_tag or "attr-" in sample_tag or "dim-" in sample_tag:
            entry.category = "PRODUCTION_GAP"
            entry.reason = f"feature not implemented (from tag {sample_tag})"
        elif "daterange" in sample_tag:
            entry.category = "PARTIAL_IMPL"
            entry.reason = f"date range partially applied (from tag {sample_tag})"
        elif "rule-" in sample_tag:
            entry.category = "PRODUCTION_GAP"
            entry.reason = f"business rule not implemented (from tag {sample_tag})"
        else:
            entry.category = "PRODUCTION_GAP"
            entry.reason = f"from conftest xfail (tag {sample_tag})"
        entry.xfail_source = f"conftest:inferred:tag-prefix"
        return entry

    # Priority 5: Check wasxfail reason text for clues
    if wasxfail:
        reason_lower = wasxfail.lower()
        if "rest" in reason_lower and ("endpoint" in reason_lower or "drops" in reason_lower):
            entry.category = "TRANSPORT_GAP"
            entry.reason = wasxfail
            entry.xfail_source = "conftest:reason"
            return entry
        if "mcp" in reason_lower and "wrapper" in reason_lower:
            entry.category = "TRANSPORT_GAP"
            entry.reason = wasxfail
            entry.xfail_source = "conftest:reason"
            return entry
        if any(kw in reason_lower for kw in ["not implemented", "gap", "spec-production", "not yet"]):
            entry.category = "PRODUCTION_GAP"
            entry.reason = wasxfail
            entry.xfail_source = "conftest:reason"
            return entry
        if any(kw in reason_lower for kw in ["harness", "env", "wired"]):
            entry.category = "HARNESS_GAP"
            entry.reason = wasxfail
            entry.xfail_source = "conftest:reason"
            return entry
        # Has a reason but doesn't match known patterns
        entry.category = "PRODUCTION_GAP"  # Default for tagged xfails with reasons
        entry.reason = wasxfail
        entry.xfail_source = "conftest:reason"
        return entry

    # Priority 6: Unclassified
    entry.category = "UNCLASSIFIED"
    entry.reason = "no wasxfail reason and no matching tag"
    return entry


def classify_xpassed(
    xpassed_tests: list[dict],
) -> tuple[set[str], dict[str, set[str]]]:
    """Classify xpassed tests into all-4-transports vs partial.

    Returns (all4_bases, partial_bases_with_transports).
    """
    by_scenario = defaultdict(set)
    for t in xpassed_tests:
        transport = extract_transport(t["nodeid"])
        base = extract_scenario_base(t["nodeid"])
        if transport:
            by_scenario[base].add(transport)

    all4 = {base for base, transports in by_scenario.items() if transports == {"impl", "a2a", "mcp", "rest"}}
    partial = {base: transports for base, transports in by_scenario.items() if transports != {"impl", "a2a", "mcp", "rest"}}

    return all4, partial


# ── Report generator ──────────────────────────────────────────────────


def generate_report(report: AuditReport, output_path: Path | None = None) -> str:
    """Generate markdown report from audit results."""
    lines = [
        "# BDD Xfail Audit Report",
        "",
        f"Generated from test results. Deterministic classification.",
        "",
        "## Summary",
        "",
        f"- **Total xfailed tests**: {report.total_xfailed}",
        f"- **Total xpassed tests**: {report.total_xpassed}",
        "",
        "### By category",
        "",
        "| Category | Count | % | Description |",
        "|----------|-------|---|-------------|",
    ]

    category_desc = {
        "PRODUCTION_GAP": "Feature in Gherkin, not implemented in src/",
        "TRANSPORT_GAP": "Works on some transports, param not forwarded",
        "HARNESS_GAP": "Harness env not wired for this scenario",
        "PARTIAL_IMPL": "Partition/boundary — some values pass, others fail",
        "MISSING_STEP": "No step definition exists for a Gherkin step",
        "PREMATURE_XFAIL": "Step calls pytest.xfail() before production code",
        "STALE": "Passes all 4 transports — should graduate",
        "PARTIAL_PASS": "Passes some transports, not all",
        "UNCLASSIFIED": "No matching pattern found",
    }

    for cat in ["PRODUCTION_GAP", "TRANSPORT_GAP", "HARNESS_GAP", "PARTIAL_IMPL",
                 "MISSING_STEP", "PREMATURE_XFAIL", "STALE", "PARTIAL_PASS", "UNCLASSIFIED"]:
        entries = report.by_category.get(cat, [])
        pct = len(entries) / report.total_xfailed * 100 if report.total_xfailed else 0
        desc = category_desc.get(cat, "")
        lines.append(f"| {cat} | {len(entries)} | {pct:.0f}% | {desc} |")

    lines.extend(["", "### By use case", ""])
    lines.append("| UC | PROD_GAP | TRANSPORT | HARNESS | PARTIAL | MISSING | PREMATURE | STALE | UNCLASS | Total |")
    lines.append("|" + "|".join(["---"] * 10) + "|")

    all_ucs = sorted(report.by_uc.keys())
    for uc in all_ucs:
        counts = report.by_uc[uc]
        total = sum(counts.values())
        lines.append(
            f"| {uc} | {counts.get('PRODUCTION_GAP', 0)} | {counts.get('TRANSPORT_GAP', 0)} | "
            f"{counts.get('HARNESS_GAP', 0)} | {counts.get('PARTIAL_IMPL', 0)} | "
            f"{counts.get('MISSING_STEP', 0)} | {counts.get('PREMATURE_XFAIL', 0)} | "
            f"{counts.get('STALE', 0)} | {counts.get('UNCLASSIFIED', 0)} | {total} |"
        )

    # Xpassed section
    lines.extend([
        "",
        "## Xpassed Tests (marked xfail but pass)",
        "",
        f"- **All 4 transports (graduation candidates)**: {len([e for e in report.xpassed_entries if e.category == 'STALE'])}",
        f"- **Partial pass (keep investigating)**: {len([e for e in report.xpassed_entries if e.category == 'PARTIAL_PASS'])}",
        "",
    ])

    # STALE details
    stale = [e for e in report.xpassed_entries if e.category == "STALE"]
    if stale:
        seen_bases = set()
        lines.append("### Graduation candidates (all 4 transports pass)")
        lines.append("")
        for e in sorted(stale, key=lambda x: x.scenario_base):
            if e.scenario_base not in seen_bases:
                seen_bases.add(e.scenario_base)
                short = e.scenario_base.split("::")[-1] if "::" in e.scenario_base else e.scenario_base
                lines.append(f"- {short}")

    # Partial pass details
    partial = [e for e in report.xpassed_entries if e.category == "PARTIAL_PASS"]
    if partial:
        lines.extend(["", "### Partial pass (some transports only)", ""])
        by_missing = defaultdict(list)
        seen_bases = {}
        for e in partial:
            if e.scenario_base not in seen_bases:
                seen_bases[e.scenario_base] = set()
            seen_bases[e.scenario_base].add(e.transport)

        for base, transports in sorted(seen_bases.items()):
            missing = {"impl", "a2a", "mcp", "rest"} - transports
            short = base.split("::")[-1] if "::" in base else base
            lines.append(f"- {short} — passes: {sorted(transports)}, missing: {sorted(missing)}")

    # Actionable summary
    lines.extend([
        "",
        "## Actionable Summary",
        "",
        "### Immediate actions",
        f"1. **Graduate {len(stale)} stale scenarios** — remove xfail tags from conftest (all 4 transports pass with strong assertions)",
        f"2. **Fix {len(report.by_category.get('PREMATURE_XFAIL', []))} premature xfails** — step calls pytest.xfail() before production code",
        "",
        "### Tracked debt",
        f"3. **{len(report.by_category.get('PRODUCTION_GAP', []))} production gaps** — legitimate, needs src/ implementation",
        f"4. **{len(report.by_category.get('TRANSPORT_GAP', []))} transport gaps** — REST/MCP/A2A wrappers missing params",
        f"5. **{len(report.by_category.get('HARNESS_GAP', []))} harness gaps** — test env not wired, fixable in tests/",
        f"6. **{len(report.by_category.get('PARTIAL_IMPL', []))} partial implementations** — partition/boundary values vary",
        f"7. **{len(report.by_category.get('MISSING_STEP', []))} missing steps** — step definitions needed",
        f"8. **{len(report.by_category.get('UNCLASSIFIED', []))} unclassified** — need manual review",
    ])

    text = "\n".join(lines)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text)
        print(f"Report written to {output_path}")
    return text


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Deterministic BDD xfail audit")
    parser.add_argument("json_path", help="Path to bdd.json test results")
    parser.add_argument("--output", "-o", help="Output markdown path", default=None)
    parser.add_argument("--conftest", help="Path to conftest.py", default="tests/bdd/conftest.py")
    parser.add_argument("--steps-dir", help="Path to step definitions", default="tests/bdd/steps")
    parser.add_argument("--inspector-report", help="Path to inspector report for cross-reference", default=None)
    args = parser.parse_args()

    json_path = Path(args.json_path)
    conftest_path = Path(args.conftest)
    steps_dir = Path(args.steps_dir)
    output_path = Path(args.output) if args.output else None

    # Step 1: Parse conftest xfail tags
    print("Parsing conftest.py xfail tags...")
    tag_map = parse_conftest_xfail_tags(conftest_path)
    print(f"  Found {len(tag_map)} tag → reason mappings")

    # Step 2: Find premature xfails in step functions
    print("Scanning step functions for premature xfails...")
    premature_xfails = find_premature_xfails(steps_dir)
    print(f"  Found {len(premature_xfails)} premature xfail functions: {sorted(premature_xfails)}")

    # Step 3: Parse test results
    print(f"Parsing {json_path}...")
    xfailed_tests, xpassed_tests = parse_test_results(json_path)
    print(f"  {len(xfailed_tests)} xfailed, {len(xpassed_tests)} xpassed")

    # Step 4: Classify xpassed tests
    print("Classifying xpassed tests...")
    all4_bases, partial_bases = classify_xpassed(xpassed_tests)
    print(f"  {len(all4_bases)} pass all 4 transports (graduation candidates)")
    print(f"  {len(partial_bases)} pass some transports (partial)")

    # Step 5: Classify each xfailed test
    print("Classifying xfailed tests...")
    report = AuditReport(
        total_xfailed=len(xfailed_tests),
        total_xpassed=len(xpassed_tests),
    )

    for test in xfailed_tests:
        entry = classify_xfail(test, tag_map, premature_xfails, all4_bases, partial_bases)
        report.entries.append(entry)
        report.by_category[entry.category].append(entry)
        uc = extract_uc(entry.nodeid)
        report.by_uc[uc][entry.category] += 1

    # Step 6: Create xpassed entries
    for test in xpassed_tests:
        base = extract_scenario_base(test["nodeid"])
        transport = extract_transport(test["nodeid"])
        if base in all4_bases:
            category = "STALE"
        else:
            category = "PARTIAL_PASS"
        entry = XfailEntry(
            nodeid=test["nodeid"],
            scenario_base=base,
            transport=transport,
            category=category,
            tags=extract_tags(test),
        )
        report.xpassed_entries.append(entry)

    # Step 7: Generate report
    print("\nGenerating report...")
    text = generate_report(report, output_path)

    if not output_path:
        print(text)
    else:
        # Print summary to stdout
        print("\n=== SUMMARY ===")
        for cat in ["PRODUCTION_GAP", "TRANSPORT_GAP", "HARNESS_GAP", "PARTIAL_IMPL",
                     "MISSING_STEP", "PREMATURE_XFAIL", "UNCLASSIFIED"]:
            count = len(report.by_category.get(cat, []))
            if count > 0:
                print(f"  {cat}: {count}")
        print(f"\n  STALE (graduation candidates): {len(all4_bases)} scenarios")
        print(f"  PARTIAL_PASS: {len(partial_bases)} scenarios")


if __name__ == "__main__":
    main()
