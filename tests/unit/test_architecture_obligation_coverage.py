"""Guard: Behavioral obligations must have test coverage.

Every obligation tagged with ``**Layer** behavioral`` in docs/test-obligations/
must either:
1. Have a matching ``Covers: <obligation-id>`` in a test (integration or unit), OR
2. Be listed in the KNOWN_UNCOVERED allowlist (JSON file)

The guard scans:
- Integration tests: tests/integration/test_*_v3.py + behavioral files
- Unit entity tests: tests/unit/test_media_buy.py, test_creative.py, test_delivery.py

The allowlist can only SHRINK — adding new uncovered obligations fails CI.
When tests are written, remove the covered ID from the allowlist
(the stale-entry test enforces this).

See: scripts/tag_obligation_ids.py (assigns IDs to obligation docs)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

OBLIGATIONS_DIR = Path(__file__).resolve().parents[2] / "docs" / "test-obligations"
INTEGRATION_DIR = Path(__file__).resolve().parents[2] / "tests" / "integration"
UNIT_DIR = Path(__file__).resolve().parents[2] / "tests" / "unit"
ALLOWLIST_FILE = Path(__file__).resolve().parent / "obligation_coverage_allowlist.json"

# Unit test entity files that carry Covers: tags
_UNIT_ENTITY_FILES = [
    "test_media_buy.py",
    "test_creative.py",
    "test_create_media_buy_behavioral.py",
    "test_update_media_buy_behavioral.py",
    "test_delivery.py",
    "test_delivery_poll_behavioral.py",
    "test_delivery_service_behavioral.py",
    "test_webhook_delivery_service.py",
    "test_product.py",
    "test_product_schema_obligations.py",
    "test_property_list_schema.py",
    "test_quiet_failure_propagation.py",
    "test_get_products_impl_coverage.py",
    "test_creative_formats_behavioral.py",
]

# Integration behavioral test files (non-v3) that carry canonical Covers: tags
_INTEGRATION_BEHAVIORAL_FILES = [
    "test_creative_repository.py",
    "test_creative_formats_behavioral.py",
    "test_creative_sync_behavioral.py",
    "test_creative_sync_data_preservation.py",
    "test_creative_sync_transport.py",
]

# Obligation ID pattern: PREFIX-SECTION-SEQ (e.g., UC-002-MAIN-01, BR-RULE-006-01)
_OBLIGATION_ID_RE = re.compile(r"[A-Z][A-Z0-9]+-[\w-]+-\d{2}")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _get_all_obligation_ids() -> set[str]:
    """Extract every ``**Obligation ID** <id>`` from obligation docs."""
    ids: set[str] = set()
    for md in OBLIGATIONS_DIR.glob("*.md"):
        for m in re.finditer(r"\*\*Obligation ID\*\*\s+(\S+)", md.read_text()):
            ids.add(m.group(1))
    return ids


def _get_behavioral_obligations() -> set[str]:
    """Get obligation IDs where ``**Layer** behavioral``."""
    ids: set[str] = set()
    for md in OBLIGATIONS_DIR.glob("*.md"):
        content = md.read_text()
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "**Obligation ID**" in line:
                m = re.search(r"\*\*Obligation ID\*\*\s+(\S+)", line)
                if not m:
                    continue
                oid = m.group(1)
                # Check next line for Layer tag
                if i + 1 < len(lines) and "**Layer** behavioral" in lines[i + 1]:
                    ids.add(oid)
    return ids


def _get_covered_obligations() -> set[str]:
    """Extract ``Covers: <id>`` tags from test docstrings.

    Scans both integration tests (test_*_v3.py) and unit entity tests.
    Only matches single-line ``Covers: ID`` patterns (not bullet lists).
    """
    covered: set[str] = set()

    def _scan_file(path: Path) -> None:
        for line in path.read_text().splitlines():
            m = re.search(r"Covers:\s+([\w-]+)", line)
            if m and _OBLIGATION_ID_RE.match(m.group(1)):
                covered.add(m.group(1))

    # Integration tests (v3 behavioral files with formal obligation IDs)
    for tf in INTEGRATION_DIR.glob("test_*_v3.py"):
        _scan_file(tf)
    for tf in INTEGRATION_DIR.glob("test_*_behavioral.py"):
        _scan_file(tf)

    # Integration behavioral tests (non-v3 files with canonical Covers: tags)
    for name in _INTEGRATION_BEHAVIORAL_FILES:
        tf = INTEGRATION_DIR / name
        if tf.exists():
            _scan_file(tf)

    # All integration tests (includes former integration_v2 files)
    for tf in INTEGRATION_DIR.glob("test_*.py"):
        _scan_file(tf)

    # Unit entity tests
    for name in _UNIT_ENTITY_FILES:
        tf = UNIT_DIR / name
        if tf.exists():
            _scan_file(tf)

    return covered


def _get_scenario_lines() -> list[tuple[str, int, str]]:
    """Find all ``#### Scenario:`` lines across UC/BR-UC docs.

    Returns list of (filename, line_number, line_text).
    """
    results = []
    for md in sorted(OBLIGATIONS_DIR.glob("*.md")):
        if md.name in ("business-rules.md", "constraints.md"):
            continue
        lines = md.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#### Scenario:"):
                results.append((md.name, i + 1, line.strip()))
    return results


def _load_allowlist() -> set[str]:
    """Load the known-uncovered allowlist from JSON."""
    if not ALLOWLIST_FILE.exists():
        return set()
    return set(json.loads(ALLOWLIST_FILE.read_text()))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestObligationCoverage:
    """Structural guard: behavioral obligations must have test coverage."""

    def test_no_new_uncovered_behavioral_obligations(self):
        """Every behavioral obligation has a test or is in the allowlist.

        Adding a new scenario to the obligation docs without a corresponding
        test (or allowlist entry) fails this test.
        """
        behavioral = _get_behavioral_obligations()
        covered = _get_covered_obligations()
        allowlist = _load_allowlist()

        uncovered_and_not_allowed = behavioral - covered - allowlist

        assert not uncovered_and_not_allowed, (
            f"Found {len(uncovered_and_not_allowed)} behavioral obligation(s) with no "
            f"test and not in the allowlist.\n"
            f"Either write a test with 'Covers: <id>' or add to the "
            f"allowlist (obligation_coverage_allowlist.json):\n"
            + "\n".join(f"  {oid}" for oid in sorted(uncovered_and_not_allowed))
        )

    def test_known_uncovered_are_still_obligations(self):
        """Every allowlist entry must reference a real obligation ID.

        Prevents the allowlist from containing phantom entries.
        """
        all_ids = _get_all_obligation_ids()
        allowlist = _load_allowlist()

        phantom = allowlist - all_ids
        assert not phantom, (
            f"Found {len(phantom)} allowlist entries that don't match any obligation ID.\n"
            f"These IDs may have been renamed or removed from the obligation docs:\n"
            + "\n".join(f"  {oid}" for oid in sorted(phantom))
        )

    def test_known_uncovered_not_already_covered(self):
        """If an obligation is covered by a test, remove it from the allowlist.

        Prevents the allowlist from becoming stale when tests are written.
        """
        covered = _get_covered_obligations()
        allowlist = _load_allowlist()

        stale = allowlist & covered
        assert not stale, (
            f"Found {len(stale)} obligation(s) in the allowlist that already have "
            f"tests.\n"
            f"Remove these from obligation_coverage_allowlist.json:\n" + "\n".join(f"  {oid}" for oid in sorted(stale))
        )

    def test_all_scenarios_have_obligation_ids(self):
        """Every ``#### Scenario:`` in UC/BR-UC docs must have an ``**Obligation ID**`` tag.

        Run ``python scripts/tag_obligation_ids.py`` to fix untagged scenarios.
        """
        scenarios = _get_scenario_lines()
        untagged = []

        for md in sorted(OBLIGATIONS_DIR.glob("*.md")):
            if md.name in ("business-rules.md", "constraints.md"):
                continue
            lines = md.read_text().splitlines()
            for i, line in enumerate(lines):
                if line.startswith("#### Scenario:"):
                    if i + 1 >= len(lines) or "**Obligation ID**" not in lines[i + 1]:
                        untagged.append(f"  {md.name}:{i + 1}: {line.strip()}")

        assert not untagged, (
            f"Found {len(untagged)} scenario(s) without **Obligation ID** tags.\n"
            f"Run: python scripts/tag_obligation_ids.py\n" + "\n".join(untagged)
        )

    def test_no_duplicate_obligation_ids(self):
        """Obligation IDs must be unique across all docs."""
        seen: dict[str, list[str]] = {}
        for md in sorted(OBLIGATIONS_DIR.glob("*.md")):
            content = md.read_text()
            for m in re.finditer(r"\*\*Obligation ID\*\*\s+(\S+)", content):
                oid = m.group(1)
                seen.setdefault(oid, []).append(md.name)

        duplicates = {oid: files for oid, files in seen.items() if len(files) > 1}
        assert not duplicates, f"Found {len(duplicates)} duplicate obligation ID(s):\n" + "\n".join(
            f"  {oid}: {', '.join(files)}" for oid, files in sorted(duplicates.items())
        )

    def test_tests_reference_valid_obligations(self):
        """``Covers:`` tags in tests must reference real obligation IDs."""
        all_ids = _get_all_obligation_ids()
        covered = _get_covered_obligations()

        invalid = covered - all_ids
        assert not invalid, (
            f"Found {len(invalid)} Covers: tag(s) referencing non-existent obligation IDs:\n"
            + "\n".join(f"  {oid}" for oid in sorted(invalid))
        )

    def test_obligation_count_documented(self):
        """Track the total obligation and coverage counts for monitoring."""
        all_ids = _get_all_obligation_ids()
        behavioral = _get_behavioral_obligations()
        covered = _get_covered_obligations()
        allowlist = _load_allowlist()

        # Informational — prints counts in verbose mode
        print(f"\n  Obligation IDs:   {len(all_ids)} total")
        print(f"  Behavioral:       {len(behavioral)}")
        print(f"  Covered:          {len(covered)} ({len(covered)}/{len(behavioral)} behavioral)")
        print(f"  Allowlisted:      {len(allowlist)}")
        print(f"  Gap:              {len(behavioral - covered - allowlist)}")

        # Allowlist must exactly match the uncovered behavioral set
        expected_allowlist_size = len(behavioral - covered)
        assert len(allowlist) == expected_allowlist_size, (
            f"Allowlist size ({len(allowlist)}) doesn't match uncovered behavioral count "
            f"({expected_allowlist_size}). Update the allowlist with:\n"
            f"  python scripts/tag_obligation_ids.py && "
            f"regenerate obligation_coverage_allowlist.json"
        )
