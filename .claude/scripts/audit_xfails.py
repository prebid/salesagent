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
    STALE             - Marked xfail but actually passes on every present transport
    STALE_CONFIRM     - All present transports pass, but single-/e2e_rest-only —
                        needs confirmation before removing tags
    PARTIAL_PASS      - Passes on some transports, fails on others
    UNCLASSIFIED      - Doesn't match any known pattern

Graduation criteria (STALE → remove xfail):
    1. Every transport *present for that scenario base* passed/xpassed
       (a2a/mcp/rest, plus e2e_rest when in the result set; not a hardcoded
       four-transport universe — #1417 dropped impl from BDD parametrization)
    2. Present set is not single-transport / e2e_rest-only (those are
       STALE_CONFIRM — confirm before removing tags; many e2e xfails are
       still strict=True in conftest)
    3. Artifact must include e2e_rest somewhere; if the whole artifact lacks
       e2e_rest (tox bdd half-run), every graduation routes to STALE_CONFIRM

PREMATURE_XFAIL matching uses crash path+lineno against live step files.
Container-rooted crash paths (``root: "/app"``) are remapped onto the host
repo root. Run this audit against a bdd.json produced from the *current*
tree; a stale artifact's linenos drift outside step ranges and reclassify
as PRODUCTION_GAP (with an explicit line-drift warning when a crash hits a
known step file outside every scanned range).

Xfail *reasons* are read from ``setup.crash.message`` / ``call.crash.message``
(pytest-json-report does not emit ``wasxfail``).
"""

from __future__ import annotations

import ast
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from bdd_audit_common import (  # noqa: E402
    CONFTEST_PATH,
    E2E_LEDGER_PATH,
    REPO_ROOT,
    STEPS_DIR,
    TagReason,
    cli_load_or_exit,
    extract_scenario_base,
    extract_transport,
    extract_uc,
    grade_base,
    load_bdd_artifact,
    load_e2e_rest_known_failure_bases,
    parse_conftest_xfail_tags,
    phase_dict,
    remap_container_path,
    short_base,
)

# Module-scope taxonomy — single owner for by-UC columns + stderr SUMMARY.
# (Sibling ``bdd_full_audit.FIX_NOW`` is also module-scope for the same reason.)
CATEGORY_DESC: dict[str, str] = {
    "PRODUCTION_GAP": "Feature in Gherkin, not implemented in src/",
    "TRANSPORT_GAP": "Works on some transports, param not forwarded",
    "HARNESS_GAP": "Harness env not wired for this scenario",
    "PARTIAL_IMPL": "Partition/boundary — some values pass, others fail",
    "MISSING_STEP": "No step definition exists for a Gherkin step",
    "PREMATURE_XFAIL": "Step calls pytest.xfail() before production code",
    "STALE": "Passes all present transports — should graduate",
    "STALE_CONFIRM": "All present pass, but single-/e2e_rest-only — needs confirmation",
    "PARTIAL_PASS": "Passes some transports, not all",
    "UNCLASSIFIED": "No matching pattern found",
}
XFAIL_CATEGORIES: tuple[str, ...] = tuple(CATEGORY_DESC.keys())
# by-UC markdown columns: (category, header abbrev) derived from CATEGORY_DESC order
# minus PARTIAL_PASS (xpassed-only; not an xfailed category column).
BY_UC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("PRODUCTION_GAP", "PROD_GAP"),
    ("TRANSPORT_GAP", "TRANSPORT"),
    ("HARNESS_GAP", "HARNESS"),
    ("PARTIAL_IMPL", "PARTIAL"),
    ("MISSING_STEP", "MISSING"),
    ("PREMATURE_XFAIL", "PREMATURE"),
    ("STALE", "STALE"),
    ("STALE_CONFIRM", "STALE_CONFIRM"),
    ("UNCLASSIFIED", "UNCLASS"),
)

# ── Data classes ──────────────────────────────────────────────────────


class CrashInfo(TypedDict, total=False):
    """pytest-json-report crash slice under setup/call (path + lineno + message)."""

    path: str
    lineno: int
    message: str


class PhaseResult(TypedDict, total=False):
    """pytest-json-report phase dict; ``crash`` / ``longrepr`` used by helpers."""

    crash: CrashInfo
    longrepr: str
    outcome: str


class TestReportEntry(TypedDict, total=False):
    """One pytest-json-report test entry (container for CrashInfo/PhaseResult)."""

    nodeid: str
    outcome: str
    keywords: list[str]
    setup: PhaseResult
    call: PhaseResult


@dataclass
class XfailEntry:
    """A single xfailed test with classification."""

    nodeid: str
    scenario_base: str  # nodeid without transport suffix
    transport: str | None  # a2a, mcp, rest, e2e_rest, legacy impl, or None
    category: str = "UNCLASSIFIED"
    reason: str = ""
    xfail_source: str = ""  # "conftest:tag", "conftest:auto", "step:funcname"
    tags: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PrematureXfail:
    """A step function that calls pytest.xfail() before production code."""

    name: str
    path: Path
    lineno: int
    end_lineno: int


@dataclass
class AuditReport:
    """Full audit report."""

    total_xfailed: int = 0
    total_xpassed: int = 0
    entries: list[XfailEntry] = field(default_factory=list)
    xpassed_entries: list[XfailEntry] = field(default_factory=list)
    by_category: dict[str, list[XfailEntry]] = field(default_factory=lambda: defaultdict(list))
    by_uc: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    partial_passing: dict[str, set[str]] = field(default_factory=dict)
    partial_missing: dict[str, set[str]] = field(default_factory=dict)
    line_drift_warnings: list[str] = field(default_factory=list)


# ── Conftest parser ───────────────────────────────────────────────────
# Canonical implementation lives in bdd_audit_common; re-export for callers
# and tests that import from this module.


# parse_conftest_xfail_tags imported from bdd_audit_common above.


# ── Step-level xfail detector ────────────────────────────────────────


def find_premature_xfails(steps_dir: Path) -> list[PrematureXfail]:
    """Find step functions that call pytest.xfail() before any production code.

    Returns location-aware records so classifiers can match pytest-json-report
    ``setup.crash`` / ``call.crash`` path+lineno to the enclosing step (step
    function names never appear in json-report entries).
    """
    premature: list[PrematureXfail] = []

    for py_file in steps_dir.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        resolved = py_file.resolve()
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
                # Skip string docstrings only (ast.Str removed in Python 3.12)
                if (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    continue
                # Skip imports
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    continue
                # Check for pytest.xfail() call
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    call = stmt.value
                    if isinstance(call.func, ast.Attribute):
                        if call.func.attr == "xfail":
                            end = getattr(node, "end_lineno", None) or node.lineno
                            premature.append(
                                PrematureXfail(
                                    name=node.name,
                                    path=resolved,
                                    lineno=node.lineno,
                                    end_lineno=end,
                                )
                            )
                            break
                # Any other statement means it's not premature
                break

    return premature


def _iter_phase_dicts(test: TestReportEntry) -> Iterator[PhaseResult]:
    """Yield setup/call phase dicts in canonical order with dict-guarding."""
    for phase in ("setup", "call"):
        ph = phase_dict(test, phase)
        if ph:
            yield cast(PhaseResult, ph)


def _crash_reason_text(test: TestReportEntry) -> str:
    """Read xfail/failure reason from setup/call crash.message (or longrepr).

    pytest-json-report does not emit ``wasxfail``; real artifacts carry the
    reason on ``setup.crash.message`` / ``call.crash.message``.
    """
    for ph in _iter_phase_dicts(test):
        crash_raw = ph.get("crash") or {}
        if isinstance(crash_raw, dict):
            message = crash_raw.get("message")
            if message:
                return str(message)
        longrepr = ph.get("longrepr")
        if longrepr:
            return str(longrepr)
    return ""


def _iter_crash_locations(
    test: TestReportEntry,
    *,
    artifact_root: str | None = None,
    host_root: Path | None = None,
) -> Iterator[tuple[Path, int]]:
    """Yield remapped ``(path, lineno)`` for setup/call crash sites on a test.

    Owns the shared phase loop, crash-dict guarding, and path/lineno
    normalization used by both premature matching and line-drift warnings.
    Container-rooted paths (artifact ``root``, e.g. ``/app``) are remapped
    onto ``host_root`` so host step scans match.
    """
    host = host_root or Path.cwd()
    for ph in _iter_phase_dicts(test):
        crash_raw = ph.get("crash") or {}
        if not isinstance(crash_raw, dict):
            continue
        crash: CrashInfo = cast(CrashInfo, crash_raw)
        cpath = crash.get("path")
        clineno = crash.get("lineno")
        if not cpath or clineno is None:
            continue
        try:
            resolved = remap_container_path(
                str(cpath),
                artifact_root=artifact_root,
                host_root=host,
            )
            yield resolved, int(clineno)
        except (OSError, TypeError, ValueError):
            continue


def match_premature_by_crash(
    test: TestReportEntry,
    premature_xfails: Sequence[PrematureXfail],
    *,
    artifact_root: str | None = None,
    host_root: Path | None = None,
) -> PrematureXfail | None:
    """Map setup/call crash path+lineno to an enclosing premature step."""
    if not premature_xfails:
        return None
    for resolved, line in _iter_crash_locations(test, artifact_root=artifact_root, host_root=host_root):
        for step in premature_xfails:
            if resolved == step.path and step.lineno <= line <= step.end_lineno:
                return step
    return None


def crash_line_drift_warnings(
    test: TestReportEntry,
    premature_xfails: Sequence[PrematureXfail],
    *,
    artifact_root: str | None = None,
    host_root: Path | None = None,
) -> list[str]:
    """Warn when a crash hits a known premature step file outside every range.

    That pattern usually means ``bdd.json`` linenos drifted from the live tree
    (stale artifact). Matching is only meaningful when the json is co-current
    with the scanned step files.
    """
    if not premature_xfails:
        return []
    known_paths = {step.path for step in premature_xfails}
    warnings: list[str] = []
    for resolved, line in _iter_crash_locations(test, artifact_root=artifact_root, host_root=host_root):
        if resolved not in known_paths:
            continue
        in_any = any(step.path == resolved and step.lineno <= line <= step.end_lineno for step in premature_xfails)
        if not in_any:
            warnings.append(
                f"line-drift/stale-json?: {resolved}:{line} is in a premature step file "
                "but outside every scanned step range — regenerate bdd.json from the current tree"
            )
    return warnings


# ── JSON test result parser ───────────────────────────────────────────


class ParsedTestResults(NamedTuple):
    """Named parse so xfailed/xpassed/all_tests slots cannot be swapped."""

    xfailed: list[TestReportEntry]
    xpassed: list[TestReportEntry]
    all_tests: list[TestReportEntry]
    artifact_root: str | None


def parse_test_results(json_path: Path) -> ParsedTestResults:
    """Parse BDD JSON results via the shared ``load_bdd_artifact`` seam.

    Empty-artifact refusal + census live in ``load_bdd_artifact`` (same path
    ``main`` uses).
    """
    loaded = load_bdd_artifact(json_path)
    all_tests: list[TestReportEntry] = cast(list[TestReportEntry], loaded.tests)
    xfailed = [t for t in all_tests if t["outcome"] == "xfailed"]
    xpassed = [t for t in all_tests if t["outcome"] == "xpassed"]
    return ParsedTestResults(
        xfailed=xfailed,
        xpassed=xpassed,
        all_tests=all_tests,
        artifact_root=loaded.artifact_root,
    )


def extract_tags(test_entry: TestReportEntry) -> set[str]:
    """Extract BDD tags from test entry keywords."""
    tags = set()
    for kw in test_entry.get("keywords", []):
        if isinstance(kw, str) and (kw.startswith("T-") or kw.startswith("BR-")):
            tags.add(kw)
    return tags


# ── Classifier ────────────────────────────────────────────────────────


def classify_xfail(
    test: TestReportEntry,
    tag_map: Mapping[str, TagReason],
    premature_xfails: Sequence[PrematureXfail],
    *,
    artifact_root: str | None = None,
    host_root: Path | None = None,
) -> XfailEntry:
    """Classify a single xfailed test."""
    nodeid = test["nodeid"]
    transport = extract_transport(nodeid)
    base = extract_scenario_base(nodeid)
    tags = extract_tags(test)
    reason_text = _crash_reason_text(test)

    entry = XfailEntry(
        nodeid=nodeid,
        scenario_base=base,
        transport=transport,
        tags=tags,
    )

    # Priority 0: Premature step-level pytest.xfail() (AST scan)
    # Match via setup/call crash path+lineno → enclosing step. pytest-json-report
    # does not emit step function names into crash.message/longrepr/nodeid.
    matched = match_premature_by_crash(test, premature_xfails, artifact_root=artifact_root, host_root=host_root)
    if matched is not None:
        entry.category = "PREMATURE_XFAIL"
        entry.reason = f"step {matched.name} calls pytest.xfail() before production code"
        entry.xfail_source = f"step:{matched.name}"
        return entry

    # Priority 1: Missing step definition (auto-xfail from pytest hook).
    # Real pytest-bdd messages carry ``StepDefinitionNotFoundError``; the
    # human phrase "Step definition not found" never matches live wording
    # ("is not found") and is intentionally not a classifier arm.
    if "StepDefinitionNotFoundError" in reason_text:
        entry.category = "MISSING_STEP"
        entry.reason = reason_text
        entry.xfail_source = "conftest:auto"
        return entry

    # Priority 2: harness not wired (live conftest wordings).
    # Covers ``No harness wired for {uc}``, ``… harness not yet wired …``,
    # ``UC harness not wired``, etc. Require both tokens so unrelated reasons
    # mentioning only "harness" or only "wired" do not mis-route.
    reason_lower = reason_text.lower()
    if "harness" in reason_lower and "wired" in reason_lower:
        entry.category = "HARNESS_GAP"
        entry.reason = reason_text
        entry.xfail_source = "conftest:auto"
        return entry

    # Priority 3: Match against conftest tag map (deterministic tag order)
    for tag in sorted(tags):
        if tag in tag_map:
            tagged = tag_map[tag]
            entry.reason = tagged.reason
            entry.xfail_source = f"conftest:tag:{tag}"
            if tagged.mechanism == "transport_gap":
                entry.category = "TRANSPORT_GAP"
            elif tagged.mechanism == "harness_gap":
                entry.category = "HARNESS_GAP"
            elif tagged.mechanism == "partial_impl":
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
        # Infer from tag content (sorted for deterministic choice)
        sample_tag = next(t for t in sorted(tags) if t.startswith("T-UC-"))
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
        entry.xfail_source = "conftest:inferred:tag-prefix"
        return entry

    # Priority 5: Check crash-message reason text for clues
    if reason_text:
        if "rest" in reason_lower and ("endpoint" in reason_lower or "drops" in reason_lower):
            entry.category = "TRANSPORT_GAP"
            entry.reason = reason_text
            entry.xfail_source = "conftest:reason"
            return entry
        if "mcp" in reason_lower and "wrapper" in reason_lower:
            entry.category = "TRANSPORT_GAP"
            entry.reason = reason_text
            entry.xfail_source = "conftest:reason"
            return entry
        if any(kw in reason_lower for kw in ["not implemented", "gap", "spec-production", "not yet"]):
            entry.category = "PRODUCTION_GAP"
            entry.reason = reason_text
            entry.xfail_source = "conftest:reason"
            return entry
        if any(kw in reason_lower for kw in ["harness", "env", "wired"]):
            entry.category = "HARNESS_GAP"
            entry.reason = reason_text
            entry.xfail_source = "conftest:reason"
            return entry
        # Has a reason but doesn't match known patterns
        entry.category = "PRODUCTION_GAP"  # Default for tagged xfails with reasons
        entry.reason = reason_text
        entry.xfail_source = "conftest:reason"
        return entry

    # Priority 6: Unclassified
    entry.category = "UNCLASSIFIED"
    entry.reason = "no crash-message reason and no matching tag"
    return entry


class XpassBuckets(NamedTuple):
    """Named xpass buckets so callers cannot swap graduate/confirm/partial."""

    graduate: set[str]
    confirm: set[str]
    partial_passing: dict[str, set[str]]
    partial_missing: dict[str, set[str]]


def classify_xpassed(
    all_tests: list[TestReportEntry],
    *,
    force_confirm: bool = False,
    ledger_bases: set[str] | None = None,
) -> XpassBuckets:
    """Classify xpassed scenario bases into graduate / confirm / partial.

    Graduation is relative to transports *present for that base* across the
    full result set (not a hardcoded impl/a2a/mcp/rest universe). Single-
    transport and e2e_rest-only present sets land in ``confirm``
    (STALE_CONFIRM) rather than firm graduation. When the artifact lacks
    e2e_rest entirely (``force_confirm``), every graduation routes to confirm.
    Bases in the e2e_rest known-failures ledger without a present e2e_rest row
    also confirm (ledger markers are outside conftest tags — via
    ``grade_base(..., ledger_member=...)``).

    ``mixed_examples`` bases (present transports, none passing after aggregate)
    land in the partial bucket so listing counts match.

    Failed outcomes participate in the grade scan (parity with bdd_full_audit)
    so a base mixing failed + xpassed does not ignore the failed transport.

    Returns ``XpassBuckets(graduate, confirm, partial_passing, partial_missing)``.
    """
    xpassed_bases = {extract_scenario_base(t["nodeid"]) for t in all_tests if t.get("outcome") == "xpassed"}
    graduate: set[str] = set()
    confirm: set[str] = set()
    partial_passing: dict[str, set[str]] = {}
    partial_missing: dict[str, set[str]] = {}
    nodeid_outcomes = [(t["nodeid"], t["outcome"]) for t in all_tests]
    ledger = ledger_bases or set()
    for base in xpassed_bases:
        grade = grade_base(
            base,
            nodeid_outcomes,
            force_confirm=force_confirm,
            ledger_member=base in ledger,
        )
        if grade.bucket == "confirm":
            confirm.add(base)
        elif grade.bucket == "graduate":
            graduate.add(base)
        elif grade.bucket == "partial":
            partial_passing[base] = grade.passing
            partial_missing[base] = grade.missing
    return XpassBuckets(
        graduate=graduate,
        confirm=confirm,
        partial_passing=partial_passing,
        partial_missing=partial_missing,
    )


# ── Report generator ──────────────────────────────────────────────────


def _render_base_list(lines: list[str], entries: Sequence[XfailEntry], header: str) -> None:
    """Append a deduped scenario-base bullet list under ``header``."""
    if not entries:
        return
    lines.extend(["", header, ""])
    seen: set[str] = set()
    for entry in sorted(entries, key=lambda x: x.scenario_base):
        if entry.scenario_base in seen:
            continue
        seen.add(entry.scenario_base)
        lines.append(f"- {short_base(entry.scenario_base)}")


def generate_report(report: AuditReport, output_path: Path | None = None) -> str:
    """Generate markdown report from audit results."""
    lines = [
        "# BDD Xfail Audit Report",
        "",
        "Generated from test results. Deterministic classification.",
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

    category_desc = CATEGORY_DESC
    xfail_categories = XFAIL_CATEGORIES
    by_uc_columns = BY_UC_COLUMNS

    for cat in xfail_categories:
        desc = category_desc[cat]
        entries = report.by_category.get(cat, [])
        pct = len(entries) / report.total_xfailed * 100 if report.total_xfailed else 0
        lines.append(f"| {cat} | {len(entries)} | {pct:.0f}% | {desc} |")

    lines.extend(["", "### By use case", ""])
    lines.append("| UC | " + " | ".join(abbrev for _, abbrev in by_uc_columns) + " | Total |")
    lines.append("|" + "|".join(["---"] * (len(by_uc_columns) + 2)) + "|")

    all_ucs = sorted(report.by_uc.keys())
    for uc in all_ucs:
        counts = report.by_uc[uc]
        total = sum(counts.values())
        cells = " | ".join(str(counts.get(cat, 0)) for cat, _ in by_uc_columns)
        lines.append(f"| {uc} | {cells} | {total} |")

    # Xpassed section
    lines.extend(
        [
            "",
            "## Xpassed Tests (marked xfail but pass)",
            "",
            f"- **All present transports (graduation candidates)**: {len([e for e in report.xpassed_entries if e.category == 'STALE'])}",
            f"- **Needs confirmation (single-/e2e_rest-only)**: {len([e for e in report.xpassed_entries if e.category == 'STALE_CONFIRM'])}",
            f"- **Partial pass (keep investigating)**: {len([e for e in report.xpassed_entries if e.category == 'PARTIAL_PASS'])}",
        ]
    )

    stale = [e for e in report.xpassed_entries if e.category == "STALE"]
    _render_base_list(lines, stale, "### Graduation candidates (all present transports pass)")

    stale_confirm = [e for e in report.xpassed_entries if e.category == "STALE_CONFIRM"]
    _render_base_list(
        lines,
        stale_confirm,
        "### Graduation needs confirmation (single-/e2e_rest-only)",
    )

    # Partial pass details — list from the same dict the counts use
    partial = [e for e in report.xpassed_entries if e.category == "PARTIAL_PASS"]
    if partial:
        lines.extend(["", "### Partial pass (some transports only)", ""])
        listed_bases = set(report.partial_passing.keys()) | set(report.partial_missing.keys())
        # Ensure every PARTIAL_PASS base appears (mixed_examples may have empty passing)
        for e in partial:
            listed_bases.add(e.scenario_base)
        for base in sorted(listed_bases):
            transports = report.partial_passing.get(base, set())
            missing = sorted(report.partial_missing.get(base, set()))
            lines.append(f"- {short_base(base)} — passes: {sorted(transports)}, missing: {missing}")
    if report.line_drift_warnings:
        lines.extend(["", "## Line-drift / stale bdd.json warnings", ""])
        for warning in report.line_drift_warnings:
            lines.append(f"- {warning}")

    # Actionable summary
    lines.extend(
        [
            "",
            "## Actionable Summary",
            "",
            "### Immediate actions",
            f"1. **Graduate {len(stale)} stale scenarios** — remove xfail tags from conftest (all present transports pass with strong assertions)",
            f"2. **Confirm {len(stale_confirm)} single-/e2e_rest-only graduations** — do not auto-remove tags; e2e is environment-dependent",
            f"3. **Fix {len(report.by_category.get('PREMATURE_XFAIL', []))} premature xfails** — step calls pytest.xfail() before production code",
            "",
            "### Tracked debt",
            f"4. **{len(report.by_category.get('PRODUCTION_GAP', []))} production gaps** — legitimate, needs src/ implementation",
            f"5. **{len(report.by_category.get('TRANSPORT_GAP', []))} transport gaps** — REST/MCP/A2A wrappers missing params",
            f"6. **{len(report.by_category.get('HARNESS_GAP', []))} harness gaps** — test env not wired, fixable in tests/",
            f"7. **{len(report.by_category.get('PARTIAL_IMPL', []))} partial implementations** — partition/boundary values vary",
            f"8. **{len(report.by_category.get('MISSING_STEP', []))} missing steps** — step definitions needed",
            f"9. **{len(report.by_category.get('UNCLASSIFIED', []))} unclassified** — need manual review",
        ]
    )

    text = "\n".join(lines)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text)
        print(f"Report written to {output_path}", file=sys.stderr)
    return text


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Deterministic BDD xfail audit")
    parser.add_argument("json_path", help="Path to bdd.json test results")
    parser.add_argument("--output", "-o", help="Output markdown path", default=None)
    parser.add_argument(
        "--conftest",
        help="Path to conftest.py",
        default=str(CONFTEST_PATH),
    )
    parser.add_argument(
        "--steps-dir",
        help="Path to step definitions",
        default=str(STEPS_DIR),
    )
    parser.add_argument(
        "--repo-root",
        help="Host repo root for remapping container crash paths (default: inferred)",
        default=str(REPO_ROOT),
    )
    parser.add_argument(
        "--e2e-ledger",
        help="Path to e2e_rest_known_failures.txt",
        default=str(E2E_LEDGER_PATH),
    )
    args = parser.parse_args()

    json_path = Path(args.json_path)
    conftest_path = Path(args.conftest)
    steps_dir = Path(args.steps_dir)
    host_root = Path(args.repo_root)
    output_path = Path(args.output) if args.output else None

    # Step 1: Parse conftest xfail tags
    print("Parsing conftest.py xfail tags...", file=sys.stderr)
    tag_map = parse_conftest_xfail_tags(conftest_path)
    print(f"  Found {len(tag_map)} tag → reason mappings", file=sys.stderr)

    # Step 2: Find premature xfails in step functions
    print("Scanning step functions for premature xfails...", file=sys.stderr)
    premature_xfails = find_premature_xfails(steps_dir)
    print(
        f"  Found {len(premature_xfails)} premature xfail functions: {sorted(p.name for p in premature_xfails)}",
        file=sys.stderr,
    )

    # Step 3: Parse test results (shared empty guard + census; CLI exit edge)
    print(f"Parsing {json_path}...", file=sys.stderr)
    loaded = cli_load_or_exit(json_path)
    all_tests = cast(list[TestReportEntry], loaded.tests)
    xfailed_tests = [t for t in all_tests if t["outcome"] == "xfailed"]
    xpassed_tests = [t for t in all_tests if t["outcome"] == "xpassed"]
    print(
        f"  Parsed {len(all_tests)} tests ({len(xfailed_tests)} xfailed, {len(xpassed_tests)} xpassed)",
        file=sys.stderr,
    )

    artifact_root = loaded.artifact_root
    if artifact_root:
        print(
            f"  Artifact root: {artifact_root} (crash paths remapped onto {host_root})",
            file=sys.stderr,
        )
    force_confirm = loaded.force_confirm

    ledger_bases = load_e2e_rest_known_failure_bases(Path(args.e2e_ledger))

    # Step 4: Classify xpassed tests (needs full suite for present-transport set)
    print("Classifying xpassed tests...", file=sys.stderr)
    buckets = classify_xpassed(all_tests, force_confirm=force_confirm, ledger_bases=ledger_bases)
    print(
        f"  {len(buckets.graduate)} pass all present transports (graduation candidates)",
        file=sys.stderr,
    )
    print(
        f"  {len(buckets.confirm)} need confirmation (single-/e2e_rest-only / incomplete)",
        file=sys.stderr,
    )
    print(f"  {len(buckets.partial_passing)} pass some transports (partial)", file=sys.stderr)

    # Step 5: Classify each xfailed test
    print("Classifying xfailed tests...", file=sys.stderr)
    report = AuditReport(
        total_xfailed=len(xfailed_tests),
        total_xpassed=len(xpassed_tests),
        partial_passing=buckets.partial_passing,
        partial_missing=buckets.partial_missing,
    )

    seen_drift: set[str] = set()
    for test in xfailed_tests:
        for warning in crash_line_drift_warnings(
            test, premature_xfails, artifact_root=artifact_root, host_root=host_root
        ):
            if warning not in seen_drift:
                seen_drift.add(warning)
                report.line_drift_warnings.append(warning)
                print(f"  WARNING: {warning}", file=sys.stderr)
        entry = classify_xfail(
            test,
            tag_map,
            premature_xfails,
            artifact_root=artifact_root,
            host_root=host_root,
        )
        report.entries.append(entry)
        report.by_category[entry.category].append(entry)
        uc = extract_uc(entry.nodeid)
        report.by_uc[uc][entry.category] += 1

    # Step 6: Create xpassed entries
    for test in xpassed_tests:
        base = extract_scenario_base(test["nodeid"])
        transport = extract_transport(test["nodeid"])
        if base in buckets.graduate:
            category = "STALE"
        elif base in buckets.confirm:
            category = "STALE_CONFIRM"
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
    print("\nGenerating report...", file=sys.stderr)
    text = generate_report(report, output_path)

    if not output_path:
        print(text)
    else:
        # Progress summary on stderr so stdout stays report-only when piped
        print("\n=== SUMMARY ===", file=sys.stderr)
        for cat in XFAIL_CATEGORIES:
            count = len(report.by_category.get(cat, []))
            if count > 0:
                print(f"  {cat}: {count}", file=sys.stderr)
        print(f"\n  STALE (graduation candidates): {len(buckets.graduate)} scenarios", file=sys.stderr)
        print(f"  STALE_CONFIRM (needs confirmation): {len(buckets.confirm)} scenarios", file=sys.stderr)
        print(f"  PARTIAL_PASS: {len(buckets.partial_passing)} scenarios", file=sys.stderr)
        if report.line_drift_warnings:
            print(f"  line-drift warnings: {len(report.line_drift_warnings)}", file=sys.stderr)


if __name__ == "__main__":
    main()
