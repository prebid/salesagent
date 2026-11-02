#!/usr/bin/env python3
"""Verify BDD feature error codes against the pinned AdCP error-code vocabulary.

Every ``error code should be "X"`` assertion and every quoted ``error "X"``
Examples cell in ``tests/bdd/features/BR-UC-*.feature`` must use a code from the
canonical AdCP ``error-code`` enum. Codes outside the enum are non-canonical and
must be reconciled upstream (the AdCP spec accepts arbitrary code strings, but
our scenarios are derived from the pinned spec and must stay on the standard
vocabulary so buyers get machine-readable, recovery-classifiable codes).

This script is BOTH:
  * the Phase-1 reconciliation worklist generator (lists what to fix), and
  * the Phase-4 Guard A engine (``--strict``-style: exit 1 on any finding).

Canonical source: ``src.core.errors.codes.CODE_TABLE`` — the codes a raise site
can actually EMIT, read offline with no ~/projects/adcp clone needed. CODE_TABLE
is the same table ``TransportResult.assert_wire_error`` and ``is_pinned_error_code``
resolve through, so this gate answers the same question as the assertions it backs;
see ``load_enum`` below for why the published ``adcp.ErrorCode`` set is the wrong
question. The ``enumMetadata`` recovery/suggestion content stays on the
separately-pinned vendored fixture — see docs/adcp-spec-version.md "Pinned schema
sources".

Usage:
    # Worklist for specific use cases
    uv run python scripts/verify_feature_error_codes.py --uc UC-002 UC-003

    # Whole repo (guard mode: exit 1 if any non-canonical code is found)
    uv run python scripts/verify_feature_error_codes.py

    # Machine-readable
    uv run python scripts/verify_feature_error_codes.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEATURES_DIR = PROJECT_ROOT / "tests" / "bdd" / "features"

# A code-shaped token: ALL_CAPS_SNAKE (e.g. INVALID_REQUEST) or a lowercase
# *_error token (e.g. authentication_error). This excludes placeholders like
# "<error_code>" and prose words, so only real error codes are graded.
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$|^[a-z][a-z0-9_]*_error$")

# `Then the error code should be "X"` — X may be a literal code or an Examples
# placeholder like "<error_code>". `is` is accepted alongside `should be`: both
# forms are live in the corpus, and matching only `should be` let 8 assertions in
# BR-UC-008 (`And the error code is "APPROVAL_REQUIRED"`) sit in a file this
# script reported CLEAN.
SHOULD_RE = re.compile(r'error code (?:should be|is) "([^"]+)"')

# Quoted Examples cell form: `| error "X" with suggestion |`.
#
# NEGATIVE LOOKBEHIND ON `response has`, because `the response has error "X"` is
# NOT the wire error code — it is a PAYLOAD FIELD with its own enum. BR-UC-032's
# comply_test_controller declares
# comply-test-controller-response.json oneOf[7].properties.error.enum =
# [INVALID_TRANSITION, INVALID_STATE, NOT_FOUND, UNKNOWN_SCENARIO, INVALID_PARAMS,
#  FORBIDDEN, JCS_NON_FINITE_NUMBER, INTERNAL_ERROR].
# Grading those against the wire vocabulary is a category error, and it did real
# damage: this script flagged NOT_FOUND, and "fixing" it to REFERENCE_NOT_FOUND
# (b47c5dae7) put a value outside the payload enum on the wire — breaking the
# conformance the scenario exists to check. Two namespaces, one regex.
CELL_RE = re.compile(r'(?<!response has )\berror "([^"]+)"')

# Prose form `... error code "X"` (and any `or "Y"` continuation on the same
# line), distinct from the `should be` assertion above. Catches descriptive
# outcome cells and inline rejections such as
# `error code "INSUFFICIENT_INVENTORY" or "INVALID_TARGETING"` or
# `rejected with error code "VALIDATION_ERROR"`, which neither SHOULD_RE
# (needs `should be`) nor CELL_RE (needs `error "X"` without `code`) matches.
PROSE_RE = re.compile(r'error code "')
QUOTED_RE = re.compile(r'"([^"]+)"')

# Bare (UNQUOTED) sentence form: `Then the error should be ASSIGNMENTS_EMPTY`.
# Structurally identifiable, so it can be graded without guessing whether a bare
# ALL_CAPS token is an error code at all.
BARE_SENTENCE_RE = re.compile(r"\berror should be ([A-Z][A-Z0-9_]*)")

# Examples columns whose HEADER declares the cell is an error code, so bare
# ALL_CAPS values in them are codes by declaration rather than by guesswork.
#
# This is the whole reason bare tokens are graded by column NAME and not by
# shape: the corpus is full of ALL_CAPS values that are NOT wire error codes --
# `snapshot_unavailable_reason` values (SNAPSHOT_UNSUPPORTED,
# SNAPSHOT_PERMISSION_DENIED), comply_test_controller's own payload `error` enum
# (NOT_FOUND, INVALID_TRANSITION, UNKNOWN_SCENARIO), config names like
# GEMINI_API_KEY. Grading every bare token would flag all of them, which is the
# same namespace confusion that made CELL_RE flag a payload field and led to a
# scenario being "fixed" into a conformance break (see the CELL_RE note above).
ERROR_COLUMN_RE = re.compile(r"^(error|error_code|expected_error|error_type)$", re.IGNORECASE)
BARE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Gherkin block boundaries — placeholder resolution is scoped to the owning
# Scenario Outline's Examples table (file-wide resolution bleeds across tables).
BLOCK_RE = re.compile(r"^\s*(Feature|Rule|Background|Scenario|Scenario Outline):")


def load_enum() -> set[str]:
    """Every code a raise site can actually emit -- CODE_TABLE, not the published enum.

    EMITTABILITY, not spec membership. This used to return ``adcp.ErrorCode``, the
    pinned spec's 92 published members, which made every legitimate PLATFORM code a
    finding. That is the wrong question twice over:

    * The AdCP error vocabulary is OPEN (core/error.json types ``error.code`` as a
      wire-typed string; published codes are documentary; senders MAY emit codes
      outside the set and receivers MUST decode an unknown one by reading
      ``recovery``). A platform code on the wire is conformant, not a violation.
    * It disagreed with the assertion helper it is supposed to back.
      ``TransportResult.assert_wire_error`` and ``is_pinned_error_code`` both resolve
      through CODE_TABLE, whose own comment states the rule: "EMITTABILITY, not spec
      membership. The question is CODE_TABLE membership -- can production put this
      code on the wire at all". A gate that answers a different question than the
      assertion it guards is drift, and it fired on AGENT_UNREACHABLE, a code
      salesagent-3dawm.16 deliberately KEPT after checking the pin.

    What the gate still catches -- and what it exists for -- is a scenario naming a
    code NO raise site can emit, which is what #1753 is about.
    """
    # The repo root, so `src.` resolves however this script is invoked (make
    # quality-ci runs it as `uv run python scripts/...`, which puts scripts/ on
    # sys.path, not the root).
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        from src.core.errors.codes import CODE_TABLE
    except ModuleNotFoundError as e:
        # An instrument failure, not "findings exist" -- must exit 2 (this
        # script's diagnostic code) rather than fall through to an uncaught
        # traceback, which exits 1, the SAME code this script uses for
        # "findings exist" and which gates make quality.
        print(f"ERROR: emittable code table not found: {e}", file=sys.stderr)
        sys.exit(2)
    return {str(code) for code in CODE_TABLE}


def _iter_blocks(lines: list[str]):
    """Yield (start_index, block_lines) split on Gherkin block boundaries."""
    start = 0
    block: list[str] = []
    for i, line in enumerate(lines):
        if BLOCK_RE.match(line) and block:
            yield start, block
            block = []
            start = i
        block.append(line)
    if block:
        yield start, block


def _block_columns(block: list[str]) -> dict[str, list[str]]:
    """Map Examples column name -> cell values for a single block.

    Each contiguous run of ``| ... |`` rows is a table; its first row is the
    header. Multiple tables in one block share the column map by header name.
    """
    columns: dict[str, list[str]] = {}
    header: list[str] | None = None
    for line in block:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if header is None:
                header = cells
            else:
                for name, value in zip(header, cells, strict=False):
                    columns.setdefault(name, []).append(value)
        else:
            header = None
    return columns


def expected_codes(feature: Path) -> list[tuple[int, str]]:
    """Return (line_number, code) for every expected error code in a feature."""
    lines = feature.read_text().splitlines()
    found: list[tuple[int, str]] = []
    for start, block in _iter_blocks(lines):
        columns = _block_columns(block)
        for offset, line in enumerate(block):
            lineno = start + offset + 1
            for match in SHOULD_RE.finditer(line):
                token = match.group(1)
                if token.startswith("<") and token.endswith(">"):
                    for value in columns.get(token[1:-1], []):
                        found.append((lineno, value))
                else:
                    found.append((lineno, token))
            # Prose `error code "X"` (plus any `or "Y"`): grade every quoted token
            # on the line. The CODE_RE filter in find_non_canonical drops prose
            # words, so over-matching quoted non-codes is harmless.
            if PROSE_RE.search(line) and "should be" not in line:
                for match in QUOTED_RE.finditer(line):
                    token = match.group(1)
                    if token.startswith("<") and token.endswith(">"):
                        for value in columns.get(token[1:-1], []):
                            found.append((lineno, value))
                    else:
                        found.append((lineno, token))
            if line.strip().startswith("|"):
                for match in CELL_RE.finditer(line):
                    found.append((lineno, match.group(1)))
            for match in BARE_SENTENCE_RE.finditer(line):
                found.append((lineno, match.group(1)))
        # Bare values in Examples columns the header declares to be error codes.
        # Reported against the block start: _block_columns flattens the table, so
        # the per-cell line number is not preserved. The file+code is enough to
        # locate it, and keeping the column map as the single table parser beats
        # a second, drifting one.
        for name, values in columns.items():
            if not ERROR_COLUMN_RE.match(name):
                continue
            for value in values:
                if BARE_CODE_RE.match(value):
                    found.append((start + 1, value))
    return found


def _uc_globs(uc_filters: list[str]) -> list[str]:
    """Normalize `UC-002`/`002`/`UC-GET-PRODUCTS` -> `BR-UC-<id>-*.feature`."""
    globs = []
    for raw in uc_filters:
        uc_id = raw[3:] if raw.upper().startswith("UC-") else raw
        globs.append(f"BR-UC-{uc_id}-*.feature")
    return globs


def select_features(uc_filters: list[str] | None) -> list[Path]:
    if not uc_filters:
        return sorted(FEATURES_DIR.glob("BR-UC-*.feature"))
    selected: list[Path] = []
    for pattern in _uc_globs(uc_filters):
        selected.extend(FEATURES_DIR.glob(pattern))
    return sorted(set(selected))


def find_non_canonical(features: list[Path], enum: set[str]) -> list[dict]:
    findings: list[dict] = []
    for feature in features:
        for lineno, code in expected_codes(feature):
            if CODE_RE.match(code) and code not in enum:
                findings.append({"file": str(feature.relative_to(PROJECT_ROOT)), "line": lineno, "code": code})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uc",
        nargs="+",
        metavar="UC",
        help="Limit to use cases, e.g. --uc UC-002 UC-003 (default: all BR-UC-*)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    enum = load_enum()
    features = select_features(args.uc)
    if not features:
        print("ERROR: no matching feature files", file=sys.stderr)
        return 2

    findings = find_non_canonical(features, enum)
    findings.sort(key=lambda f: (f["file"], f["line"], f["code"]))
    distinct = sorted({f["code"] for f in findings})

    if args.json:
        print(
            json.dumps(
                {
                    "scope": args.uc or "repo-wide",
                    "features_scanned": len(features),
                    "finding_count": len(findings),
                    "distinct_codes": distinct,
                    "findings": findings,
                },
                indent=2,
            )
        )
    else:
        scope = " ".join(args.uc) if args.uc else "repo-wide"
        for f in findings:
            print(f"{f['file']}:{f['line']}: {f['code']}")
        print(
            f"\n{scope}: {len(findings)} non-canonical occurrence(s), "
            f"{len(distinct)} distinct code(s) across {len(features)} feature file(s)."
        )
        if distinct:
            print(f"Distinct: {', '.join(distinct)}")

    # Guard mode: any finding fails the gate.
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
