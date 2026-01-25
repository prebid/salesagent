"""Unit tests for enhanced custom targeting with OR/AND operators and include/exclude.

Tests the new targeting widget data structure:
{
    'include': {'key_id': ['value1', 'value2']},  # Multiple values = OR within key
    'exclude': {'key_id': ['value3']},
    'operator': 'AND' | 'OR'  # How to combine different keys
}
"""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.gam.managers.targeting import GAMTargetingManager


@pytest.fixture
def mock_adapter_config():
    """Fixture for mocked adapter config with custom targeting keys."""
    mock_config = MagicMock()
    mock_config.axe_include_key = None
    mock_config.axe_exclude_key = None
    mock_config.axe_macro_key = None
    mock_config.custom_targeting_keys = {
        "category": "11111",
        "segment": "22222",
        "brand": "33333",
    }
    return mock_config


@pytest.fixture
def targeting_manager(mock_adapter_config):
    """Create a GAMTargetingManager with mocked dependencies."""
    with patch("src.core.database.database_session.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = mock_adapter_config

        mock_gam_client = MagicMock()
        manager = GAMTargetingManager("tenant_123", gam_client=mock_gam_client)

        # Mock _get_or_create_custom_targeting_value to return predictable IDs
        def mock_get_value(key_id, value_name):
            # Return hash-based ID for consistent testing
            return hash(f"{key_id}:{value_name}") % 1000000

        manager._get_or_create_custom_targeting_value = mock_get_value

        yield manager


class TestEnhancedCustomTargetingStructure:
    """Test suite for enhanced custom targeting structure."""

    def test_legacy_format_still_works(self, targeting_manager):
        """Test that legacy dict[str, str] format still works."""
        legacy_dict = {
            "11111": "sports",
            "22222": "premium",
        }

        result = targeting_manager._build_custom_targeting_structure(legacy_dict)

        assert result["xsi_type"] == "CustomCriteriaSet"
        assert result["logicalOperator"] == "AND"
        assert len(result["children"]) == 2

        # Check both criteria exist
        key_ids = [c["keyId"] for c in result["children"]]
        assert 11111 in key_ids
        assert 22222 in key_ids

        # All should be IS operator
        for child in result["children"]:
            assert child["operator"] == "IS"

    def test_legacy_format_with_not_prefix(self, targeting_manager):
        """Test legacy format with NOT_ prefix for exclusions."""
        legacy_dict = {
            "11111": "sports",
            "NOT_22222": "budget",
        }

        result = targeting_manager._build_custom_targeting_structure(legacy_dict)

        assert len(result["children"]) == 2

        # Find the excluded criteria
        excluded = [c for c in result["children"] if c["keyId"] == 22222]
        assert len(excluded) == 1
        assert excluded[0]["operator"] == "IS_NOT"

        # Find the included criteria
        included = [c for c in result["children"] if c["keyId"] == 11111]
        assert len(included) == 1
        assert included[0]["operator"] == "IS"

    def test_enhanced_format_include_only(self, targeting_manager):
        """Test enhanced format with only include values."""
        enhanced_dict = {
            "include": {
                "11111": ["sports", "entertainment"],
                "22222": ["premium"],
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert result["xsi_type"] == "CustomCriteriaSet"
        assert result["logicalOperator"] == "AND"
        assert len(result["children"]) == 2

        # Check all are IS operators
        for child in result["children"]:
            assert child["operator"] == "IS"

        # Find the multi-value criteria (sports, entertainment)
        category_criteria = [c for c in result["children"] if c["keyId"] == 11111]
        assert len(category_criteria) == 1
        assert len(category_criteria[0]["valueIds"]) == 2  # Two values = OR within key

    def test_enhanced_format_exclude_only(self, targeting_manager):
        """Test enhanced format with only exclude values."""
        enhanced_dict = {
            "exclude": {
                "11111": ["politics", "controversial"],
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert result["xsi_type"] == "CustomCriteriaSet"
        assert result["logicalOperator"] == "AND"
        assert len(result["children"]) == 1

        # Should be IS_NOT operator
        assert result["children"][0]["operator"] == "IS_NOT"
        assert result["children"][0]["keyId"] == 11111
        assert len(result["children"][0]["valueIds"]) == 2  # Two excluded values

    def test_enhanced_format_include_and_exclude(self, targeting_manager):
        """Test enhanced format with both include and exclude."""
        enhanced_dict = {
            "include": {
                "11111": ["sports", "entertainment"],
            },
            "exclude": {
                "22222": ["budget"],
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert result["logicalOperator"] == "AND"
        assert len(result["children"]) == 2

        # Find include criteria
        include_criteria = [c for c in result["children"] if c["operator"] == "IS"]
        assert len(include_criteria) == 1
        assert include_criteria[0]["keyId"] == 11111

        # Find exclude criteria
        exclude_criteria = [c for c in result["children"] if c["operator"] == "IS_NOT"]
        assert len(exclude_criteria) == 1
        assert exclude_criteria[0]["keyId"] == 22222

    def test_enhanced_format_or_operator(self, targeting_manager):
        """Test enhanced format with OR operator between keys."""
        enhanced_dict = {
            "include": {
                "11111": ["sports"],
                "22222": ["premium"],
            },
            "operator": "OR",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert result["logicalOperator"] == "OR"
        assert len(result["children"]) == 2

    def test_enhanced_format_empty_values_ignored(self, targeting_manager):
        """Test that empty value lists are ignored."""
        enhanced_dict = {
            "include": {
                "11111": ["sports"],
                "22222": [],  # Empty - should be ignored
            },
            "exclude": {
                "33333": [],  # Empty - should be ignored
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert len(result["children"]) == 1
        assert result["children"][0]["keyId"] == 11111

    def test_enhanced_format_empty_all_returns_empty(self, targeting_manager):
        """Test that all empty returns empty dict."""
        enhanced_dict = {
            "include": {},
            "exclude": {},
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert result == {}

    def test_enhanced_format_defaults_to_and_operator(self, targeting_manager):
        """Test that operator defaults to AND if not specified."""
        enhanced_dict = {
            "include": {
                "11111": ["sports"],
            },
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert result["logicalOperator"] == "AND"

    def test_multiple_values_per_key_creates_or_logic(self, targeting_manager):
        """Test that multiple values for same key creates OR logic in GAM.

        When valueIds has multiple IDs, GAM interprets this as:
        key IS value1 OR key IS value2
        """
        enhanced_dict = {
            "include": {
                "11111": ["sports", "entertainment", "news"],
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert len(result["children"]) == 1
        criteria = result["children"][0]
        assert criteria["keyId"] == 11111
        assert criteria["operator"] == "IS"
        assert len(criteria["valueIds"]) == 3  # All three values OR'd together


class TestEnhancedCustomTargetingDetection:
    """Test detection of enhanced vs legacy format."""

    def test_detects_enhanced_format_with_include(self, targeting_manager):
        """Test that include key triggers enhanced format handling."""
        enhanced_dict = {"include": {"11111": ["sports"]}}

        # Should not raise error when called with enhanced format
        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)
        assert result["logicalOperator"] == "AND"

    def test_detects_enhanced_format_with_exclude(self, targeting_manager):
        """Test that exclude key triggers enhanced format handling."""
        enhanced_dict = {"exclude": {"11111": ["politics"]}}

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)
        assert result["children"][0]["operator"] == "IS_NOT"

    def test_legacy_format_without_include_exclude(self, targeting_manager):
        """Test that format without include/exclude is treated as legacy."""
        legacy_dict = {"11111": "sports", "22222": "premium"}

        result = targeting_manager._build_custom_targeting_structure(legacy_dict)

        # Should work as legacy format
        assert len(result["children"]) == 2
        for child in result["children"]:
            assert child["operator"] == "IS"
            assert len(child["valueIds"]) == 1  # Single value per key in legacy


class TestCustomTargetingLogicalOperatorPassThrough:
    """Test logical operator parameter is respected."""

    def test_legacy_format_respects_operator_param(self, targeting_manager):
        """Test that logical_operator param works with legacy format."""
        legacy_dict = {"11111": "sports"}

        result = targeting_manager._build_custom_targeting_structure(legacy_dict, logical_operator="OR")

        assert result["logicalOperator"] == "OR"

    def test_enhanced_format_uses_own_operator(self, targeting_manager):
        """Test that enhanced format uses its own operator, ignoring param."""
        enhanced_dict = {
            "include": {"11111": ["sports"]},
            "operator": "OR",
        }

        # Even if we pass AND, enhanced format should use its own OR
        result = targeting_manager._build_custom_targeting_structure(enhanced_dict, logical_operator="AND")

        assert result["logicalOperator"] == "OR"


class TestEnhancedCustomTargetingNumericValueIds:
    """Test handling of numeric value IDs vs value names."""

    def test_numeric_value_ids_used_directly(self, targeting_manager):
        """Test that numeric value IDs are used directly without lookup.

        When values are already GAM IDs (numeric strings), they should be
        converted to integers and used directly without calling
        _get_or_create_custom_targeting_value.
        """
        enhanced_dict = {
            "include": {
                "11111": ["451005167391", "451470637712"],  # Numeric GAM value IDs
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert len(result["children"]) == 1
        criteria = result["children"][0]
        assert criteria["keyId"] == 11111
        # Value IDs should be integers converted from numeric strings
        assert 451005167391 in criteria["valueIds"]
        assert 451470637712 in criteria["valueIds"]

    def test_mixed_numeric_and_name_values(self, targeting_manager):
        """Test handling of mixed numeric IDs and value names."""
        enhanced_dict = {
            "include": {
                "11111": ["451005167391", "sports"],  # Mixed: ID and name
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert len(result["children"]) == 1
        criteria = result["children"][0]
        assert criteria["keyId"] == 11111
        # First value should be the numeric ID, second is looked up
        assert 451005167391 in criteria["valueIds"]
        assert len(criteria["valueIds"]) == 2

    def test_numeric_exclude_ids_used_directly(self, targeting_manager):
        """Test that numeric value IDs work for excludes too."""
        enhanced_dict = {
            "exclude": {
                "22222": ["999888777"],
            },
            "operator": "AND",
        }

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        assert len(result["children"]) == 1
        criteria = result["children"][0]
        assert criteria["keyId"] == 22222
        assert criteria["operator"] == "IS_NOT"
        assert 999888777 in criteria["valueIds"]


class TestNestedGroupsCustomTargeting:
    """Test suite for GAM-style nested groups targeting.

    New data model supports:
    - Multiple groups connected by OR logic
    - Within each group, criteria connected by AND logic
    - Each criterion has key, values (OR'd), and optional exclude flag

    Data format:
    {
        'groups': [
            {
                'criteria': [
                    {'keyId': '123', 'values': ['v1', 'v2']},
                    {'keyId': '456', 'values': ['v3'], 'exclude': True}
                ]
            },
            {
                'criteria': [
                    {'keyId': '789', 'values': ['v4']}
                ]
            }
        ]
    }

    Translates to GAM:
    (key123 IS v1|v2 AND key456 IS_NOT v3) OR (key789 IS v4)
    """

    def test_single_group_single_criterion(self, targeting_manager):
        """Test simplest case: one group with one criterion."""
        groups_dict = {"groups": [{"criteria": [{"keyId": "11111", "values": ["sports"]}]}]}

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        # Single group should still produce OR at top level for consistency
        assert result["xsi_type"] == "CustomCriteriaSet"
        assert result["logicalOperator"] == "OR"
        assert len(result["children"]) == 1

        # The single group child
        group = result["children"][0]
        assert group["xsi_type"] == "CustomCriteriaSet"
        assert group["logicalOperator"] == "AND"
        assert len(group["children"]) == 1

        # The criterion
        criterion = group["children"][0]
        assert criterion["xsi_type"] == "CustomCriteria"
        assert criterion["keyId"] == 11111
        assert criterion["operator"] == "IS"

    def test_single_group_multiple_criteria_and_logic(self, targeting_manager):
        """Test one group with multiple criteria connected by AND."""
        groups_dict = {
            "groups": [
                {"criteria": [{"keyId": "11111", "values": ["sports"]}, {"keyId": "22222", "values": ["premium"]}]}
            ]
        }

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        assert result["logicalOperator"] == "OR"
        assert len(result["children"]) == 1

        group = result["children"][0]
        assert group["logicalOperator"] == "AND"
        assert len(group["children"]) == 2

        key_ids = [c["keyId"] for c in group["children"]]
        assert 11111 in key_ids
        assert 22222 in key_ids

    def test_multiple_groups_or_logic(self, targeting_manager):
        """Test multiple groups connected by OR logic."""
        groups_dict = {
            "groups": [
                {"criteria": [{"keyId": "11111", "values": ["sports"]}]},
                {"criteria": [{"keyId": "22222", "values": ["news"]}]},
            ]
        }

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        # Top level OR for groups
        assert result["logicalOperator"] == "OR"
        assert len(result["children"]) == 2

        # Each group is AND internally
        for group in result["children"]:
            assert group["xsi_type"] == "CustomCriteriaSet"
            assert group["logicalOperator"] == "AND"

    def test_groups_with_exclude_criteria(self, targeting_manager):
        """Test groups with excluded criteria."""
        groups_dict = {
            "groups": [
                {
                    "criteria": [
                        {"keyId": "11111", "values": ["sports"]},
                        {"keyId": "22222", "values": ["politics"], "exclude": True},
                    ]
                }
            ]
        }

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        group = result["children"][0]
        assert len(group["children"]) == 2

        include_criteria = [c for c in group["children"] if c["operator"] == "IS"]
        exclude_criteria = [c for c in group["children"] if c["operator"] == "IS_NOT"]

        assert len(include_criteria) == 1
        assert include_criteria[0]["keyId"] == 11111

        assert len(exclude_criteria) == 1
        assert exclude_criteria[0]["keyId"] == 22222

    def test_groups_multiple_values_or_within_criterion(self, targeting_manager):
        """Test that multiple values in a criterion are OR'd together."""
        groups_dict = {"groups": [{"criteria": [{"keyId": "11111", "values": ["sports", "entertainment", "news"]}]}]}

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        criterion = result["children"][0]["children"][0]
        assert criterion["keyId"] == 11111
        assert len(criterion["valueIds"]) == 3

    def test_groups_numeric_value_ids(self, targeting_manager):
        """Test groups format with numeric GAM value IDs."""
        groups_dict = {"groups": [{"criteria": [{"keyId": "11111", "values": ["451005167391", "451470637712"]}]}]}

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        criterion = result["children"][0]["children"][0]
        assert 451005167391 in criterion["valueIds"]
        assert 451470637712 in criterion["valueIds"]

    def test_groups_complex_gam_style(self, targeting_manager):
        """Test complex GAM-style targeting: (A AND B) OR (C AND D).

        Matches the GAM UI pattern:
        Group 1: DAY_OF_WEEK is MONDAY,TUESDAY AND CustomKey is srP
        Or
        Group 2: BOK Test is cat
        """
        groups_dict = {
            "groups": [
                {
                    "criteria": [
                        {"keyId": "11111", "values": ["MONDAY", "TUESDAY"]},
                        {"keyId": "22222", "values": ["srP"]},
                    ]
                },
                {"criteria": [{"keyId": "33333", "values": ["cat"]}]},
            ]
        }

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        # Top level: OR between groups
        assert result["logicalOperator"] == "OR"
        assert len(result["children"]) == 2

        # Group 1: key1 AND key2
        group1 = result["children"][0]
        assert group1["logicalOperator"] == "AND"
        assert len(group1["children"]) == 2

        # Group 2: key3 only
        group2 = result["children"][1]
        assert group2["logicalOperator"] == "AND"
        assert len(group2["children"]) == 1

    def test_groups_empty_groups_filtered(self, targeting_manager):
        """Test that empty groups are filtered out."""
        groups_dict = {
            "groups": [
                {"criteria": []},  # Empty group
                {"criteria": [{"keyId": "11111", "values": ["sports"]}]},
                {"criteria": []},  # Another empty group
            ]
        }

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        # Only one non-empty group should remain
        assert len(result["children"]) == 1

    def test_groups_empty_criteria_filtered(self, targeting_manager):
        """Test that criteria with empty values are filtered out."""
        groups_dict = {
            "groups": [
                {
                    "criteria": [
                        {"keyId": "11111", "values": []},  # Empty values
                        {"keyId": "22222", "values": ["sports"]},
                    ]
                }
            ]
        }

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        group = result["children"][0]
        assert len(group["children"]) == 1
        assert group["children"][0]["keyId"] == 22222

    def test_groups_all_empty_returns_empty(self, targeting_manager):
        """Test that all empty groups returns empty dict."""
        groups_dict = {"groups": [{"criteria": []}, {"criteria": [{"keyId": "11111", "values": []}]}]}

        result = targeting_manager._build_custom_targeting_structure(groups_dict)

        assert result == {}

    def test_groups_format_detected(self, targeting_manager):
        """Test that groups format is detected and routed correctly."""
        groups_dict = {"groups": [{"criteria": [{"keyId": "11111", "values": ["sports"]}]}]}

        # Should not raise, should detect groups format
        result = targeting_manager._build_custom_targeting_structure(groups_dict)
        assert "children" in result

    def test_backward_compat_enhanced_still_works(self, targeting_manager):
        """Test that enhanced format still works after groups support added."""
        enhanced_dict = {"include": {"11111": ["sports"]}, "exclude": {"22222": ["politics"]}, "operator": "AND"}

        result = targeting_manager._build_custom_targeting_structure(enhanced_dict)

        # Should still produce flat structure for enhanced format
        assert result["logicalOperator"] == "AND"
        assert len(result["children"]) == 2

    def test_backward_compat_legacy_still_works(self, targeting_manager):
        """Test that legacy format still works after groups support added."""
        legacy_dict = {"11111": "sports", "22222": "premium"}

        result = targeting_manager._build_custom_targeting_structure(legacy_dict)

        # Should still produce flat structure for legacy format
        assert result["logicalOperator"] == "AND"
        assert len(result["children"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
