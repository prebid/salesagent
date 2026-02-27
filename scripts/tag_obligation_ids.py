#!/usr/bin/env python3
"""Auto-assign obligation IDs to test obligation docs.

Parses all docs/test-obligations/*.md files and assigns stable,
machine-parseable obligation IDs to every obligation item:
- UC/BR-UC docs: each ``#### Scenario:`` block gets an ID
- business-rules.md: each ``### BR-RULE-NNN:`` section gets an ID
- constraints.md: each ``### slug-name:`` section gets an ID

IDs follow the format ``{DOC_PREFIX}-{SECTION_SLUG}-{SEQ:02d}``.

Usage::

    python scripts/tag_obligation_ids.py              # Tag all files
    python scripts/tag_obligation_ids.py --dry-run    # Preview only
    python scripts/tag_obligation_ids.py --verify     # Check all tagged
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OBLIGATIONS_DIR = Path(__file__).resolve().parents[1] / "docs" / "test-obligations"

# ---------------------------------------------------------------------------
# Slugification helpers
# ---------------------------------------------------------------------------


def _slugify(text: str, max_words: int = 3) -> str:
    """Turn a heading fragment into a concise uppercase slug."""
    # Strip parenthesized content and markdown chars
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"[*`\[\]#]", "", text)
    text = text.strip(" :-\u2013\u2014")
    words = re.findall(r"[A-Za-z0-9]+", text)
    # Take first N significant words (skip tiny filler words after first)
    significant: list[str] = []
    for w in words:
        if len(w) > 2 or not significant:
            significant.append(w)
        if len(significant) >= max_words:
            break
    return "-".join(w.upper() for w in significant)


# ---------------------------------------------------------------------------
# Doc-prefix extraction
# ---------------------------------------------------------------------------


def _get_doc_prefix(filename: str) -> str:
    """Extract doc prefix from obligation doc filename."""
    if filename == "business-rules.md":
        return "BR"
    if filename == "constraints.md":
        return "CONSTR"
    # UC-001-xxx.md → UC-001, BR-UC-006-xxx.md → UC-006
    m = re.match(r"(?:BR-)?UC-(\d+)", filename)
    if m:
        return f"UC-{m.group(1)}"
    return _slugify(filename.replace(".md", ""))


# ---------------------------------------------------------------------------
# Section-slug extraction (for ### headings in UC/BR-UC docs)
# ---------------------------------------------------------------------------


def _section_to_slug(heading: str) -> str:
    """Turn a ``###`` heading into a section slug for obligation IDs."""
    heading = heading.strip()

    # Extension *a / Extension A / Extension *k
    m = re.match(r"Extension\s+\*?([a-zA-Z])\s*:", heading)
    if m:
        return f"EXT-{m.group(1).upper()}"

    # Main Flow (MCP) / Main Flow (REST...) / Main Flow
    if re.search(r"Main Flow\s*\(MCP\)", heading):
        return "MAIN-MCP"
    if re.search(r"Main Flow\s*\(REST", heading):
        return "MAIN-REST"
    if heading.startswith("Main Flow"):
        return "MAIN"

    # Preconditions / Postconditions
    if re.match(r"Precondition", heading):
        return "PRECOND"
    if re.match(r"Postcondition", heading):
        return "POST"

    # Alternative / Alt flows
    m = re.match(r"Alt(?:ernative)?\s*(?:Flow)?\s*:\s*(.+)", heading, re.I)
    if m:
        return f"ALT-{_slugify(m.group(1))}"

    # Cross-Cutting
    m = re.match(r"Cross-Cutting:\s*(.+)", heading)
    if m:
        return f"CC-{_slugify(m.group(1))}"

    # Business Rule (inline in UC docs)
    m = re.match(r"Business Rule:\s*(.+)", heading)
    if m:
        return f"BR-{_slugify(m.group(1))}"

    # NFR-NNN
    m = re.match(r"(NFR-\d+)", heading)
    if m:
        return m.group(1)

    # Schema Compliance (standalone section)
    if re.match(r"Schema Compliance", heading):
        return "SCHEMA"

    # 3.6 Upgrade sections
    if re.match(r"3\.6 Upgrade", heading):
        return "UPG"

    # Generic fallback
    return _slugify(heading)


# ---------------------------------------------------------------------------
# Constraint section slug (for constraints.md ### headings)
# ---------------------------------------------------------------------------


def _constraint_section_slug(heading: str) -> str:
    """Turn a constraints.md ``### slug_name: Title`` into a slug.

    Uses the full slug before the colon to avoid truncation collisions
    (e.g., create-media-buy-request vs create-media-buy-response).
    """
    m = re.match(r"([a-zA-Z][a-zA-Z0-9_/\-]+)\s*:", heading)
    if m:
        return m.group(1).upper().replace("/", "-").replace("_", "-")
    return _slugify(heading)


# ---------------------------------------------------------------------------
# Tagging: UC / BR-UC scenario docs
# ---------------------------------------------------------------------------


def _tag_scenario_doc(filepath: Path, dry_run: bool) -> dict[str, list[str]]:
    """Tag ``#### Scenario:`` blocks in a UC or BR-UC obligation doc.

    Returns dict mapping section_slug -> list of assigned obligation IDs.
    """
    doc_prefix = _get_doc_prefix(filepath.name)
    lines = filepath.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    assigned: dict[str, list[str]] = {}

    current_section_slug: str | None = None
    section_counters: dict[str, int] = {}  # slug -> next seq
    in_test_scenarios = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip("\n")

        # Track ## Test Scenarios boundary (only tag scenarios after this)
        if stripped.startswith("## Test Scenarios"):
            in_test_scenarios = True
            new_lines.append(line)
            i += 1
            continue

        # Track ### section headings (only within Test Scenarios, or always
        # for docs that don't have a ## Test Scenarios header)
        if stripped.startswith("### ") and not stripped.startswith("#### "):
            heading_text = stripped[4:].strip()
            # Skip non-scenario sections before Test Scenarios
            if in_test_scenarios or not _is_impact_section(heading_text):
                current_section_slug = _section_to_slug(heading_text)
            new_lines.append(line)
            i += 1
            continue

        # Process #### Scenario: lines
        if stripped.startswith("#### Scenario:"):
            # Check if next line already has an Obligation ID tag
            has_id = False
            if i + 1 < len(lines) and "**Obligation ID**" in lines[i + 1]:
                has_id = True

            if has_id:
                # Already tagged — extract the ID for reporting
                m = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", lines[i + 1])
                if m:
                    slug = current_section_slug or "UNKNOWN"
                    assigned.setdefault(slug, []).append(m.group(1))
                new_lines.append(line)
                i += 1
                continue

            # Assign new ID
            slug = current_section_slug or "UNKNOWN"
            seq = section_counters.get(slug, 0) + 1
            section_counters[slug] = seq
            obligation_id = f"{doc_prefix}-{slug}-{seq:02d}"

            assigned.setdefault(slug, []).append(obligation_id)

            # Write: scenario line, then Obligation ID, then Layer
            new_lines.append(line)
            new_lines.append(f"**Obligation ID** {obligation_id}\n")
            new_lines.append("**Layer** behavioral\n")
            i += 1
            continue

        new_lines.append(line)
        i += 1

    if not dry_run:
        filepath.write_text("".join(new_lines))

    return assigned


def _is_impact_section(heading: str) -> bool:
    """Check if a ### heading is a 3.6 Upgrade Impact sub-section (not test scenarios)."""
    return bool(
        re.match(r"salesagent-", heading)
        or re.match(r"Bug:", heading)
        or re.match(r"Referenced Business Rules", heading)
        or re.match(r"Upgrade test priorities", heading)
        or re.match(r"Schema changes", heading)
        or re.match(r"Filter Fields Impact", heading)
    )


# ---------------------------------------------------------------------------
# Tagging: business-rules.md
# ---------------------------------------------------------------------------


def _tag_business_rules(filepath: Path, dry_run: bool) -> dict[str, list[str]]:
    """Tag ``### BR-RULE-NNN:`` sections in business-rules.md."""
    lines = filepath.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    assigned: dict[str, list[str]] = {}
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip("\n")

        m = re.match(r"^### (BR-RULE-\d+):", stripped)
        if m:
            rule_id = m.group(1)
            # Check if next line already has Obligation ID
            has_id = False
            if i + 1 < len(lines) and "**Obligation ID**" in lines[i + 1]:
                has_id = True

            if has_id:
                m2 = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", lines[i + 1])
                if m2:
                    assigned.setdefault(rule_id, []).append(m2.group(1))
                new_lines.append(line)
                i += 1
                continue

            # Assign ID: BR-RULE-NNN-01 (one obligation per rule section)
            obligation_id = f"{rule_id}-01"
            assigned.setdefault(rule_id, []).append(obligation_id)

            new_lines.append(line)
            new_lines.append(f"**Obligation ID** {obligation_id}\n")
            new_lines.append("**Layer** behavioral\n")
            i += 1
            continue

        new_lines.append(line)
        i += 1

    if not dry_run:
        filepath.write_text("".join(new_lines))

    return assigned


# ---------------------------------------------------------------------------
# Tagging: constraints.md
# ---------------------------------------------------------------------------


def _tag_constraints(filepath: Path, dry_run: bool) -> dict[str, list[str]]:
    """Tag ``### slug_name: Title`` sections in constraints.md."""
    lines = filepath.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    assigned: dict[str, list[str]] = {}
    in_constraints = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip("\n")

        # Only tag sections after ## Constraints
        if stripped.startswith("## Constraints"):
            in_constraints = True
            new_lines.append(line)
            i += 1
            continue

        if in_constraints and stripped.startswith("### "):
            heading_text = stripped[4:].strip()
            slug = _constraint_section_slug(heading_text)

            # Check if next line already has Obligation ID
            has_id = False
            if i + 1 < len(lines) and "**Obligation ID**" in lines[i + 1]:
                has_id = True

            if has_id:
                m = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", lines[i + 1])
                if m:
                    assigned.setdefault(slug, []).append(m.group(1))
                new_lines.append(line)
                i += 1
                continue

            obligation_id = f"CONSTR-{slug}-01"
            assigned.setdefault(slug, []).append(obligation_id)

            new_lines.append(line)
            new_lines.append(f"**Obligation ID** {obligation_id}\n")
            new_lines.append("**Layer** behavioral\n")
            i += 1
            continue

        new_lines.append(line)
        i += 1

    if not dry_run:
        filepath.write_text("".join(new_lines))

    return assigned


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Tag obligation docs with IDs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--verify", action="store_true", help="Check all scenarios are tagged")
    args = parser.parse_args()

    all_ids: list[str] = []
    total_tagged = 0

    for md_file in sorted(OBLIGATIONS_DIR.glob("*.md")):
        if md_file.name == "business-rules.md":
            assigned = _tag_business_rules(md_file, dry_run=args.dry_run)
        elif md_file.name == "constraints.md":
            assigned = _tag_constraints(md_file, dry_run=args.dry_run)
        else:
            assigned = _tag_scenario_doc(md_file, dry_run=args.dry_run)

        file_total = sum(len(ids) for ids in assigned.values())
        total_tagged += file_total

        # Collect all IDs for uniqueness check
        for ids in assigned.values():
            all_ids.extend(ids)

        # Report
        action = "would tag" if args.dry_run else "tagged"
        print(f"{md_file.name}: {action} {file_total} obligations")
        for section, ids in sorted(assigned.items()):
            print(f"  {section}: {', '.join(ids[:3])}{'...' if len(ids) > 3 else ''} ({len(ids)})")

    # Check uniqueness
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for oid in all_ids:
        seen[oid] = seen.get(oid, 0) + 1
    for oid, count in seen.items():
        if count > 1:
            duplicates.append(f"  {oid} (×{count})")

    print(f"\nTotal: {total_tagged} obligation IDs assigned")
    if duplicates:
        print(f"DUPLICATES FOUND ({len(duplicates)}):")
        for d in duplicates:
            print(d)
        sys.exit(1)
    else:
        print("No duplicates — all IDs are unique.")

    if args.verify:
        # Verify all #### Scenario: lines have Obligation ID
        untagged = 0
        for md_file in sorted(OBLIGATIONS_DIR.glob("*.md")):
            if md_file.name in ("business-rules.md", "constraints.md"):
                continue
            lines = md_file.read_text().splitlines()
            for idx, line in enumerate(lines):
                if line.startswith("#### Scenario:"):
                    if idx + 1 >= len(lines) or "**Obligation ID**" not in lines[idx + 1]:
                        print(f"  UNTAGGED: {md_file.name}:{idx + 1}: {line.strip()}")
                        untagged += 1
        if untagged:
            print(f"\n{untagged} untagged scenarios found!")
            sys.exit(1)
        else:
            print("All scenarios are tagged.")


if __name__ == "__main__":
    main()
