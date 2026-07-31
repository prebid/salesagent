"""Shared helpers for BDD audit meta-tooling scripts.

Used by ``bdd_full_audit.py``, ``audit_xfails.py``, ``cross_reference_audit.py``,
``salvage_audit_output.py``, and ``scripts/graduate_pending.py`` so transport /
scenario / UC parsing and graduation bucketing cannot drift apart.

Transport vocabulary mirrors ``tests/bdd/conftest.py`` parametrize ids after
#1417: ``a2a`` / ``mcp`` / ``rest``, plus ``e2e_rest`` when in-network is
enabled. Legacy ``impl`` remains recognized for historical bdd.json files but
is never required for graduation and does not inflate the single-transport
confirmation gate. ``e2e_mcp`` / ``e2e_a2a`` are recognized so unrecognized
transports are not silently dropped from coverage.

Report-bucket vocabulary (intentional split, same underlying coverage grade):
``bdd_full_audit`` labels partial xpass as ``PARTIAL_XPASS`` / full as
``GRADUATE`` / confirm as ``GRADUATE_CONFIRM``; ``audit_xfails`` keeps
``PARTIAL_PASS`` / ``STALE`` / ``STALE_CONFIRM``. Do not unify the report
tokens without a deliberate cross-script rename. The shared ``grade_base``
helper owns the confirmation gate so both classifiers cannot drift.

Canonical conftest tag parsing lives here as ``parse_conftest_xfail_tags``
(``tag → (reason, mechanism)``). Callers that only need reasons use
``tag_reasons`` (a one-line projection) — not a second parser.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

# `[` anchor + `(?:-|])` tail delimiter recognizes full ids (including
# `e2e_rest` / `e2e_mcp` / `e2e_a2a`); longer e2e_* alternatives come first.
_TRANSPORT_RE = re.compile(r"\[(e2e_rest|e2e_mcp|e2e_a2a|impl|a2a|mcp|rest)(?:-|])")

# ANSI CSI / OSC sequences — strip before salvage regex matching.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")


class StepRecord(TypedDict):
    """Canonical six-key step shape for the JSONL store (producer + consumer)."""

    file_path: str
    line_number: int
    step_type: str
    step_text: str
    function_name: str
    source_text: str


STEP_RECORD_KEYS: frozenset[str] = frozenset(StepRecord.__annotations__)


class GradeResult(NamedTuple):
    """Named grade so callers cannot swap the two bools positionally."""

    graduates: bool
    passing: set[str]
    missing: set[str]
    present_count: int
    needs_confirmation: bool
    mixed_examples: bool


class CoverageResult(NamedTuple):
    """Named coverage so callers cannot swap graduates/passing/missing."""

    graduates: bool
    passing: set[str]
    missing: set[str]


class ArtifactCensus(NamedTuple):
    """Transport presence across an entire bdd.json artifact."""

    transports_seen: frozenset[str]
    has_e2e_rest: bool
    incomplete_e2e: bool  # e2e_rest absent for every base → force CONFIRM


class LoadedArtifact(NamedTuple):
    """Shared bdd.json load: tests, outcomes, and census-driven force_confirm."""

    tests: list[dict[str, Any]]
    nodeid_outcomes: list[tuple[str, str]]
    force_confirm: bool
    artifact_root: str | None


# Outcomes that count as "this transport passed for the scenario base".
_PASSING_OUTCOMES = frozenset({"passed", "xpassed"})
# Outcomes that block graduation for a present transport.
_FAILING_OUTCOMES = frozenset({"failed", "xfailed"})

# Legacy transport that must not defeat the single-transport confirm gate.
_LEGACY_GATE_EXCLUDE = frozenset({"impl"})


def extract_transport(nodeid: str) -> str | None:
    """Extract transport id from a parametrized pytest nodeid.

    Recognizes ``a2a``, ``mcp``, ``rest``, ``e2e_rest``, ``e2e_mcp``,
    ``e2e_a2a``, and legacy ``impl``. Returns ``None`` when the nodeid has no
    known transport param (e.g. admin scenarios without wire-transport
    parametrization).
    """
    m = _TRANSPORT_RE.search(nodeid)
    return m.group(1) if m else None


def extract_scenario_base(nodeid: str) -> str:
    """Strip the trailing parametrize bracket suffix to get the scenario base."""
    return re.sub(r"\[.*\]$", "", nodeid)


def short_base(base: str) -> str:
    """Shorten a scenario base to the final ``::`` segment for report bullets."""
    return base.rsplit("::", 1)[-1]


def extract_uc(text: str) -> str:
    """Extract use case id from a nodeid or path (e.g. ``UC-004``).

    Matches ``test_ucNNN`` / ``ucNNN`` (case-insensitive). Returns ``GENERIC``
    when no use-case token is present.
    """
    m = re.search(r"(?:test_)?uc(\d+)", text, re.IGNORECASE)
    return f"UC-{m.group(1)}" if m else "GENERIC"


def extract_longrepr_e_line(longrepr: str) -> str:
    """Return the first pytest ``E `` error line from a longrepr, or ``\"\"``."""
    for line in longrepr.split("\n"):
        stripped = line.strip()
        if stripped.startswith("E "):
            return stripped[2:].strip()
    return ""


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so salvage regexes match colored captures."""
    return _ANSI_RE.sub("", text)


def transport_coverage(
    outcomes_by_transport: Mapping[str, str],
) -> CoverageResult:
    """Grade a scenario base from per-transport outcomes.

    Graduation is relative to transports *present for that base* in the result
    set — not a hardcoded four-transport universe. A UC that only parametrizes
    ``a2a``+``mcp`` graduates when both pass; ``rest`` is not "missing".

    Returns ``CoverageResult(graduates, passing, missing)`` where ``missing`` is
    the set of present transports that did not pass (failed/xfailed/skipped or
    unknown outcome).
    """
    present = set(outcomes_by_transport)
    if not present:
        return CoverageResult(graduates=False, passing=set(), missing=set())

    passing = {t for t, outcome in outcomes_by_transport.items() if outcome in _PASSING_OUTCOMES}
    failing = {t for t, outcome in outcomes_by_transport.items() if outcome in _FAILING_OUTCOMES}
    missing = present - passing
    # Graduate when every present transport passed. ``not failing`` is
    # belt-and-braces: for standard pytest outcomes failing ⊆ missing, so
    # ``present <= passing`` already excludes fail/xfail; keep both.
    graduates = present <= passing and not failing
    return CoverageResult(graduates=graduates, passing=passing, missing=missing)


def _worst_transport_outcome(outcomes: list[str]) -> str:
    """Aggregate example-row outcomes for one transport.

    A transport passes only if every example passed/xpassed and none
    failed/xfailed. Any failing example dominates; otherwise the first
    non-passing outcome blocks graduation; else prefer ``passed`` over
    ``xpassed``.
    """
    if any(o in _FAILING_OUTCOMES for o in outcomes):
        return "failed" if "failed" in outcomes else "xfailed"
    non_passing = [o for o in outcomes if o not in _PASSING_OUTCOMES]
    if non_passing:
        return non_passing[0]
    if "passed" in outcomes:
        return "passed"
    return "xpassed"


def outcomes_by_transport_for_base(
    base: str,
    nodeid_outcomes: Iterable[tuple[str, str]],
) -> dict[str, str]:
    """Build ``{transport: aggregated_outcome}`` for one scenario base.

    Scenario outlines emit one bdd.json entry per example row per transport.
    Aggregate a worst-outcome per transport: the transport passes only if
    every example passed/xpassed and none failed/xfailed.
    """
    collected: dict[str, list[str]] = {}
    for nodeid, outcome in nodeid_outcomes:
        if extract_scenario_base(nodeid) != base:
            continue
        transport = extract_transport(nodeid)
        if transport:
            collected.setdefault(transport, []).append(outcome)
    return {t: _worst_transport_outcome(outs) for t, outs in collected.items()}


def artifact_transport_census(
    nodeid_outcomes: Iterable[tuple[str, str]],
) -> ArtifactCensus:
    """Census transports present anywhere in an artifact.

    When ``e2e_rest`` never appears, the default tox ``bdd`` job silently
    dropped the 5th transport — every firm graduation must route to CONFIRM.
    """
    seen: set[str] = set()
    for nodeid, _outcome in nodeid_outcomes:
        transport = extract_transport(nodeid)
        if transport:
            seen.add(transport)
    has_e2e = "e2e_rest" in seen
    return ArtifactCensus(
        transports_seen=frozenset(seen),
        has_e2e_rest=has_e2e,
        incomplete_e2e=not has_e2e,
    )


def load_e2e_rest_known_failure_bases(ledger_path: Path) -> set[str]:
    """Scenario bases listed in ``tests/bdd/e2e_rest_known_failures.txt``.

    Non-strict ledger xfails live outside conftest tags; a GRADUATE verdict
    that only clears conftest would still leave ledger markers. When the
    artifact is e2e-incomplete, these bases must not firm-graduate.
    """
    if not ledger_path.is_file():
        return set()
    bases: set[str] = set()
    for line in ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Ledger lines are full nodeids or bare scenario refs.
        bases.add(extract_scenario_base(stripped))
    return bases


def load_bdd_artifact(path: Path) -> LoadedArtifact:
    """Load bdd.json with the shared empty-artifact guard and transport census.

    Empty ``{"tests": []}`` is indistinguishable from a clean run — refuse with
    exit 2 and one canonical stderr message. When ``e2e_rest`` is absent from
    the whole artifact, ``force_confirm`` is True and a single warning is
    emitted on stderr so all four consumers grade identically.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    tests = list(data.get("tests") or [])
    if not tests:
        print(
            'ERROR: artifact contains 0 tests — refusing empty {"tests": []}.',
            file=sys.stderr,
        )
        raise SystemExit(2)

    nodeid_outcomes = [(t["nodeid"], t["outcome"]) for t in tests]
    census = artifact_transport_census(nodeid_outcomes)
    force_confirm = census.incomplete_e2e
    if force_confirm:
        print(
            "WARNING: e2e_rest appears for no base — every graduation routes to confirm.",
            file=sys.stderr,
        )
    root = data.get("root")
    artifact_root = root if isinstance(root, str) else None
    return LoadedArtifact(
        tests=tests,
        nodeid_outcomes=nodeid_outcomes,
        force_confirm=force_confirm,
        artifact_root=artifact_root,
    )


def grade_base(
    base: str,
    nodeid_outcomes: Iterable[tuple[str, str]],
    *,
    force_confirm: bool = False,
    ledger_member: bool = False,
) -> GradeResult:
    """Grade one scenario base end-to-end for both audit classifiers.

    Composes ``outcomes_by_transport_for_base`` + ``transport_coverage`` and
    applies the confirmation gate so callers never re-wire that pipeline (or
    re-scan for ``present_count``).

    ``needs_confirmation`` is True when every present transport passed but only
    one *non-legacy* transport is present, when ``force_confirm`` is set
    (artifact-wide e2e_rest absence), or when ``ledger_member`` is True and
    ``e2e_rest`` is absent for this base (ledger-awareness). Lone ``e2e_rest``
    is the common single-transport case; many e2e xfails are still
    ``strict=True`` in conftest — do not claim they are non-strict.

    ``mixed_examples`` is True when the base has present transports but none
    passed (every present transport failed/xfailed/skipped after aggregate).
    """
    outcomes = outcomes_by_transport_for_base(base, nodeid_outcomes)
    coverage = transport_coverage(outcomes)
    present_count = len(outcomes)
    gate_transports = {t for t in outcomes if t not in _LEGACY_GATE_EXCLUDE}
    gate_count = len(gate_transports) if gate_transports else present_count
    needs_confirmation = bool(coverage.graduates and (gate_count <= 1 or force_confirm))
    if (
        coverage.graduates
        and not needs_confirmation
        and ledger_member
        and "e2e_rest" not in coverage.passing
        and "e2e_rest" not in coverage.missing
    ):
        needs_confirmation = True
    mixed_examples = bool(present_count > 0 and not coverage.passing)
    return GradeResult(
        graduates=coverage.graduates,
        passing=coverage.passing,
        missing=coverage.missing,
        present_count=present_count,
        needs_confirmation=needs_confirmation,
        mixed_examples=mixed_examples,
    )


def parse_conftest_xfail_tags(conftest_path: Path) -> dict[str, tuple[str, str]]:
    """Parse conftest.py to extract tag → (reason, mechanism) mappings.

    Returns dict mapping tag string to (reason, mechanism) where mechanism
    is one of: 'production_gap', 'transport_gap', 'harness_gap', 'partial_impl'.

    Covers dict forms (``_XFAIL_TAGS``, ``_UC004_XFAIL_TAGS`` tuples,
    ``_UC003_EXT_XFAILS``), set forms (``_UC*_XFAIL_TAGS``, partition/boundary/
    partial), MCP selective reason= kwargs, and validation tuple forms.
    """
    text = conftest_path.read_text(encoding="utf-8", errors="replace")
    tag_map: dict[str, tuple[str, str]] = {}

    # _XFAIL_TAGS dict: tag → reason (production gaps)
    for match in re.finditer(r'"(T-[^"]+)":\s*"([^"]+)"', text):
        tag, reason = match.group(1), match.group(2)
        tag_map[tag] = (reason, "production_gap")

    # _UC026_XFAIL_TAGS, _UC019_XFAIL_TAGS, etc. — sets with production/harness gaps
    for set_match in re.finditer(
        r"(_UC\d+_XFAIL_TAGS)\s*(?::\s*set\[str\])?\s*=\s*\{([^}]+)\}",
        text,
        re.DOTALL,
    ):
        set_name = set_match.group(1)
        block = set_match.group(2)
        mechanism = "production_gap"
        if "harness" in set_name.lower() or "env" in set_name.lower():
            mechanism = "harness_gap"

        for tag_m in re.finditer(r'"(T-[^"]+)"', block):
            tag = tag_m.group(1)
            if tag not in tag_map:
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
    for set_match in re.finditer(r"(_UC\d+_PARTIAL_TAGS)\s*=\s*\{([^}]+)\}", text, re.DOTALL):
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
    for match in re.finditer(r'\(\s*"(T-[^"]+)",\s*\{[^}]*\},\s*"([^"]+)"\s*\)', text):
        tag, reason = match.group(1), match.group(2)
        if tag not in tag_map:
            tag_map[tag] = (reason, "production_gap")

    # _UC004_XFAIL_TAGS dict with tuples: tag → (reason, strict)
    for match in re.finditer(r'"(T-UC-004[^"]+)":\s*\(\s*"([^"]+)",\s*(True|False)\s*\)', text):
        tag, reason = match.group(1), match.group(2)
        if tag not in tag_map:
            tag_map[tag] = (reason, "production_gap")

    # _UC003_EXT_XFAILS dict
    for match in re.finditer(r'"(T-UC-003[^"]+)":\s*"([^"]+)"', text):
        tag, reason = match.group(1), match.group(2)
        if tag not in tag_map:
            tag_map[tag] = (reason, "production_gap")

    # Dict form with nested reason key (legacy / alternate)
    for m in re.finditer(
        r'"(T-[^"]+)"\s*:\s*\{[^}]*"reason"\s*:\s*"([^"]+)"',
        text,
    ):
        tag, reason = m.group(1), m.group(2)
        if tag not in tag_map:
            tag_map[tag] = (reason, "production_gap")

    return tag_map


def tag_reasons(conftest_path: Path) -> dict[str, str]:
    """Projection of ``parse_conftest_xfail_tags`` to ``tag → reason`` only."""
    return {k: v[0] for k, v in parse_conftest_xfail_tags(conftest_path).items()}


def remap_container_path(
    cpath: str,
    *,
    artifact_root: str | None,
    host_root: Path,
) -> Path:
    """Remap a container-rooted crash path onto the host repo root.

    Real ``run_all_tests.sh`` artifacts declare ``root: "/app"`` and store
    crash paths as ``/app/tests/...``. Host step scans use the worktree path;
    without remapping, premature matching and drift warnings never fire.
    """
    if artifact_root:
        root = artifact_root.rstrip("/")
        if cpath == root or cpath.startswith(root + "/"):
            rel = cpath[len(root) :].lstrip("/")
            return (host_root / rel).resolve()
    return Path(cpath).resolve()
