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

Canonical source: the VENDORED enum at
``tests/fixtures/adcp_schemas_pinned/enums/error-code.json`` (pinned to adcp
commit 04f59d2d5). Read offline — CI has no ~/projects/adcp clone.

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
ENUM_PATH = PROJECT_ROOT / "tests" / "fixtures" / "adcp_schemas_pinned" / "enums" / "error-code.json"

# A code-shaped token: ALL_CAPS_SNAKE (e.g. INVALID_REQUEST) or a lowercase
# *_error token (e.g. authentication_error). This excludes placeholders like
# "<error_code>" and prose words, so only real error codes are graded.
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$|^[a-z][a-z0-9_]*_error$")

# `Then the error code should be "X"` — X may be a literal code or an Examples
# placeholder like "<error_code>".
SHOULD_RE = re.compile(r'error code should be "([^"]+)"')

# Quoted Examples cell form: `| error "X" with suggestion |`.
CELL_RE = re.compile(r'\berror "([^"]+)"')

# Prose form `... error code "X"` (and any `or "Y"` continuation on the same
# line), distinct from the `should be` assertion above. Catches descriptive
# outcome cells and inline rejections such as
# `error code "INSUFFICIENT_INVENTORY" or "INVALID_TARGETING"` or
# `rejected with error code "VALIDATION_ERROR"`, which neither SHOULD_RE
# (needs `should be`) nor CELL_RE (needs `error "X"` without `code`) matches.
PROSE_RE = re.compile(r'error code "')
QUOTED_RE = re.compile(r'"([^"]+)"')

# Gherkin block boundaries — placeholder resolution is scoped to the owning
# Scenario Outline's Examples table (file-wide resolution bleeds across tables).
BLOCK_RE = re.compile(r"^\s*(Feature|Rule|Background|Scenario|Scenario Outline):")


def load_enum() -> set[str]:
    if not ENUM_PATH.exists():
        print(
            f"ERROR: pinned enum not found at {ENUM_PATH}\n"
            "Run: uv run python tests/fixtures/adcp_schemas_pinned/_refresh.py",
            file=sys.stderr,
        )
        sys.exit(2)
    return set(json.loads(ENUM_PATH.read_text())["enum"])


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
