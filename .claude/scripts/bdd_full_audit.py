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

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from bdd_audit_common import (  # noqa: E402
    CONFTEST_PATH,
    E2E_LEDGER_PATH,
    REPO_ROOT,
    cli_load_or_exit,
    extract_longrepr_e_line,
    extract_scenario_base,
    extract_transport,
    extract_uc,
    grade_base,
    load_bdd_artifact,
    load_e2e_rest_known_failure_bases,
    phase_dict,
    short_base,
    short_nodeid,
    tag_reasons,
)

PROJECT_ROOT = REPO_ROOT
INSPECTOR_DIR = PROJECT_ROOT / ".claude" / "reports" / "inspect-all-steps"


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
    "GRADUATE": "All present transports pass — remove xfail tag",
    "GRADUATE_CONFIRM": (
        "All present transports pass, but the set is single-transport or "
        "e2e_rest-only — confirm before removing xfail (e2e is environment-dependent)"
    ),
    "PARTIAL_XPASS": "Passes some transports — investigate remaining gaps before graduating",
    "FIXTURE_GAP": "Strengthened assertion exposes missing test fixture data — fix factory",
    "STEP_BUG": "Step implementation has a bug — fix step code",
    "WEAK_ASSERTION": "Inspector-flagged: assertion doesn't match scenario intent",
}

# XFAIL_IT: production behavior, document and move on
XFAIL_IT = {
    "PROD_BEHAVIOR": "Production behavior differs from spec — xfail with reason",
    "PROD_BUG": "Production code crashes/errors — xfail with reason",
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


def _test_entry_from_raw(t: dict) -> TestEntry:
    """Build a TestEntry from one bdd.json test dict (null-safe longrepr)."""
    longrepr = phase_dict(t, "call").get("longrepr") or ""
    return TestEntry(
        nodeid=t["nodeid"],
        outcome=t["outcome"],
        keywords=t.get("keywords", []),
        error=extract_longrepr_e_line(longrepr),
        longrepr=longrepr[:1000],
    )


def parse_test_results(json_path: Path) -> list[TestEntry]:
    """Parse bdd.json into TestEntry list via shared ``load_bdd_artifact``."""
    loaded = load_bdd_artifact(json_path)
    return [_test_entry_from_raw(t) for t in loaded.tests]


def parse_inspector_reports() -> list[InspectorFlag]:
    """Parse all inspector markdown reports for flagged steps."""
    flags: list[InspectorFlag] = []
    if not INSPECTOR_DIR.exists():
        return flags

    for md_file in INSPECTOR_DIR.glob("*.md"):
        content = md_file.read_text(encoding="utf-8", errors="replace")
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


def classify_failure(entry: TestEntry) -> tuple[str, str, str]:
    """Classify a failed test. Returns (bucket, category, detail).

    bucket: FIX_NOW | XFAIL_IT | FEATURE_FIX

    STALE_STRICT_XFAIL keys on ``XPASS(strict)`` in longrepr (not a dead
    conftest-tag scan).
    """
    error = entry.error
    longrepr = entry.longrepr

    # ── FIX_NOW: stale strict xfail (reachable via XPASS(strict) in longrepr) ─
    if "XPASS(strict)" in longrepr or "XPASS(strict)" in error:
        return "FIX_NOW", "STALE_STRICT_XFAIL", "Remove strict xfail tag"

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
    if "error_code" in error:
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # Context echo failures — production doesn't echo context back
    if "context" in error.lower() and "echo" in error.lower():
        return "XFAIL_IT", "PROD_BEHAVIOR", error

    # ── FIX_NOW: step implementation bug (last resort) ───────────────
    if "AssertionError" in longrepr or "assert" in error.lower():
        return "FIX_NOW", "STEP_BUG", error

    return "FIX_NOW", "STEP_BUG", error or "Unknown failure"


def classify_xfail(entry: TestEntry, tag_reasons_map: dict[str, str]) -> tuple[str, str, str]:
    """Classify an xfailed test. Returns (bucket, category, reason).

    All xfails are XFAIL_IT by definition — they're already marked.
    """
    tags = sorted(k for k in entry.keywords if k.startswith("T-"))

    if "partition" in entry.nodeid or "boundary" in entry.nodeid:
        reason = tag_reasons_map.get(tags[0], "Partition/boundary xfail") if tags else "Partition/boundary xfail"
        return "XFAIL_IT", "PARTIAL_IMPL", reason

    reason = tag_reasons_map.get(tags[0], "Feature not implemented") if tags else "Feature not implemented"
    return "XFAIL_IT", "NOT_IMPLEMENTED", reason


class ClassifiedXpass(NamedTuple):
    """Named xpass classification so callers cannot swap adjacent str fields."""

    bucket: str
    category: str
    detail: str
    present_count: int


def classify_xpass(
    entry: TestEntry,
    all_entries: list[TestEntry],
    *,
    force_confirm: bool = False,
    ledger_bases: set[str] | None = None,
) -> ClassifiedXpass:
    """Classify an xpassed test.

    FIX_NOW either way: GRADUATE when every transport *present for this base*
    passed (remove the stale xfail tag); GRADUATE_CONFIRM when that present
    set is a single transport or e2e_rest-only, or when the artifact lacks
    e2e_rest entirely; PARTIAL_XPASS when only a subset passed — including
    ``mixed_examples`` (present transports, none passing after aggregate).
    """
    base = extract_scenario_base(entry.nodeid)
    ledger = ledger_bases or set()
    grade = grade_base(
        base,
        ((e.nodeid, e.outcome) for e in all_entries),
        force_confirm=force_confirm,
        ledger_member=base in ledger,
    )
    if grade.bucket == "confirm":
        return ClassifiedXpass(
            bucket="FIX_NOW",
            category="GRADUATE_CONFIRM",
            detail=(f"All {grade.present_count} present transports pass (needs confirmation): {sorted(grade.passing)}"),
            present_count=grade.present_count,
        )
    if grade.bucket == "graduate":
        return ClassifiedXpass(
            bucket="FIX_NOW",
            category="GRADUATE",
            detail=f"All {grade.present_count} present transports pass: {sorted(grade.passing)}",
            present_count=grade.present_count,
        )
    # mixed_examples and partial both land in PARTIAL_XPASS
    return ClassifiedXpass(
        bucket="FIX_NOW",
        category="PARTIAL_XPASS",
        detail=f"Passes: {sorted(grade.passing)}, missing: {sorted(grade.missing)}",
        present_count=grade.present_count,
    )


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
    tag_reasons_map: dict[str, str],
    all_entries: list[TestEntry],
    *,
    force_confirm: bool = False,
    ledger_bases: set[str] | None = None,
) -> list[WorkItem]:
    """Generate actionable work items grouped by 3 buckets."""
    items: list[WorkItem] = []

    # ── 1. Failed tests → classify into buckets ──────────────────────
    # Group by (UC, bucket, category) then deduplicate across transports
    fail_groups: dict[tuple[str, str, str], list[TestEntry]] = defaultdict(list)

    for entry in failed:
        uc = extract_uc(entry.nodeid)
        bucket, cat, _ = classify_failure(entry)
        fail_groups[(uc, bucket, cat)].append(entry)

    for (uc, bucket, cat), entries in fail_groups.items():
        # Deduplicate: group by scenario base (strip transport)
        by_scenario: dict[str, list[TestEntry]] = defaultdict(list)
        for e in entries:
            by_scenario[extract_scenario_base(e.nodeid)].append(e)

        for base, group in by_scenario.items():
            transports = {t for e in group if (t := extract_transport(e.nodeid)) is not None}
            rep_error = group[0].error
            transport_note = f" [{','.join(sorted(transports))}]" if transports else ""
            scenario_name = short_base(base)

            items.append(
                WorkItem(
                    title=f"[{uc}] {scenario_name[:50]}{transport_note}",
                    bucket=bucket,
                    category=cat,
                    uc=uc,
                    test_count=len(group),
                    details=rep_error[:150],
                    sample_tests=[short_nodeid(e.nodeid) for e in group[:3]],
                )
            )

    # ── 2. Xpassed tests → FIX_NOW (graduate when all present transports pass) ─
    grad_groups: dict[tuple[str, str, int], list[TestEntry]] = defaultdict(list)
    for entry in xpassed:
        classified = classify_xpass(entry, all_entries, force_confirm=force_confirm, ledger_bases=ledger_bases)
        grad_groups[(classified.category, classified.detail, classified.present_count)].append(entry)

    for (cat, detail, present_n), entries in grad_groups.items():
        uc = extract_uc(entries[0].nodeid) if entries else "MIXED"
        if cat == "GRADUATE_CONFIRM":
            title = f"Graduate needs confirmation (all {present_n} present): {uc}"
        elif cat == "GRADUATE":
            title = f"Graduate (all {present_n} present): {uc}"
        else:
            title = f"Partial xpass (gaps remain): {uc}"
        items.append(
            WorkItem(
                title=title,
                bucket="FIX_NOW",
                category=cat,
                uc=uc,
                test_count=len(entries),
                details=detail,
                sample_tests=[short_nodeid(e.nodeid) for e in entries[:5]],
            )
        )

    # ── 3. Xfailed tests → XFAIL_IT (already marked, just summarize) ─
    xfail_groups: dict[tuple[str, str], list[TestEntry]] = defaultdict(list)
    for entry in xfailed:
        uc = extract_uc(entry.nodeid)
        _, cat, _ = classify_xfail(entry, tag_reasons_map)
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
                sample_tests=[short_nodeid(e.nodeid) for e in entries[:3]],
            )
        )

    # ── 4. Inspector flags → FIX_NOW (weak assertions) ───────────────
    if inspector_flags:
        by_uc: dict[str, list[InspectorFlag]] = defaultdict(list)
        for flag in inspector_flags:
            by_uc[extract_uc(flag.source_file)].append(flag)

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

        # Single source of truth: FIX_NOW insertion order (no parallel list).
        for cat in FIX_NOW:
            cat_items = by_cat.get(cat, [])
            if not cat_items:
                continue
            cat_desc = FIX_NOW[cat]
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
        newly_failed = [i for i in xfail_it if i.category in ("PROD_BEHAVIOR", "PROD_BUG")]
        already_xfailed = [i for i in xfail_it if i.category in ("NOT_IMPLEMENTED", "PARTIAL_IMPL")]

        if newly_failed:
            lines.append("### Needs xfail tags (newly exposed by assertion strengthening)")
            lines.append("")
            newly_by_cat: dict[str, list[WorkItem]] = defaultdict(list)
            for item in newly_failed:
                newly_by_cat[item.category].append(item)

            for cat, cat_items in newly_by_cat.items():
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
        by_b: dict[str, int] = defaultdict(int)
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
    parser.add_argument(
        "--conftest",
        help="Path to conftest.py",
        default=str(CONFTEST_PATH),
    )
    parser.add_argument(
        "--e2e-ledger",
        help="Path to e2e_rest_known_failures.txt",
        default=str(E2E_LEDGER_PATH),
    )
    args = parser.parse_args()

    json_path = Path(args.json_path)
    output_path = Path(args.output) if args.output else None
    conftest_path = Path(args.conftest)

    print("Parsing test results...", file=sys.stderr)
    loaded = cli_load_or_exit(json_path)
    entries = [_test_entry_from_raw(t) for t in loaded.tests]
    summary = Counter(e.outcome for e in entries)
    print(f"  Parsed {len(entries)} tests: {dict(summary)}", file=sys.stderr)

    failed = [e for e in entries if e.outcome == "failed"]
    xfailed = [e for e in entries if e.outcome == "xfailed"]
    xpassed = [e for e in entries if e.outcome == "xpassed"]
    force_confirm = loaded.force_confirm

    print("Parsing conftest xfail tags...", file=sys.stderr)
    tag_reasons_map = tag_reasons(conftest_path) if conftest_path.exists() else {}
    print(f"  {len(tag_reasons_map)} tag reasons", file=sys.stderr)

    ledger_bases = load_e2e_rest_known_failure_bases(Path(args.e2e_ledger))

    print("Parsing inspector reports...", file=sys.stderr)
    inspector_flags = parse_inspector_reports()
    print(f"  {len(inspector_flags)} flagged steps", file=sys.stderr)

    print("Classifying and generating work items...", file=sys.stderr)
    items = generate_work_items(
        failed=failed,
        xfailed=xfailed,
        xpassed=xpassed,
        inspector_flags=inspector_flags,
        tag_reasons_map=tag_reasons_map,
        all_entries=entries,
        force_confirm=force_confirm,
        ledger_bases=ledger_bases,
    )
    print(f"  {len(items)} work items generated", file=sys.stderr)

    report = generate_report(items, dict(summary), output_path)
    if not output_path:
        print(report)


if __name__ == "__main__":
    main()
