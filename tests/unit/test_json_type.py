"""Tests for custom SQLAlchemy JSONType for PostgreSQL JSONB.

Per CLAUDE.md: This codebase is PostgreSQL-only (no SQLite support).
JSONType uses native JSONB storage for optimal performance.
"""

import pytest

from src.core.database.json_type import JSONType


class TestJSONType:
    """Test JSONType handles PostgreSQL native JSONB correctly."""

    def test_process_result_value_with_none(self):
        """Test that None values are returned as None."""
        json_type = JSONType()
        result = json_type.process_result_value(None, None)
        assert result is None

    def test_process_result_value_with_dict(self):
        """Test that dict values (PostgreSQL native JSONB) are returned as-is."""
        json_type = JSONType()
        test_dict = {"key": "value", "nested": {"data": 123}}
        result = json_type.process_result_value(test_dict, None)
        assert result == test_dict
        assert isinstance(result, dict)

    def test_process_result_value_with_list(self):
        """Test that list values (PostgreSQL native JSONB) are returned as-is."""
        json_type = JSONType()
        test_list = ["item1", "item2", "item3"]
        result = json_type.process_result_value(test_list, None)
        assert result == test_list
        assert isinstance(result, list)

    def test_process_result_value_with_nested_json(self):
        """Test deeply nested JSON structures."""
        json_type = JSONType()
        nested_dict = {
            "level1": {
                "level2": {"level3": {"level4": {"value": "deep"}}},
                "array": [1, 2, 3],
            }
        }

        result = json_type.process_result_value(nested_dict, None)
        assert result == nested_dict
        assert result["level1"]["level2"]["level3"]["level4"]["value"] == "deep"

    def test_process_result_value_with_special_characters(self):
        """Test JSON with special characters and unicode."""
        json_type = JSONType()
        test_dict = {
            "emoji": "🎉",
            "unicode": "Hëllö Wörld",
            "newlines": "line1\nline2",
            "quotes": 'He said "hello"',
        }

        result = json_type.process_result_value(test_dict, None)
        assert result == test_dict

    def test_process_result_value_with_null_values_in_array(self):
        """Test that null values in arrays are preserved."""
        json_type = JSONType()
        test_array = ["item1", None, "item2", None, "item3"]
        result = json_type.process_result_value(test_array, None)
        assert result == test_array

    def test_process_result_value_with_mixed_types_in_array(self):
        """Test arrays with mixed types."""
        json_type = JSONType()
        test_array = ["string", 123, True, None, {"nested": "object"}]
        result = json_type.process_result_value(test_array, None)
        assert result == test_array

    def test_process_result_value_with_boolean_values(self):
        """Test JSON with boolean values."""
        json_type = JSONType()
        test_dict = {"enabled": True, "disabled": False}
        result = json_type.process_result_value(test_dict, None)
        assert result == {"enabled": True, "disabled": False}

    def test_process_result_value_with_numeric_values(self):
        """Test JSON with various numeric types."""
        json_type = JSONType()
        test_dict = {"int": 42, "float": 3.14, "negative": -10, "zero": 0}
        result = json_type.process_result_value(test_dict, None)
        assert result == {"int": 42, "float": 3.14, "negative": -10, "zero": 0}

    def test_process_result_value_with_json_string_raises_error(self):
        """Test that JSON strings raise TypeError (PostgreSQL JSONB never returns strings)."""
        json_type = JSONType()
        json_string = '{"key": "value", "count": 42}'
        with pytest.raises(TypeError, match="Unexpected type in JSONB column: str"):
            json_type.process_result_value(json_string, None)

    def test_process_result_value_with_unexpected_type_int(self):
        """Test that int raises TypeError."""
        json_type = JSONType()
        with pytest.raises(TypeError, match="Unexpected type in JSONB column"):
            json_type.process_result_value(42, None)

    def test_process_result_value_with_unexpected_type_bool(self):
        """Test that bool raises TypeError."""
        json_type = JSONType()
        with pytest.raises(TypeError, match="Unexpected type in JSONB column"):
            json_type.process_result_value(True, None)

    def test_cache_ok_is_true(self):
        """Test that cache_ok flag is set for query caching."""
        json_type = JSONType()
        assert json_type.cache_ok is True

    def test_impl_is_jsonb(self):
        """Test that the implementation type is PostgreSQL JSONB with none_as_null."""
        from sqlalchemy.dialects.postgresql import JSONB

        # Check that impl is a JSONB instance (not just the class)
        assert isinstance(JSONType.impl, JSONB)
        # Verify none_as_null is enabled to ensure Python None → SQL NULL
        assert JSONType.impl.none_as_null is True


class TestJSONTypeBindParam:
    """Test process_bind_param input validation."""

    def test_process_bind_param_with_none(self):
        """Test that None values are passed through."""
        json_type = JSONType()
        result = json_type.process_bind_param(None, None)
        assert result is None

    def test_process_bind_param_with_dict(self):
        """Test that dict values are passed through (PostgreSQL handles serialization)."""
        json_type = JSONType()
        test_dict = {"key": "value"}
        result = json_type.process_bind_param(test_dict, None)
        assert result == test_dict  # Returned as-is for PostgreSQL JSONB
        assert isinstance(result, dict)

    def test_process_bind_param_with_list(self):
        """Test that list values are passed through (PostgreSQL handles serialization)."""
        json_type = JSONType()
        test_list = ["item1", "item2"]
        result = json_type.process_bind_param(test_list, None)
        assert result == test_list  # Returned as-is for PostgreSQL JSONB
        assert isinstance(result, list)

    def test_process_bind_param_with_invalid_type_string(self):
        """A str (typically json.dumps pre-serialization) must raise, not silently empty to {}.

        The old {}-coercion silently destroyed audit details, principal
        platform_mappings, and signup tenant fields (salesagent-akjc).
        """
        json_type = JSONType()
        with pytest.raises(TypeError, match="never pre-serialize with json.dumps"):
            json_type.process_bind_param('{"a": 1}', None)

    def test_process_bind_param_with_invalid_type_int(self):
        """An int is a caller bug — loud TypeError, not silent {}."""
        json_type = JSONType()
        with pytest.raises(TypeError, match="JSONType column received int"):
            json_type.process_bind_param(42, None)

    def test_process_bind_param_with_invalid_type_bool(self):
        """A bool is a caller bug — loud TypeError, not silent {}."""
        json_type = JSONType()
        with pytest.raises(TypeError, match="JSONType column received bool"):
            json_type.process_bind_param(True, None)


class TestJSONTypePostgreSQLOptimization:
    """Test PostgreSQL fast-path optimization."""

    def test_postgresql_dict_fast_path(self):
        """Test that PostgreSQL dicts are returned immediately."""
        from unittest.mock import Mock

        json_type = JSONType()
        dialect = Mock()
        dialect.name = "postgresql"

        test_dict = {"key": "value"}
        result = json_type.process_result_value(test_dict, dialect)

        # Should return immediately without processing
        assert result == test_dict
        assert result is test_dict  # Same object reference

    def test_postgresql_list_fast_path(self):
        """Test that PostgreSQL lists are returned immediately."""
        from unittest.mock import Mock

        json_type = JSONType()
        dialect = Mock()
        dialect.name = "postgresql"

        test_list = ["item1", "item2"]
        result = json_type.process_result_value(test_list, dialect)

        # Should return immediately without processing
        assert result == test_list
        assert result is test_list  # Same object reference


class TestJSONTypeRealWorldScenarios:
    """Test real-world scenarios from the codebase."""

    def test_authorized_domains_scenario(self):
        """Test authorized_domains list from PostgreSQL JSONB."""
        json_type = JSONType()

        # PostgreSQL native list
        postgres_value = ["example.com", "test.com", "company.com"]
        result = json_type.process_result_value(postgres_value, None)
        assert result == ["example.com", "test.com", "company.com"]
        assert isinstance(result, list)

    def test_platform_mappings_scenario(self):
        """Test platform_mappings dict scenario."""
        json_type = JSONType()

        # Complex nested structure from PostgreSQL JSONB
        mappings_dict = {
            "google_ad_manager": {
                "enabled": True,
                "advertiser_id": "123456",
                "trafficker_id": "789012",
            },
            "mock": {"enabled": False},
        }

        result = json_type.process_result_value(mappings_dict, None)
        assert result == mappings_dict

    def test_empty_list_default_scenario(self):
        """Test empty list default values."""
        json_type = JSONType()

        # Empty list from PostgreSQL JSONB
        result = json_type.process_result_value([], None)
        assert result == []

    def test_empty_dict_scenario(self):
        """Test empty dict default values."""
        json_type = JSONType()

        # Empty dict from PostgreSQL JSONB
        result = json_type.process_result_value({}, None)
        assert result == {}
