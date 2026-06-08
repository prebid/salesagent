#!/usr/bin/env python3
"""
Unit test for sync_creatives assignment reporting.

Verifies that when creatives are assigned to packages, the response properly
reports which packages each creative was assigned to via the assigned_to field.
"""

import pytest

from src.core.schemas import SyncCreativeResult


@pytest.mark.unit
class TestSyncCreativesAssignmentReporting:
    """Test that sync_creatives response includes assignment information."""

    def test_sync_creative_result_has_assignment_fields(self):
        """Test that SyncCreativeResult schema includes assigned_to and assignment_errors fields."""
        # Create a result with assignments
        result = SyncCreativeResult(
            creative_id="test_creative_1",
            action="created",
            assigned_to=["pkg_1", "pkg_2"],
        )

        assert result.creative_id == "test_creative_1"
        assert result.action == "created"
        assert result.assigned_to == ["pkg_1", "pkg_2"]
        assert result.assignment_errors is None

    def test_sync_creative_result_with_assignment_errors(self):
        """Test that SyncCreativeResult can include assignment errors."""
        # Create a result with assignment errors
        result = SyncCreativeResult(
            creative_id="test_creative_2",
            action="created",
            assigned_to=["pkg_1"],
            assignment_errors={"pkg_2": "Package not found: pkg_2"},
        )

        assert result.creative_id == "test_creative_2"
        assert result.action == "created"
        assert result.assigned_to == ["pkg_1"]
        assert result.assignment_errors == {"pkg_2": "Package not found: pkg_2"}

    def test_sync_creative_result_without_assignments(self):
        """Test that SyncCreativeResult works without assignment fields."""
        # Create a result without assignments (creative sync only)
        result = SyncCreativeResult(
            creative_id="test_creative_3",
            action="updated",
            changes=["name", "media_url"],
        )

        assert result.creative_id == "test_creative_3"
        assert result.action == "updated"
        assert result.changes == ["name", "media_url"]
        assert result.assigned_to is None
        assert result.assignment_errors is None

    def test_sync_creative_result_serialization(self):
        """Test that SyncCreativeResult properly serializes assignment fields."""
        # Create a result with all fields
        result = SyncCreativeResult(
            creative_id="test_creative_4",
            action="created",
            status="approved",
            platform_id="platform_123",
            assigned_to=["pkg_1", "pkg_2", "pkg_3"],
        )

        # Serialize to dict (model_dump() defaults to exclude_none=True per AdCP spec)
        result_dict = result.model_dump()

        assert result_dict["creative_id"] == "test_creative_4"
        assert result_dict["action"] == "created"
        assert result_dict["assigned_to"] == ["pkg_1", "pkg_2", "pkg_3"]
        # assignment_errors is None, so excluded by default (exclude_none=True)
        assert "assignment_errors" not in result_dict

    def test_sync_creative_result_excludes_none_assignments(self):
        """Test that None assignment fields can be excluded from serialization."""
        # Create a result without assignments
        result = SyncCreativeResult(
            creative_id="test_creative_5",
            action="unchanged",
        )

        # Serialize excluding None values
        result_dict = result.model_dump(exclude_none=True)

        assert result_dict["creative_id"] == "test_creative_5"
        assert result_dict["action"] == "unchanged"
        assert "assigned_to" not in result_dict  # Excluded because None
        assert "assignment_errors" not in result_dict  # Excluded because None
