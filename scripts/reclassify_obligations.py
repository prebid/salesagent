#!/usr/bin/env python3
"""Reclassify obligation layers: behavioral vs schema.

Parses all docs/test-obligations/*.md files, extracts the scenario/requirement
text for each obligation ID, and classifies it as ``behavioral`` or ``schema``
using scored keyword matching.

Modes::

    python scripts/reclassify_obligations.py --report
        Print classification summary without modifying files.

    python scripts/reclassify_obligations.py --apply
        Update **Layer** tags in obligation docs.

    python scripts/reclassify_obligations.py --regenerate-allowlist
        Rewrite obligation_coverage_allowlist.json to contain only
        behavioral obligations that lack integration test coverage.

    python scripts/reclassify_obligations.py --apply --regenerate-allowlist
        Do both.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

OBLIGATIONS_DIR = Path(__file__).resolve().parents[1] / "docs" / "test-obligations"
ALLOWLIST_FILE = Path(__file__).resolve().parents[1] / "tests" / "unit" / "obligation_coverage_allowlist.json"
INTEGRATION_DIR = Path(__file__).resolve().parents[1] / "tests" / "integration"

_OBLIGATION_ID_RE = re.compile(r"[A-Z][A-Z0-9]+-[\w-]+-\d{2}")

# ---------------------------------------------------------------------------
# Keyword scoring tables
# ---------------------------------------------------------------------------

# Positive score → behavioral, negative score → schema
_BEHAVIORAL_KEYWORDS: list[tuple[re.Pattern[str], int]] = [
    # State mutation
    (re.compile(r"\bcreates?\b", re.I), 1),
    (re.compile(r"\bpersists?\b", re.I), 1),
    (re.compile(r"\bstores?\b", re.I), 1),
    (re.compile(r"\bsaves?\b", re.I), 1),
    (re.compile(r"\bdeletes?\b", re.I), 1),
    (re.compile(r"\binserts?\b", re.I), 1),
    (re.compile(r"\bupdated?\b", re.I), 1),
    (re.compile(r"\bdeactivat", re.I), 1),
    (re.compile(r"\bno database records\b", re.I), 2),
    (re.compile(r"\bno state change", re.I), 1),
    # Workflow / status transitions
    (re.compile(r"\bapproval\b", re.I), 1),
    (re.compile(r"\bpending\b", re.I), 1),
    (re.compile(r"\bstatus\s+(changes?|transition|becomes?)\b", re.I), 2),
    (re.compile(r"\bworkflow\b", re.I), 1),
    (re.compile(r"\bauto-approved\b", re.I), 1),
    (re.compile(r"\bpending_approval\b", re.I), 1),
    (re.compile(r"\bpending_creatives\b", re.I), 1),
    # External I/O
    (re.compile(r"\badapter\b", re.I), 1),
    (re.compile(r"\bGAM\b"), 1),
    (re.compile(r"\bad server\b", re.I), 1),
    (re.compile(r"\bwebhook\b", re.I), 1),
    (re.compile(r"\bnotification\b", re.I), 1),
    (re.compile(r"\bLLM\b"), 1),
    (re.compile(r"\bAPI call\b", re.I), 1),
    (re.compile(r"\bretry\b", re.I), 1),
    (re.compile(r"\bexponential backoff\b", re.I), 1),
    (re.compile(r"\bGEMINI\b"), 1),
    # Isolation / scoping
    (re.compile(r"\btenant isolation\b", re.I), 1),
    (re.compile(r"\bprincipal isolation\b", re.I), 1),
    (re.compile(r"\bownership\b", re.I), 1),
    (re.compile(r"\bscoped\b", re.I), 1),
    (re.compile(r"\bfiltered by principal\b", re.I), 1),
    (re.compile(r"\bcross-principal\b", re.I), 1),
    # Auth / access control
    # NOTE: bare "rejected" removed — it fires on schema validation rejection
    # ("Then rejected (not in enum)") causing false positives. Use specific
    # phrases instead.
    (re.compile(r"\bdenied\b", re.I), 1),
    (re.compile(r"\bunauthorized\b", re.I), 1),
    (re.compile(r"\bforbidden\b", re.I), 1),
    (re.compile(r"\baccess control\b", re.I), 1),
    (re.compile(r"\bauth.required\b", re.I), 1),
    (re.compile(r"\bPermissionError\b"), 1),
    (re.compile(r"\bAUTH_REQUIRED\b"), 1),
    (re.compile(r"\bAUTH_TOKEN_INVALID\b"), 1),
    # Transactions
    (re.compile(r"\brollback\b", re.I), 1),
    (re.compile(r"\batomic\b", re.I), 1),
    (re.compile(r"\bfailure recovery\b", re.I), 1),
    (re.compile(r"\bno partial state\b", re.I), 2),
    # Multi-step / roundtrip
    (re.compile(r"\broundtrip\b", re.I), 2),
    (re.compile(r"\bcreate.+then.+retrieve\b", re.I), 2),
    (re.compile(r"\bcreate.+and.+list\b", re.I), 2),
    # Request processing (business logic beyond pure schema)
    (re.compile(r"\brequest is rejected\b", re.I), 1),
    (re.compile(r"\brequest proceeds\b", re.I), 1),
    (re.compile(r"\bfail-open\b", re.I), 1),
    (re.compile(r"\bresolution\b", re.I), 1),
    (re.compile(r"\blookup\b", re.I), 1),
    (re.compile(r"\bresolves?\s+to\b", re.I), 1),
    (re.compile(r"\bresolves?\s+the\b", re.I), 1),
    (re.compile(r"\bprincipal_[AB]\b"), 1),
    (re.compile(r"\banonymous\b", re.I), 1),
    (re.compile(r"\bauthenticat", re.I), 1),
    # Filtering/querying (requires DB or runtime logic)
    (re.compile(r"\bonly .+ returned\b", re.I), 1),
    (re.compile(r"\bsuppressed\b", re.I), 1),
    (re.compile(r"\bsilently\s+(excluded|dropped|omit)", re.I), 1),
    # Specific error codes (runtime validation, not schema)
    (re.compile(r"\bToolError\b"), 1),
    (re.compile(r"\bPOLICY_VIOLATION\b"), 1),
    (re.compile(r"\bINVALID_CREATIVES\b"), 1),
    (re.compile(r"\bBUDGET_BELOW_MINIMUM\b"), 1),
    (re.compile(r"\bEMPTY_UPDATE\b"), 1),
    (re.compile(r"\bLIST_NOT_FOUND\b"), 1),
]

_SCHEMA_KEYWORDS: list[tuple[re.Pattern[str], int]] = [
    # Field presence in response
    (re.compile(r"\bfield is present\b", re.I), 1),
    (re.compile(r"\bfield is included\b", re.I), 1),
    (re.compile(r"\bcontains? field\b", re.I), 1),
    (re.compile(r"\bresponse includes?\b", re.I), 1),
    (re.compile(r"\bis preserved\b", re.I), 1),
    (re.compile(r"\bare present\b", re.I), 1),
    # Serialization
    (re.compile(r"\bserialized to AdCP\b", re.I), 2),
    (re.compile(r"\bmodel_dump\b", re.I), 1),
    (re.compile(r"\bJSON output\b", re.I), 1),
    (re.compile(r"\bAdCP schema\b", re.I), 1),
    # Enum / type checking
    (re.compile(r"\benum\b", re.I), 1),
    (re.compile(r"\bvalid values?\b", re.I), 1),
    (re.compile(r"\btype is\b", re.I), 1),
    (re.compile(r"\bformat is\b", re.I), 1),
    (re.compile(r"\bXOR constraint\b", re.I), 1),
    (re.compile(r"\boneOf\b"), 1),
    # Shape / structure
    (re.compile(r"\brequired field", re.I), 1),
    (re.compile(r"\boptional field", re.I), 1),
    (re.compile(r"\badditional.properties\b", re.I), 1),
    (re.compile(r"\bextra fields?\b", re.I), 1),
    # Constraint patterns
    (re.compile(r"\bexactly one of\b", re.I), 1),
    (re.compile(r"\bmutually exclusive\b", re.I), 1),
    (re.compile(r"\bminimum\b", re.I), 1),
    (re.compile(r"\bmaximum\b", re.I), 1),
    (re.compile(r"\bminItems\b"), 1),
    (re.compile(r"\bminLength\b"), 1),
    (re.compile(r"\bpattern\b", re.I), 1),
    # Schema validation language
    (re.compile(r"\bschema valid", re.I), 1),
    (re.compile(r"\bschema.level\b", re.I), 1),
    (re.compile(r"\bschema\s+validation\b", re.I), 1),
    (re.compile(r"\bvalidated against\b", re.I), 1),
    (re.compile(r"\bnot in enum\b", re.I), 2),
    (re.compile(r"\brejected\s*\(", re.I), 1),  # "rejected (...)" = terse rejection in constraints
    # Default values
    (re.compile(r"\bdefaults? to\b", re.I), 1),
    # Conversion / format
    (re.compile(r"\bconversion\s+(succeeds|fails)\b", re.I), 1),
    # Requirement line patterns (strong signals in constraint docs)
    (re.compile(r"\bEnum:", re.I), 2),  # "Enum: value1, value2"
    (re.compile(r"\bBoolean\b"), 1),
    (re.compile(r"\bURI\b"), 1),
    (re.compile(r"\bISO \d+\b"), 1),  # ISO 4217, ISO 8601 etc.
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Obligation:
    """A single obligation with its parsed scenario text and classification."""

    obligation_id: str
    source_file: str
    # The extracted text block (requirement + scenario/given-when-then)
    text: str
    # Original layer tag
    original_layer: str = "behavioral"
    # Computed classification
    computed_layer: str = "behavioral"
    score: int = 0
    behavioral_signals: list[str] = field(default_factory=list)
    schema_signals: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing: extract obligation text blocks
# ---------------------------------------------------------------------------


def _extract_obligations_from_uc_doc(filepath: Path) -> list[Obligation]:
    """Extract obligations from UC/BR-UC scenario docs.

    Format: #### Scenario: ... followed by **Obligation ID**, **Layer**,
    then **Given**/**When**/**Then** lines until next #### or ### or ---.
    """
    obligations: list[Obligation] = []
    lines = filepath.read_text().splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith("#### Scenario:"):
            scenario_title = line
            # Look for Obligation ID on next line
            if i + 1 < len(lines) and "**Obligation ID**" in lines[i + 1]:
                m = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", lines[i + 1])
                if m:
                    oid = m.group(1)
                    # Determine current layer
                    layer = "behavioral"
                    if i + 2 < len(lines):
                        layer_m = re.search(r"\*\*Layer\*\*\s+(\S+)", lines[i + 2])
                        if layer_m:
                            layer = layer_m.group(1)

                    # Collect text from scenario line until next section/separator
                    text_lines = [scenario_title]
                    j = i + 1
                    while j < len(lines):
                        l = lines[j]
                        if l.startswith("#### Scenario:") and j != i:
                            break
                        if l.startswith("### ") and not l.startswith("#### "):
                            break
                        if l.strip() == "---":
                            break
                        text_lines.append(l)
                        j += 1

                    obligations.append(
                        Obligation(
                            obligation_id=oid,
                            source_file=filepath.name,
                            text="\n".join(text_lines),
                            original_layer=layer,
                        )
                    )
            i += 1
            continue

        i += 1

    return obligations


def _extract_obligations_from_rules(filepath: Path) -> list[Obligation]:
    """Extract obligations from business-rules.md.

    Format: ### BR-RULE-NNN: Title followed by **Obligation ID**, **Layer**,
    **Invariant:**, **Scenario:** with gherkin block.
    """
    obligations: list[Obligation] = []
    lines = filepath.read_text().splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        m = re.match(r"^### (BR-RULE-\d+):", line)
        if m:
            # Collect entire section until next ### or ---
            section_lines = [line]
            oid = None
            layer = "behavioral"
            j = i + 1
            while j < len(lines):
                l = lines[j]
                if l.startswith("### ") and not l.startswith("#### "):
                    break
                if l.strip() == "---":
                    break
                section_lines.append(l)
                if "**Obligation ID**" in l:
                    m2 = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", l)
                    if m2:
                        oid = m2.group(1)
                if "**Layer**" in l:
                    m3 = re.search(r"\*\*Layer\*\*\s+(\S+)", l)
                    if m3:
                        layer = m3.group(1)
                j += 1

            if oid:
                obligations.append(
                    Obligation(
                        obligation_id=oid,
                        source_file=filepath.name,
                        text="\n".join(section_lines),
                        original_layer=layer,
                    )
                )

        i += 1

    return obligations


def _extract_obligations_from_constraints(filepath: Path) -> list[Obligation]:
    """Extract obligations from constraints.md.

    Format: ### slug: Title followed by **Obligation ID**, **Layer**,
    **Requirement:**, **Scenario:** with gherkin block.
    """
    obligations: list[Obligation] = []
    lines = filepath.read_text().splitlines()
    in_constraints = False
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith("## Constraints"):
            in_constraints = True
            i += 1
            continue

        if in_constraints and line.startswith("### "):
            # Collect entire section until next ### or ---
            section_lines = [line]
            oid = None
            layer = "behavioral"
            j = i + 1
            while j < len(lines):
                l = lines[j]
                if l.startswith("### ") and not l.startswith("#### "):
                    break
                if l.strip() == "---":
                    break
                section_lines.append(l)
                if "**Obligation ID**" in l:
                    m = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", l)
                    if m:
                        oid = m.group(1)
                if "**Layer**" in l:
                    m2 = re.search(r"\*\*Layer\*\*\s+(\S+)", l)
                    if m2:
                        layer = m2.group(1)
                j += 1

            if oid:
                obligations.append(
                    Obligation(
                        obligation_id=oid,
                        source_file=filepath.name,
                        text="\n".join(section_lines),
                        original_layer=layer,
                    )
                )

        i += 1

    return obligations


def extract_all_obligations() -> list[Obligation]:
    """Parse all obligation docs and return a list of Obligation objects."""
    obligations: list[Obligation] = []

    for md in sorted(OBLIGATIONS_DIR.glob("*.md")):
        if md.name == "business-rules.md":
            obligations.extend(_extract_obligations_from_rules(md))
        elif md.name == "constraints.md":
            obligations.extend(_extract_obligations_from_constraints(md))
        else:
            obligations.extend(_extract_obligations_from_uc_doc(md))

    return obligations


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(obligation: Obligation) -> None:
    """Score and classify an obligation as behavioral or schema."""
    text = obligation.text
    score = 0
    behavioral_signals: list[str] = []
    schema_signals: list[str] = []

    for pattern, weight in _BEHAVIORAL_KEYWORDS:
        matches = pattern.findall(text)
        if matches:
            score += weight * len(matches)
            behavioral_signals.append(f"+{weight}×{len(matches)} {pattern.pattern}")

    for pattern, weight in _SCHEMA_KEYWORDS:
        matches = pattern.findall(text)
        if matches:
            score -= weight * len(matches)
            schema_signals.append(f"-{weight}×{len(matches)} {pattern.pattern}")

    obligation.score = score
    obligation.behavioral_signals = behavioral_signals
    obligation.schema_signals = schema_signals

    # Classification decision
    if score > 0:
        obligation.computed_layer = "behavioral"
    elif score <= 0 and schema_signals:
        obligation.computed_layer = "schema"
    else:
        # No signals at all → safe default
        obligation.computed_layer = "behavioral"


# ---------------------------------------------------------------------------
# Apply: update **Layer** tags in obligation docs
# ---------------------------------------------------------------------------


def apply_layer_tags(obligations: list[Obligation]) -> int:
    """Update **Layer** tags in obligation docs. Returns count of changes."""
    # Group by source file
    by_file: dict[str, dict[str, str]] = {}
    for ob in obligations:
        by_file.setdefault(ob.source_file, {})[ob.obligation_id] = ob.computed_layer

    changes = 0
    for md in sorted(OBLIGATIONS_DIR.glob("*.md")):
        if md.name not in by_file:
            continue
        id_to_layer = by_file[md.name]
        lines = md.read_text().splitlines(keepends=True)
        new_lines: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]
            if "**Obligation ID**" in line:
                m = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", line)
                if m and m.group(1) in id_to_layer:
                    new_layer = id_to_layer[m.group(1)]
                    new_lines.append(line)
                    # Check if next line is a Layer tag
                    if i + 1 < len(lines) and "**Layer**" in lines[i + 1]:
                        old_layer_m = re.search(r"\*\*Layer\*\*\s+(\S+)", lines[i + 1])
                        old_layer = old_layer_m.group(1) if old_layer_m else ""
                        if old_layer != new_layer:
                            # Preserve line ending style
                            ending = "\n" if lines[i + 1].endswith("\n") else ""
                            new_lines.append(f"**Layer** {new_layer}{ending}")
                            changes += 1
                            i += 2
                            continue
                    i += 1
                    continue

            new_lines.append(line)
            i += 1

        md.write_text("".join(new_lines))

    return changes


# ---------------------------------------------------------------------------
# Regenerate allowlist
# ---------------------------------------------------------------------------


def _get_covered_obligations() -> set[str]:
    """Extract Covers: tags from integration tests."""
    covered: set[str] = set()
    for tf in INTEGRATION_DIR.glob("test_*_v3.py"):
        for line in tf.read_text().splitlines():
            m = re.match(r"\s+Covers:\s+([\w-]+)", line)
            if m and _OBLIGATION_ID_RE.match(m.group(1)):
                covered.add(m.group(1))
    return covered


def regenerate_allowlist(obligations: list[Obligation]) -> int:
    """Rewrite allowlist to contain only uncovered behavioral obligations.

    Returns the new allowlist size.
    """
    behavioral_ids = {ob.obligation_id for ob in obligations if ob.computed_layer == "behavioral"}
    covered = _get_covered_obligations()
    uncovered = sorted(behavioral_ids - covered)

    ALLOWLIST_FILE.write_text(json.dumps(uncovered, indent=2) + "\n")
    return len(uncovered)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def print_report(obligations: list[Obligation]) -> None:
    """Print classification summary."""
    schema_obs = [ob for ob in obligations if ob.computed_layer == "schema"]
    behavioral_obs = [ob for ob in obligations if ob.computed_layer == "behavioral"]
    borderline = [ob for ob in obligations if ob.score == 0]

    # Summary
    print(f"Total obligations: {len(obligations)}")
    print(f"  Schema:     {len(schema_obs)} (reclassified)")
    print(f"  Behavioral: {len(behavioral_obs)} (kept)")
    print(f"  Borderline: {len(borderline)} (score = 0)")
    print()

    # Schema details
    if schema_obs:
        print("=" * 72)
        print(f"SCHEMA ({len(schema_obs)} obligations)")
        print("=" * 72)
        # Group by source file
        by_file: dict[str, list[Obligation]] = {}
        for ob in schema_obs:
            by_file.setdefault(ob.source_file, []).append(ob)

        for fname in sorted(by_file):
            print(f"\n  {fname}:")
            for ob in sorted(by_file[fname], key=lambda o: o.obligation_id):
                signals = ", ".join(ob.schema_signals[:3])
                print(f"    {ob.obligation_id} (score: {ob.score}) [{signals}]")

    # Borderline details
    if borderline:
        print()
        print("=" * 72)
        print(f"BORDERLINE ({len(borderline)} obligations, score = 0)")
        print("=" * 72)
        for ob in sorted(borderline, key=lambda o: o.obligation_id):
            b_count = len(ob.behavioral_signals)
            s_count = len(ob.schema_signals)
            layer = ob.computed_layer
            print(f"  {ob.obligation_id} → {layer} (behavioral signals: {b_count}, schema signals: {s_count})")

    # Allowlist impact
    covered = _get_covered_obligations()
    new_behavioral = {ob.obligation_id for ob in obligations if ob.computed_layer == "behavioral"}
    new_allowlist_size = len(new_behavioral - covered)
    current_allowlist = set()
    if ALLOWLIST_FILE.exists():
        current_allowlist = set(json.loads(ALLOWLIST_FILE.read_text()))

    print()
    print("=" * 72)
    print("ALLOWLIST IMPACT")
    print("=" * 72)
    print(f"  Current allowlist:  {len(current_allowlist)}")
    print(f"  New allowlist:      {new_allowlist_size}")
    print(f"  Reduction:          {len(current_allowlist) - new_allowlist_size}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Reclassify obligation layers (behavioral vs schema)")
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print classification summary (no file changes)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Update **Layer** tags in obligation docs",
    )
    parser.add_argument(
        "--regenerate-allowlist",
        action="store_true",
        help="Rewrite allowlist for behavioral-only obligations",
    )
    args = parser.parse_args()

    if not args.report and not args.apply and not args.regenerate_allowlist:
        print("Error: specify at least one of --report, --apply, --regenerate-allowlist")
        sys.exit(1)

    # Parse and classify
    obligations = extract_all_obligations()
    if not obligations:
        print("Error: no obligations found in docs/test-obligations/")
        sys.exit(1)

    for ob in obligations:
        classify(ob)

    if args.report:
        print_report(obligations)

    if args.apply:
        changes = apply_layer_tags(obligations)
        print(f"\nApplied {changes} layer tag changes across obligation docs.")

    if args.regenerate_allowlist:
        new_size = regenerate_allowlist(obligations)
        print(f"\nRegenerated allowlist with {new_size} entries.")


if __name__ == "__main__":
    main()
