"""Unit tests for GAM custom targeting translation."""

import os
import sys

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from src.admin.utils import get_custom_targeting_mappings, translate_custom_targeting


@pytest.mark.unit
class TestCustomTargetingTranslation:
    """Test suite for custom targeting translation functions."""

    def test_get_custom_targeting_mappings(self):
        """Test that default mappings are returned."""
        key_mappings, value_mappings = get_custom_targeting_mappings()

        # Check that we get dictionaries back
        assert isinstance(key_mappings, dict)
        assert isinstance(value_mappings, dict)

        # Check some expected keys exist
        assert "13748922" in key_mappings
        assert key_mappings["13748922"] == "hb_pb"

        assert "448589710493" in value_mappings
        assert value_mappings["448589710493"] == "0.01"

    def test_translate_custom_targeting_none(self):
        """Test handling of None input."""
        result = translate_custom_targeting(None)
        assert result is None

    def test_translate_custom_targeting_empty(self):
        """Test handling of empty input."""
        result = translate_custom_targeting({})
        assert result is None

    def test_translate_custom_targeting_simple_is(self):
        """Test simple IS operator translation."""
        input_node = {"keyId": 13748922, "operator": "IS", "valueIds": [448589710493]}

        result = translate_custom_targeting(input_node)

        assert result == {"key": "hb_pb", "in": ["0.01"]}

    def test_translate_custom_targeting_simple_is_not(self):
        """Test simple IS_NOT operator translation."""
        input_node = {"keyId": 14094596, "operator": "IS_NOT", "valueIds": [448946353802]}

        result = translate_custom_targeting(input_node)

        assert result == {"key": "hb_format", "not_in": ["video"]}

    def test_translate_custom_targeting_multiple_values(self):
        """Test translation with multiple values."""
        input_node = {"keyId": 14095946, "operator": "IS", "valueIds": [448946107548, 448946356517]}

        result = translate_custom_targeting(input_node)

        assert result == {"key": "hb_source", "in": ["freestar", "prebid"]}

    def test_translate_custom_targeting_and_operator(self):
        """Test AND logical operator translation."""
        input_node = {
            "logicalOperator": "AND",
            "children": [
                {"keyId": 13748922, "operator": "IS", "valueIds": [448589710493]},
                {"keyId": 14095946, "operator": "IS", "valueIds": [448946107548]},
            ],
        }

        result = translate_custom_targeting(input_node)

        expected = {"and": [{"key": "hb_pb", "in": ["0.01"]}, {"key": "hb_source", "in": ["freestar"]}]}
        assert result == expected

    def test_translate_custom_targeting_or_operator(self):
        """Test OR logical operator translation."""
        input_node = {
            "logicalOperator": "OR",
            "children": [
                {"keyId": 13748922, "operator": "IS", "valueIds": [448589710493]},
                {"keyId": 14095946, "operator": "IS", "valueIds": [448946356517]},
            ],
        }

        result = translate_custom_targeting(input_node)

        expected = {"or": [{"key": "hb_pb", "in": ["0.01"]}, {"key": "hb_source", "in": ["prebid"]}]}
        assert result == expected

    def test_translate_custom_targeting_nested_operators(self):
        """Test nested AND/OR operators translation."""
        input_node = {
            "logicalOperator": "OR",
            "children": [
                {
                    "logicalOperator": "AND",
                    "children": [
                        {"keyId": 13748922, "operator": "IS", "valueIds": [448589710493]},
                        {"keyId": 14095946, "operator": "IS", "valueIds": [448946107548, 448946356517]},
                        {"keyId": 14094596, "operator": "IS_NOT", "valueIds": [448946353802]},
                    ],
                }
            ],
        }

        result = translate_custom_targeting(input_node)

        # When OR has single child, it should return the child directly
        expected = {
            "and": [
                {"key": "hb_pb", "in": ["0.01"]},
                {"key": "hb_source", "in": ["freestar", "prebid"]},
                {"key": "hb_format", "not_in": ["video"]},
            ]
        }
        assert result == expected

    def test_translate_custom_targeting_unknown_ids(self):
        """Test handling of unknown key/value IDs."""
        input_node = {"keyId": 99999999, "operator": "IS", "valueIds": [88888888]}

        result = translate_custom_targeting(input_node)

        # Should use fallback format for unknown IDs
        assert result == {"key": "key_99999999", "in": ["88888888"]}

    def test_translate_custom_targeting_empty_children(self):
        """Test handling of logical operator with empty children."""
        input_node = {"logicalOperator": "AND", "children": []}

        result = translate_custom_targeting(input_node)

        assert result is None

    def test_translate_custom_targeting_single_child_unwrap(self):
        """Test that single child in logical operator is unwrapped."""
        input_node = {
            "logicalOperator": "AND",
            "children": [{"keyId": 13748922, "operator": "IS", "valueIds": [448589710493]}],
        }

        result = translate_custom_targeting(input_node)

        # Single child should be returned directly, not wrapped in AND
        assert result == {"key": "hb_pb", "in": ["0.01"]}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
