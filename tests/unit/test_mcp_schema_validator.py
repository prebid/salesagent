"""Tests for MCP tool-schema parameter alignment validator.

These tests ensure the validator catches parameter mismatch bugs where:
1. Clients can pass parameters that tools don't accept
2. Tools are missing required schema fields
3. Tools are missing optional schema fields (causes "Unexpected keyword argument" errors)
"""

import ast
import sys
from pathlib import Path

import pytest

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from validate_mcp_schemas import ToolSchemaValidator


class TestValidatorDetectsOptionalFieldMismatches:
    """Test that validator catches missing optional fields (the adcp_version bug)."""

    def test_validator_catches_missing_optional_field(self, tmp_path):
        """Reproduce the adcp_version bug: tool missing optional schema field."""

        # Create a fake main.py with the BUG (missing adcp_version parameter)
        main_py = tmp_path / "main.py"
        main_py.write_text('''
from src.core.schemas import GetProductsRequest

@mcp.tool
async def get_products(
    promoted_offering: str,
    brief: str = "",
    min_exposures: int | None = None,
    filters: dict | None = None,
    strategy_id: str | None = None,
    context: Context = None,
) -> GetProductsResponse:
    """Get products - MISSING adcp_version parameter!"""
    req = GetProductsRequest(
        brief=brief,
        promoted_offering=promoted_offering,
        min_exposures=min_exposures,
        filters=filters,
        strategy_id=strategy_id,
    )
    return req
''')

        validator = ToolSchemaValidator()

        # Parse the buggy tool
        tools = validator.parse_main_py_for_tools(main_py)
        assert "get_products" in tools
        tool_params = tools["get_products"]

        # Tool params should NOT include adcp_version (reproducing the bug)
        assert "adcp_version" not in tool_params
        assert "promoted_offering" in tool_params

        # Find schemas used
        schemas_used = validator.find_schema_constructions(main_py, "get_products")
        assert "GetProductsRequest" in schemas_used

        # Validate - should ERROR because adcp_version is in schema but not tool
        from src.core.schemas import GetProductsRequest

        validator.validate_tool("get_products", tool_params, GetProductsRequest)

        # Should have caught the bug!
        assert len(validator.errors) > 0
        assert any("adcp_version" in err for err in validator.errors)
        assert any("optional field" in err.lower() for err in validator.errors)

    def test_validator_passes_with_all_fields(self, tmp_path):
        """Validator should pass when tool has all schema fields."""

        # Create a fixed main.py (includes adcp_version)
        main_py = tmp_path / "main.py"
        main_py.write_text('''
from src.core.schemas import GetProductsRequest

@mcp.tool
async def get_products(
    promoted_offering: str,
    brief: str = "",
    adcp_version: str = "1.0.0",
    min_exposures: int | None = None,
    filters: dict | None = None,
    strategy_id: str | None = None,
    context: Context = None,
) -> GetProductsResponse:
    """Get products - includes adcp_version!"""
    req = GetProductsRequest(
        brief=brief,
        promoted_offering=promoted_offering,
        adcp_version=adcp_version,
        min_exposures=min_exposures,
        filters=filters,
        strategy_id=strategy_id,
    )
    return req
''')

        validator = ToolSchemaValidator()

        # Parse the fixed tool
        tools = validator.parse_main_py_for_tools(main_py)
        tool_params = tools["get_products"]

        # Tool params should include adcp_version
        assert "adcp_version" in tool_params

        # Validate - should PASS
        from src.core.schemas import GetProductsRequest

        validator.validate_tool("get_products", tool_params, GetProductsRequest)

        # Should have NO errors
        assert len(validator.errors) == 0

    def test_validator_detects_shared_impl_pattern(self, tmp_path):
        """Validator should check both tool and _tool_impl functions."""

        # Create main.py with shared implementation pattern
        main_py = tmp_path / "main.py"
        main_py.write_text('''
from src.core.schemas import GetProductsRequest

async def _get_products_impl(req: GetProductsRequest, context: Context) -> GetProductsResponse:
    """Shared implementation with full business logic."""
    # Schema construction happens here, not in wrapper!
    return GetProductsResponse(products=[])

@mcp.tool
async def get_products(
    promoted_offering: str,
    brief: str = "",
    adcp_version: str = "1.0.0",
    context: Context = None,
) -> GetProductsResponse:
    """MCP wrapper - missing min_exposures, filters, strategy_id!"""
    req = GetProductsRequest(
        brief=brief,
        promoted_offering=promoted_offering,
        adcp_version=adcp_version,
    )
    return await _get_products_impl(req, context)
''')

        validator = ToolSchemaValidator()

        # Find schemas in both functions
        schemas_used = validator.find_schema_constructions(main_py, "get_products")

        # Should find GetProductsRequest in the _impl function
        assert "GetProductsRequest" in schemas_used


class TestValidatorExistingFunctionality:
    """Test that existing validator functionality still works."""

    def test_validator_catches_extra_parameters(self, tmp_path):
        """Validator should catch when tool has parameters not in schema."""

        main_py = tmp_path / "main.py"
        main_py.write_text('''
from src.core.schemas import GetProductsRequest

@mcp.tool
async def get_products(
    promoted_offering: str,
    brief: str = "",
    extra_param_not_in_schema: str = "",
    context: Context = None,
) -> GetProductsResponse:
    """Tool with extra parameter not in schema."""
    req = GetProductsRequest(
        brief=brief,
        promoted_offering=promoted_offering,
    )
    return req
''')

        validator = ToolSchemaValidator()
        tools = validator.parse_main_py_for_tools(main_py)
        tool_params = tools["get_products"]

        from src.core.schemas import GetProductsRequest

        validator.validate_tool("get_products", tool_params, GetProductsRequest)

        # Should error on extra parameter
        assert len(validator.errors) > 0
        assert any("extra_param_not_in_schema" in err for err in validator.errors)

    def test_validator_catches_missing_required_field(self, tmp_path):
        """Validator should catch when tool is missing required schema field."""

        main_py = tmp_path / "main.py"
        main_py.write_text('''
from src.core.schemas import GetProductsRequest

@mcp.tool
async def get_products(
    brief: str = "",
    context: Context = None,
) -> GetProductsResponse:
    """Tool missing REQUIRED promoted_offering field!"""
    req = GetProductsRequest(
        brief=brief,
    )
    return req
''')

        validator = ToolSchemaValidator()
        tools = validator.parse_main_py_for_tools(main_py)
        tool_params = tools["get_products"]

        from src.core.schemas import GetProductsRequest

        validator.validate_tool("get_products", tool_params, GetProductsRequest)

        # Should error on missing required field
        assert len(validator.errors) > 0
        assert any("promoted_offering" in err for err in validator.errors)
        assert any("required" in err.lower() for err in validator.errors)
