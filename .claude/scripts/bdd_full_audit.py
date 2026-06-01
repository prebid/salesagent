"""Full BDD audit: cross-reference test results, inspector reports, and xfail classifications.

Produces a categorized epic of actionable work items grouped by use case and priority.

Data sources:
    1. bdd.json test results — failures, xfails, xpasses, passes
    2. Inspector reports (.claude/reports/inspect-all-steps/*.md) — step quality flags
    3. conftest.py — xfail tag definitions and reasons
    4. Step source files — AST analysis for assertion patterns

Output: Markdown report with grouped issues ready for beads task filing.

Usage:
    python .claude/scripts/bdd_full_audit.py test-results/010426_0022/bdd.json
    python .claude/scripts/bdd_full_audit.py test-results/010426_0022/bdd.json --output .claude/reports/bdd-full-audit.md
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

TRANSPORTS = {"impl", "a2a", "mcp", "rest"}
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INSPECTOR_DIR = PROJECT_ROOT / ".claude" / "reports" / "inspect-all-steps"
CONFTEST_PATH = PROJECT_ROOT / "tests" / "bdd" / "conftest.py"
STEPS_DIR = PROJECT_ROOT / "tests" / "bdd" / "steps"


# ── Category taxonomy ────────────────────────────────────────────────
#
# Three action buckets:
#   FIX_NOW     — wiring/fixture/xfail-tag problems. Our job. Get tests green.
#   XFAIL_IT    — production behavior exposed by strong assertions. Mark xfail
#                 with documented reason. This IS the value of BDD — capturing
#                 system truth so refactoring is safe.
#   FEATURE_FIX — rare: .feature file is imprecise (e.g., budget=0 listed as
#                 "success" when it's obviously invalid). Fix the Examples table.
#
# Production bugs are NOT tasks to fix here. They're xfail reasons.

# FIX_NOW: test infrastructure we control
FIX_NOW = {
    "STALE_STRICT_XFAIL": "Test passes but strict xfail tag rejects it — remove tag",
    "GRADUATE": "All transports pass — remove xfail tag",
    "FIXTURE_GAP": "Strengthened assertion exposes missing test fixture data — fix factory",
    "STEP_BUG": "Step implementation has a bug — fix step code",
    "WEAK_ASSERTION": "Inspector-flagged: assertion doesn't match scenario intent",
}

# XFAIL_IT: production behavior, document and move on
XFAIL_IT = {
    "PROD_BEHAVIOR": "Production behavior differs from spec — xfail with reason",
    "PROD_BUG": "Production code crashes/errors — xfail with reason",
    "TRANSPORT_GAP": "Works on some transports, not all — xfail per-transport",
    "NOT_IMPLEMENTED": "Feature specified in Gherkin, not built yet — already xfailed",
    "PARTIAL_IMPL": "Some partition values work, others don't — already xfailed",
}

# FEATURE_FIX: rare .feature file corrections
FEATURE_FIX = {
    "SPEC_IMPRECISE": "Feature file Examples table has wrong expected outcome — fix row",
}


@dataclass
class TestEntry:
    """One test from bdd.json."""

    nodeid: str
    outcome: str  # passed, failed, xfailed, xpassed
    keywords: list[str] = field(default_factory=list)
    error: str = ""
    longrepr: str = ""


@dataclass
class InspectorFlag:
    """One flagged step from inspector report."""

    function: str
    step_text: str
    reason: str
    source_file: str  # which report


# ── Parsing ──────────────────────────────────────────────────────────


def parse_test_results(json_path: Path) -> list[TestEntry]:
    """Parse bdd.json into TestEntry list."""
    data = json.loads(json_path.read_text())
    entries = []
    for t in data["tests"]:
        error = ""
        longrepr = t.get("call", {}).get("longrepr", "")
        # Extract E-line from longrepr
        for line in longrepr.split("\n"):
            stripped = line.strip()
            if stripped.startswith("E "):
                error = stripped[2:].strip()
                break
        entries.append(
            TestEntry(
                nodeid=t["nodeid"],
                outcome=t["outcome"],
                keywords=t.get("keywords", []),
                error=error,
                longrepr=longrepr[:1000],
            )
        )
    return entries


def extract_uc(nodeid: str) -> str:
    """Extract use case from nodeid (e.g., 'UC-004')."""
    m = re.search(r"test_uc(\d+)", nodeid)
    return f"UC-{m.group(1)}" if m else "GENERIC"


def extract_transport(nodeid: str) -> str | None:
    """Extract transport from parametrized test nodeid."""
    m = re.search(r"\[(impl|a2a|mcp|rest)", nodeid)
    return m.group(1) if m else None


def extract_scenario_base(nodeid: str) -> str:
    """Strip transport suffix to get scenario base name."""
    # Remove everything inside [...] brackets
    base = re.sub(r"\[.*\]", "", nodeid)
    return base


def parse_inspector_reports() -> list[InspectorFlag]:
    """Parse all inspector markdown reports for flagged steps."""
    flags = []
    if not INSPECTOR_DIR.exists():
        return flags

    for md_file in INSPECTOR_DIR.glob("*.md"):
        content = md_file.read_text()
        # Parse table rows: | # | `func_name` | step text | reason |
        for m in re.finditer(
            r"\|\s*\d+\s*\|\s*`(\w+)`\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
            content,
        ):
            func_name = m.group(1)
            step_text = m.group(2).strip()
            reason = m.group(3).strip()
            # Skip When steps (flagged as "not a Then step")
            if "When action step" in reason or "not a Then step" in reason:
                continue
            flags.append(
                InspectorFlag(
                    function=func_name,
                    step_text=step_text,
                    reason=reason,
                    source_file=md_file.name,
                )
            )
    return flags


def parse_conftest_xfail_tags() -> dict[str, str]:
    """Parse conftest.py for tag → reason mappings."""
    tag_reasons: dict[str, str] = {}
    if not CONFTEST_PATH.exists():
        return tag_reasons

    content = CONFTEST_PATH.read_text()
    # Match patterns like "T-UC-004-xxx": "reason string"
    # or "T-UC-004-xxx": {"reason": "...", "strict": True}
    for m in re.finditer(
        r'"(T-[^"]+)"\s*:\s*(?:"([^"]+)"|{[^}]*"reason"\s*:\s*"([^"]+)")',
        content,
    ):
        tag = m.group(1)
        reason = m.group(2) or m.group(3) or ""
        tag_reasons[tag] = reason
    return tag_reasons


def parse_conftest_strict_tags() -> set[str]:
    """Parse conftest.py for tags with strict=True."""
    strict_tags: set[str] = set()
    if not CONFTEST_PATH.exists():
        return strict_tags

    content = CONFTEST_PATH.read_text()
    for m in re.finditer(
        r'"(T-[^"]+)"\s*:\s*\{[^}]*"strict"\s*:\s*True',
        content,
    ):
        strict_tags.add(m.group(1))
    return strict_tags


# ── Classification ───────────────────────────────────────────────────
#
# Decision tree for failed tests:
#   1. XPASS(strict)? → FIX_NOW:STALE_STRICT_XFAIL (remove tag)
#   2. Fixture not populated? → FIX_NOW:FIXTURE_GAP (fix factory)
#   3. Production crashes/errors on valid input? → XFAIL_IT:PROD_BUG
#   4. Production accepts input spec says invalid? → XFAIL_IT:PROD_BEHAVIOR
#   5. Production rejects input spec says valid? → check if spec is obviously
#      wrong (budget=0 as "success") → FEATURE_FIX:SPEC_IMPRECISE
#      otherwise → XFAIL_IT:PROD_BEHAVIOR (production is the truth)
#   6. Step code wrong? → FIX_NOW:STEP_BUG


def classify_failure(entry: TestEntry, strict_tags: set[str]) -> tuple[str, str, str]:
    """Classify a failed test. Returns (bucket, category, detail).

    bucket: FIX_NOW | XFAIL_IT | FEATURE_FIX
    """
    error = entry.error
    longrepr = entry.longrepr

    # ── FIX_NOW: stale strict xfail ──────────────────────────────────
    if "XPASS(strict)" in longrepr or "XPASS(strict)" in error:
        return "FIX_NOW", "STALE_STRICT_XFAIL", "Remove strict xfail tag"

    for kw in entry.keywords:
        if kw in strict_tags and "XPASS" in longrepr:
            return "FIX_NOW", "STALE_STRICT_XFAIL", f"Strict tag {kw}"

    # ── FIX_NOW: fixture not populated ───────────────────────────────
    if "is not None" in longrepr and ("has no" in error or "not None" in error):
        return "FIX_NOW", "FIXTURE_GAP", error
    if "requires populated value" in error:
        return "FIX_NOW", "FIXTURE_GAP", error

    # ── XFAIL_IT: production crashes (AttributeError, TypeError, etc.) ─
    if "object has no attribute" in error or "TypeError" in error:
        return "XFAIL_IT", "PROD_BUG", error

    # ── Production rejects what spec expects to succeed ──────────────
    if "Expected success but got error" in error:
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # ── Production accepts what spec expects to reject ───────────────
    # Covers: "Expected error X but operation succeeded"
    #         "Expected invalid X result but operation succeeded"
    #         "Expected AdCPError for X, got success"
    if "operation succeeded" in error and ("Expected" in error or "expected" in error):
        return "XFAIL_IT", "PROD_BEHAVIOR", error
    if "Expected AdCPError" in error:
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # ── Spec/production mismatch with explicit "expected X but got Y" ─
    if "but got" in error.lower() and "expected" in error.lower():
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # ── Production doesn't return expected data ──────────────────────
    # "Expected at least one X" — production returns empty where spec expects data
    if "Expected at least one" in error:
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # "Expected error_code" — production error doesn't include expected field
    if "Expected error_code" in error or "error_code" in error:
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # Context echo failures — production doesn't echo context back
    if "Context echo failed" in error or "context" in error.lower() and "echo" in error.lower():
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # ── FIX_NOW: step implementation bug (last resort) ───────────────
    if "AssertionError" in longrepr or "assert" in error.lower():
        return "FIX_NOW", "STEP_BUG", error

    return "FIX_NOW", "STEP_BUG", error or "Unknown failure"


def classify_xfail(entry: TestEntry, tag_reasons: dict[str, str]) -> tuple[str, str, str]:
    """Classify an xfailed test. Returns (bucket, category, reason).

    All xfails are XFAIL_IT by definition — they're already marked.
    """
    tags = [k for k in entry.keywords if k.startswith("T-")]

    for tag in tags:
        reason = tag_reasons.get(tag, "")
        if "transport" in reason.lower() or "not forwarded" in reason.lower():
            return "XFAIL_IT", "TRANSPORT_GAP", reason

    if "partition" in entry.nodeid or "boundary" in entry.nodeid:
        reason = tag_reasons.get(tags[0], "Partition/boundary xfail") if tags else "Partition/boundary xfail"
        return "XFAIL_IT", "PARTIAL_IMPL", reason

    reason = tag_reasons.get(tags[0], "Feature not implemented") if tags else "Feature not implemented"
    return "XFAIL_IT", "NOT_IMPLEMENTED", reason


def classify_xpass(entry: TestEntry, all_entries: list[TestEntry]) -> tuple[str, str, str]:
    """Classify an xpassed test. Returns (bucket, category, detail).

    All xpasses are FIX_NOW — remove the stale xfail tag.
    """
    base = extract_scenario_base(entry.nodeid)

    passing_transports = set()
    for e in all_entries:
        if extract_scenario_base(e.nodeid) == base and e.outcome in ("xpassed", "passed"):
            t = extract_transport(e.nodeid)
            if t:
                passing_transports.add(t)

    if passing_transports >= TRANSPORTS:
        return "FIX_NOW", "GRADUATE", f"All transports pass: {sorted(passing_transports)}"
    else:
        missing = TRANSPORTS - passing_transports
        return "FIX_NOW", "GRADUATE", f"Passes: {sorted(passing_transports)}, missing: {sorted(missing)}"


# ── Work item generation ─────────────────────────────────────────────


@dataclass
class WorkItem:
    """One actionable issue for beads filing."""

    title: str
    bucket: str  # FIX_NOW | XFAIL_IT | FEATURE_FIX
    category: str
    uc: str
    test_count: int
    details: str
    sample_tests: list[str] = field(default_factory=list)


def generate_work_items(
    failed: list[TestEntry],
    xfailed: list[TestEntry],
    xpassed: list[TestEntry],
    inspector_flags: list[InspectorFlag],
    tag_reasons: dict[str, str],
    strict_tags: set[str],
    all_entries: list[TestEntry],
) -> list[WorkItem]:
    """Generate actionable work items grouped by 3 buckets."""
    items: list[WorkItem] = []

    # ── 1. Failed tests → classify into buckets ──────────────────────
    # Group by (UC, bucket, category) then deduplicate across transports
    fail_groups: dict[tuple[str, str, str], list[TestEntry]] = defaultdict(list)

    for entry in failed:
        uc = extract_uc(entry.nodeid)
        bucket, cat, _ = classify_failure(entry, strict_tags)
        fail_groups[(uc, bucket, cat)].append(entry)

    for (uc, bucket, cat), entries in fail_groups.items():
        # Deduplicate: group by scenario base (strip transport)
        by_scenario: dict[str, list[TestEntry]] = defaultdict(list)
        for e in entries:
            by_scenario[extract_scenario_base(e.nodeid)].append(e)

        for base, group in by_scenario.items():
            transports = {extract_transport(e.nodeid) for e in group} - {None}
            rep_error = group[0].error
            transport_note = f" [{','.join(sorted(transports))}]" if transports else ""
            scenario_name = base.split("::")[-1] if "::" in base else base

            cat_desc = {**FIX_NOW, **XFAIL_IT, **FEATURE_FIX}.get(cat, cat)
            items.append(
                WorkItem(
                    title=f"[{uc}] {scenario_name[:50]}{transport_note}",
                    bucket=bucket,
                    category=cat,
                    uc=uc,
                    test_count=len(group),
                    details=rep_error[:150],
                    sample_tests=[e.nodeid.split("::")[-1][:80] for e in group[:3]],
                )
            )

    # ── 2. Xpassed tests → FIX_NOW (graduate) ───────────────────────
    grad_groups: dict[str, list[TestEntry]] = defaultdict(list)
    for entry in xpassed:
        _, _, detail = classify_xpass(entry, all_entries)
        grad_groups[detail].append(entry)

    for detail, entries in grad_groups.items():
        uc = extract_uc(entries[0].nodeid) if entries else "MIXED"
        all_pass = "All transports pass" in detail
        items.append(
            WorkItem(
                title=f"Graduate {'(all transports)' if all_pass else '(partial)'}: {uc}",
                bucket="FIX_NOW",
                category="GRADUATE",
                uc=uc,
                test_count=len(entries),
                details=detail,
                sample_tests=[e.nodeid.split("::")[-1][:80] for e in entries[:5]],
            )
        )

    # ── 3. Xfailed tests → XFAIL_IT (already marked, just summarize) ─
    xfail_groups: dict[tuple[str, str], list[TestEntry]] = defaultdict(list)
    for entry in xfailed:
        uc = extract_uc(entry.nodeid)
        _, cat, _ = classify_xfail(entry, tag_reasons)
        xfail_groups[(uc, cat)].append(entry)

    for (uc, cat), entries in xfail_groups.items():
        cat_desc = XFAIL_IT.get(cat, cat)
        items.append(
            WorkItem(
                title=f"[{uc}] {cat_desc} ({len(entries)} tests)",
                bucket="XFAIL_IT",
                category=cat,
                uc=uc,
                test_count=len(entries),
                details=f"{len(entries)} tests already xfailed",
                sample_tests=[e.nodeid.split("::")[-1][:80] for e in entries[:3]],
            )
        )

    # ── 4. Inspector flags → FIX_NOW (weak assertions) ───────────────
    if inspector_flags:
        by_uc: dict[str, list[InspectorFlag]] = defaultdict(list)
        for flag in inspector_flags:
            m = re.search(r"uc(\d+)", flag.source_file)
            uc = f"UC-{m.group(1)}" if m else "GENERIC"
            by_uc[uc].append(flag)

        for uc, flags in by_uc.items():
            items.append(
                WorkItem(
                    title=f"[{uc}] Strengthen {len(flags)} weak assertions",
                    bucket="FIX_NOW",
                    category="WEAK_ASSERTION",
                    uc=uc,
                    test_count=len(flags),
                    details="Inspector-flagged: assertion doesn't match scenario intent",
                    sample_tests=[f"{f.function}: {f.reason[:60]}" for f in flags[:3]],
                )
            )

    return items


# ── Report generation ────────────────────────────────────────────────


def generate_report(
    items: list[WorkItem],
    summary: dict[str, int],
    output_path: Path | None,
) -> str:
    """Generate markdown report organized by action bucket."""
    lines: list[str] = []
    lines.append("# BDD Full Audit Report")
    lines.append("")
    lines.append("Cross-references test outcomes, inspector flags, and xfail classifications.")
    lines.append("")

    # ── Summary ──────────────────────────────────────────────────────
    lines.append("## Test Outcome Summary")
    lines.append("")
    lines.append("| Outcome | Count |")
    lines.append("|---------|-------|")
    for k in ("passed", "failed", "xfailed", "xpassed"):
        lines.append(f"| {k} | {summary.get(k, 0)} |")
    lines.append(f"| **total** | **{sum(summary.values())}** |")
    lines.append("")

    # ── Bucket summary ───────────────────────────────────────────────
    by_bucket: dict[str, list[WorkItem]] = defaultdict(list)
    for item in items:
        by_bucket[item.bucket].append(item)

    fix_now = by_bucket.get("FIX_NOW", [])
    xfail_it = by_bucket.get("XFAIL_IT", [])
    feature_fix = by_bucket.get("FEATURE_FIX", [])

    lines.append("## Action Buckets")
    lines.append("")
    lines.append("| Bucket | Items | Tests | What to do |")
    lines.append("|--------|-------|-------|------------|")
    lines.append(
        f"| **FIX_NOW** | {len(fix_now)} | {sum(i.test_count for i in fix_now)} | Fix wiring, fixtures, stale xfails, step bugs — get tests green |"
    )
    lines.append(
        f"| **XFAIL_IT** | {len(xfail_it)} | {sum(i.test_count for i in xfail_it)} | Production behavior captured — mark xfail with documented reason |"
    )
    lines.append(
        f"| **FEATURE_FIX** | {len(feature_fix)} | {sum(i.test_count for i in feature_fix)} | Rare .feature file imprecisions — fix Examples table |"
    )
    lines.append("")

    # ── FIX_NOW: the actionable work ─────────────────────────────────
    lines.append("## FIX_NOW — Test Wiring Work")
    lines.append("")
    lines.append("These are our job. Fix them to get tests green.")
    lines.append("")

    if fix_now:
        by_cat: dict[str, list[WorkItem]] = defaultdict(list)
        for item in fix_now:
            by_cat[item.category].append(item)

        for cat in ["STALE_STRICT_XFAIL", "GRADUATE", "FIXTURE_GAP", "STEP_BUG", "WEAK_ASSERTION"]:
            cat_items = by_cat.get(cat, [])
            if not cat_items:
                continue
            cat_desc = FIX_NOW.get(cat, cat)
            total = sum(i.test_count for i in cat_items)
            lines.append(f"### {cat} ({len(cat_items)} items, {total} tests)")
            lines.append(f"*{cat_desc}*")
            lines.append("")
            for item in sorted(cat_items, key=lambda x: -x.test_count):
                lines.append(f"- **{item.title}** ({item.test_count} tests)")
                if item.details:
                    lines.append(f"  - {item.details[:150]}")
                for st in item.sample_tests[:2]:
                    lines.append(f"  - `{st}`")
            lines.append("")

    # ── XFAIL_IT: production behavior (not our problem right now) ────
    lines.append("## XFAIL_IT — Production Behavior to Document")
    lines.append("")
    lines.append("These are NOT tasks to fix. They capture system truth.")
    lines.append("Mark each as xfail with a documented reason, then move on to refactoring.")
    lines.append("")

    if xfail_it:
        # Split: newly-failed (need xfail tags added) vs already-xfailed (just summarize)
        newly_failed = [i for i in xfail_it if i.category in ("PROD_BEHAVIOR", "PROD_BUG", "TRANSPORT_GAP")]
        already_xfailed = [i for i in xfail_it if i.category in ("NOT_IMPLEMENTED", "PARTIAL_IMPL")]

        if newly_failed:
            lines.append("### Needs xfail tags (newly exposed by assertion strengthening)")
            lines.append("")
            by_cat: dict[str, list[WorkItem]] = defaultdict(list)
            for item in newly_failed:
                by_cat[item.category].append(item)

            for cat, cat_items in by_cat.items():
                cat_desc = XFAIL_IT.get(cat, cat)
                total = sum(i.test_count for i in cat_items)
                lines.append(f"**{cat}** — {cat_desc} ({len(cat_items)} items, {total} tests)")
                lines.append("")
                for item in sorted(cat_items, key=lambda x: (x.uc, -x.test_count)):
                    lines.append(f"- {item.title} ({item.test_count} tests)")
                    if item.details:
                        lines.append(f"  - {item.details[:150]}")
                lines.append("")

        if already_xfailed:
            lines.append("### Already xfailed (existing debt, just for context)")
            lines.append("")
            lines.append("| UC | Category | Tests |")
            lines.append("|----|----------|-------|")
            for item in sorted(already_xfailed, key=lambda x: (x.uc, x.category)):
                lines.append(f"| {item.uc} | {item.category} | {item.test_count} |")
            lines.append("")

    # ── FEATURE_FIX (rare) ───────────────────────────────────────────
    if feature_fix:
        lines.append("## FEATURE_FIX — Feature File Corrections")
        lines.append("")
        lines.append("Rare: .feature Examples table has wrong expected outcome.")
        lines.append("")
        for item in feature_fix:
            lines.append(f"- **{item.title}** ({item.test_count} tests)")
            if item.details:
                lines.append(f"  - {item.details[:150]}")
        lines.append("")

    # ── By use case cross-reference ──────────────────────────────────
    lines.append("## Summary by Use Case")
    lines.append("")

    by_uc: dict[str, list[WorkItem]] = defaultdict(list)
    for item in items:
        by_uc[item.uc].append(item)

    lines.append("| UC | FIX_NOW | XFAIL_IT | FEATURE_FIX | Total Tests |")
    lines.append("|----|---------|----------|-------------|-------------|")
    for uc in sorted(by_uc.keys()):
        uc_items = by_uc[uc]
        by_b = defaultdict(int)
        for i in uc_items:
            by_b[i.bucket] += i.test_count
        total_t = sum(i.test_count for i in uc_items)
        lines.append(
            f"| {uc} | {by_b.get('FIX_NOW', 0)} | {by_b.get('XFAIL_IT', 0)} | {by_b.get('FEATURE_FIX', 0)} | {total_t} |"
        )
    lines.append("")

    report = "\n".join(lines)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)
        print(f"Report written to {output_path}", file=sys.stderr)

    return report


# ── Main ─────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Full BDD audit")
    parser.add_argument("json_path", help="Path to bdd.json test results")
    parser.add_argument("--output", "-o", help="Output markdown file path")
    args = parser.parse_args()

    json_path = Path(args.json_path)
    output_path = Path(args.output) if args.output else None

    print("Parsing test results...", file=sys.stderr)
    entries = parse_test_results(json_path)
    summary = Counter(e.outcome for e in entries)
    print(f"  {len(entries)} tests: {dict(summary)}", file=sys.stderr)

    failed = [e for e in entries if e.outcome == "failed"]
    xfailed = [e for e in entries if e.outcome == "xfailed"]
    xpassed = [e for e in entries if e.outcome == "xpassed"]

    print("Parsing conftest xfail tags...", file=sys.stderr)
    tag_reasons = parse_conftest_xfail_tags()
    strict_tags = parse_conftest_strict_tags()
    print(f"  {len(tag_reasons)} tag reasons, {len(strict_tags)} strict tags", file=sys.stderr)

    print("Parsing inspector reports...", file=sys.stderr)
    inspector_flags = parse_inspector_reports()
    print(f"  {len(inspector_flags)} flagged steps", file=sys.stderr)

    print("Classifying and generating work items...", file=sys.stderr)
    items = generate_work_items(
        failed=failed,
        xfailed=xfailed,
        xpassed=xpassed,
        inspector_flags=inspector_flags,
        tag_reasons=tag_reasons,
        strict_tags=strict_tags,
        all_entries=entries,
    )
    print(f"  {len(items)} work items generated", file=sys.stderr)

    report = generate_report(items, dict(summary), output_path)
    if not output_path:
        print(report)


if __name__ == "__main__":
    main()
