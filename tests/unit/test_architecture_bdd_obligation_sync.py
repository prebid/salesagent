"""Guard: BDD feature files and obligation docs must stay in sync.

Ensures bidirectional consistency between:
- tests/bdd/features/*.feature (compiled BDD scenarios)
- docs/test-obligations/bdd-traceability.yaml (traceability mapping)
- docs/test-obligations/*.md (obligation docs)

All tests are NO-OPs when tests/bdd/features/ contains no .feature files
(Phase 0 state -- guard activates only when BDD features exist).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.bdd_traceability_schema import BDDTraceabilityMapping

FEATURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "bdd" / "features"
OBLIGATIONS_DIR = Path(__file__).resolve().parents[2] / "docs" / "test-obligations"
TRACEABILITY_FILE = OBLIGATIONS_DIR / "bdd-traceability.yaml"

# Pattern matching Scenario: or Scenario Outline: lines in .feature files
_SCENARIO_RE = re.compile(r"^\s*Scenario(?: Outline)?:\s*(.+)$")

# Tag lines preceding scenarios (e.g., @T-UC-005-main-mcp)
_TAG_RE = re.compile(r"@(T-[\w-]+)")

# Obligation ID pattern (matches IDs in obligation docs)
_OBLIGATION_ID_RE = re.compile(r"[A-Z][A-Z0-9]+-[\w-]+-\d{2}")

# Generation stamp that compile_bdd.py writes at the top of each .feature file
_GENERATION_STAMP_PREFIX = "# Generated from adcp-req"
# Hand-authored features (not compiled) use a different stamp
_HAND_AUTHORED_STAMP_PREFIX = "# Hand-authored feature"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _get_feature_files() -> list[Path]:
    """Return all .feature files in the BDD features directory."""
    if not FEATURES_DIR.is_dir():
        return []
    return sorted(FEATURES_DIR.glob("*.feature"))


def _parse_scenario_ids_from_features() -> dict[str, str]:
    """Extract scenario IDs from .feature files.

    Returns dict mapping scenario_id -> feature filename.
    Scenario IDs come from @T-* tags on the line(s) preceding a Scenario line.
    """
    scenario_ids: dict[str, str] = {}
    for feature_file in _get_feature_files():
        lines = feature_file.read_text().splitlines()
        pending_tags: list[str] = []
        for line in lines:
            stripped = line.strip()
            # Collect tags from tag lines
            if stripped.startswith("@"):
                for m in _TAG_RE.finditer(stripped):
                    pending_tags.append(m.group(1))
            elif _SCENARIO_RE.match(stripped):
                # Associate all pending tags with this scenario
                for tag in pending_tags:
                    scenario_ids[tag] = feature_file.name
                pending_tags = []
            elif not stripped.startswith("#") and stripped:
                # Non-tag, non-scenario line — reset pending tags
                pending_tags = []
    return scenario_ids


def _parse_scenario_names_from_features() -> list[tuple[str, str]]:
    """Extract (feature_filename, scenario_name) pairs from .feature files."""
    scenarios: list[tuple[str, str]] = []
    for feature_file in _get_feature_files():
        for line in feature_file.read_text().splitlines():
            m = _SCENARIO_RE.match(line.strip())
            if m:
                scenarios.append((feature_file.name, m.group(1).strip()))
    return scenarios


def _load_traceability() -> BDDTraceabilityMapping:
    """Load and validate bdd-traceability.yaml."""
    content = yaml.safe_load(TRACEABILITY_FILE.read_text())
    return BDDTraceabilityMapping.model_validate(content)


def _get_all_obligation_ids() -> set[str]:
    """Extract every ``**Obligation ID** <id>`` from obligation docs."""
    ids: set[str] = set()
    for md in OBLIGATIONS_DIR.glob("*.md"):
        for m in re.finditer(r"\*\*Obligation ID\*\*\s+(\S+)", md.read_text()):
            ids.add(m.group(1))
    return ids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBDDObligationSync:
    """Structural guard: BDD features and obligation docs must stay in sync."""

    def test_bdd_scenarios_have_traceability_entries(self):
        """Every Scenario in tests/bdd/features/ must have a traceability entry.

        Parses all Scenario/Scenario Outline lines from compiled .feature files
        and verifies each scenario's @T-* tag exists in bdd-traceability.yaml.

        No-op when no .feature files exist (Phase 0).
        """
        feature_files = _get_feature_files()
        if not feature_files:
            return  # Phase 0: no compiled features yet

        scenario_ids = _parse_scenario_ids_from_features()
        if not scenario_ids:
            return  # Features exist but have no tagged scenarios

        traceability = _load_traceability()

        # Collect all adcp_scenario_ids from traceability mappings
        mapped_ids: set[str] = set()
        for entries in traceability.mappings.values():
            for entry in entries:
                mapped_ids.add(entry.adcp_scenario_id)

        missing = set(scenario_ids.keys()) - mapped_ids
        assert not missing, (
            f"Found {len(missing)} BDD scenario(s) with no traceability entry.\n"
            f"Run compile_bdd.py to update bdd-traceability.yaml, or add entries manually:\n"
            + "\n".join(f"  {sid} (in {scenario_ids[sid]})" for sid in sorted(missing))
        )

    def test_traceability_mapped_obligations_exist(self):
        """Every mapped entry in traceability must reference a real obligation ID.

        Entries with status=mapped must have an obligation_id that exists
        in docs/test-obligations/*.md.

        No-op when mappings is empty.
        """
        traceability = _load_traceability()
        all_entries = [entry for entries in traceability.mappings.values() for entry in entries]
        if not all_entries:
            return  # No mappings yet

        all_obligation_ids = _get_all_obligation_ids()

        invalid: list[str] = []
        for _uc_key, entries in sorted(traceability.mappings.items()):
            for entry in entries:
                if entry.status.value == "mapped" and entry.obligation_id:
                    if entry.obligation_id not in all_obligation_ids:
                        invalid.append(
                            f"  {entry.adcp_scenario_id} -> {entry.obligation_id} (not found in obligation docs)"
                        )

        assert not invalid, (
            f"Found {len(invalid)} traceability entry/entries with status=mapped "
            f"referencing non-existent obligation IDs:\n" + "\n".join(invalid)
        )

    def test_traceability_has_no_phantom_scenarios(self):
        """Every adcp_scenario_id in traceability must exist in a compiled feature file.

        Prevents stale entries from accumulating in bdd-traceability.yaml
        after scenarios are removed upstream.

        No-op when mappings is empty AND no .feature files exist.
        """
        traceability = _load_traceability()
        feature_files = _get_feature_files()

        all_entries = [entry for entries in traceability.mappings.values() for entry in entries]
        if not all_entries and not feature_files:
            return  # Phase 0: nothing to check

        if not feature_files and all_entries:
            # Traceability has entries but no features compiled yet —
            # this is valid during the transition period
            return

        scenario_ids = _parse_scenario_ids_from_features()

        phantom: list[str] = []
        for uc_key, entries in sorted(traceability.mappings.items()):
            for entry in entries:
                if entry.adcp_scenario_id not in scenario_ids:
                    phantom.append(f"  {entry.adcp_scenario_id} (in {uc_key}, not found in any .feature file)")

        assert not phantom, (
            f"Found {len(phantom)} traceability entry/entries referencing "
            f"scenarios not in any compiled .feature file:\n"
            + "\n".join(phantom)
            + "\n\nRemove stale entries or re-run compile_bdd.py."
        )

    def test_compiled_features_have_generation_stamps(self):
        """Every .feature file must start with a compile_bdd.py generation stamp.

        Prevents manual edits to compiled files that compile_bdd.py would
        overwrite on the next run.

        No-op when no .feature files exist (Phase 0).
        """
        feature_files = _get_feature_files()
        if not feature_files:
            return  # Phase 0: no compiled features yet

        missing_stamp: list[str] = []
        for feature_file in feature_files:
            first_line = feature_file.read_text().split("\n", 1)[0]
            if not (
                first_line.startswith(_GENERATION_STAMP_PREFIX) or first_line.startswith(_HAND_AUTHORED_STAMP_PREFIX)
            ):
                missing_stamp.append(f"  {feature_file.name}")

        assert not missing_stamp, (
            f"Found {len(missing_stamp)} .feature file(s) without generation stamps.\n"
            f"Compiled features must start with '{_GENERATION_STAMP_PREFIX}'.\n"
            f"Hand-authored features must start with '{_HAND_AUTHORED_STAMP_PREFIX}'.\n"
            f"Unrecognized:\n" + "\n".join(missing_stamp)
        )

    def test_traceability_yaml_validates(self):
        """bdd-traceability.yaml must validate against the Pydantic schema.

        Always runs — the traceability file must always be valid YAML
        conforming to the BDDTraceabilityMapping schema.
        """
        content = yaml.safe_load(TRACEABILITY_FILE.read_text())
        # model_validate raises ValidationError on schema violation
        mapping = BDDTraceabilityMapping.model_validate(content)

        # Basic structural assertions
        assert mapping.schema_version >= 1, f"schema_version must be >= 1, got {mapping.schema_version}"
        assert isinstance(mapping.mappings, dict), f"mappings must be a dict, got {type(mapping.mappings).__name__}"
