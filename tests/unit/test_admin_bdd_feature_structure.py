"""Structural test: BR-ADMIN-ACCOUNTS feature file exists and has expected scenarios.

Validates that the admin accounts BDD feature file is well-formed and
contains the minimum required scenario coverage.

beads: salesagent-oj0.1.1
"""

from __future__ import annotations

import re
from pathlib import Path

FEATURE_FILE = Path(__file__).resolve().parents[2] / "tests" / "bdd" / "features" / "BR-ADMIN-ACCOUNTS.feature"

# Minimum scenarios required per task description
MIN_SCENARIOS = 8

# Required coverage areas (at least one scenario tag must match each)
REQUIRED_AREAS = {
    "list",  # List accounts
    "create",  # Create account
    "detail",  # View detail
    "edit",  # Edit account
    "status",  # Status change
    "filter",  # Filter by status
    "validation",  # Input validation
    "auth",  # Auth required
}


class TestAdminBddFeatureStructure:
    """Verify BR-ADMIN-ACCOUNTS.feature meets structural requirements."""

    def test_feature_file_exists(self) -> None:
        assert FEATURE_FILE.exists(), (
            f"Feature file not found at {FEATURE_FILE}. Task salesagent-oj0.1.1 requires creating this file."
        )

    def test_minimum_scenario_count(self) -> None:
        text = FEATURE_FILE.read_text()
        scenario_count = len(re.findall(r"^\s*Scenario(?: Outline)?:", text, re.MULTILINE))
        assert scenario_count >= MIN_SCENARIOS, f"Feature has {scenario_count} scenarios, need >= {MIN_SCENARIOS}"

    def test_required_area_coverage(self) -> None:
        text = FEATURE_FILE.read_text()
        tags = set(re.findall(r"@(\w+)", text))
        missing = REQUIRED_AREAS - tags
        assert not missing, (
            f"Feature file missing coverage for areas: {missing}. "
            "Each area must appear as a @tag on at least one scenario."
        )

    def test_has_background_section(self) -> None:
        text = FEATURE_FILE.read_text()
        assert "Background:" in text, "Feature file must have a Background section"

    def test_tag_prefix_convention(self) -> None:
        text = FEATURE_FILE.read_text()
        scenario_tags = re.findall(r"@(T-ADMIN-ACCT-\d+)", text)
        assert len(scenario_tags) >= MIN_SCENARIOS, (
            f"Found {len(scenario_tags)} @T-ADMIN-ACCT-xxx tags, need >= {MIN_SCENARIOS}"
        )
