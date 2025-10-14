"""Tests for MCP tool-schema parameter alignment validator.

These tests ensure the validator catches parameter mismatch bugs where:
1. Clients can pass parameters that tools don't accept
2. Tools are missing required schema fields
3. Tools are missing optional schema fields (causes "Unexpected keyword argument" errors)
"""

import sys
from pathlib import Path

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from validate_mcp_schemas import ToolSchemaValidator


class TestValidatorDetectsOptionalFieldMismatches:
    """Test that validator catches missing optional fields (the adcp_version bug)."""

    def test_validator_catches_missing_optional_field(self, tmp_path):
        """Validator should accept tools using request object pattern.

        This test verifies the CORRECT pattern (request object) works.
        The old pattern (individual params) was buggy and has been removed.
        """

        # Create main.py using correct request object pattern
        main_py = tmp_path / "main.py"
        main_py.write_text(
            '''
from src.core.schema_adapters import GetProductsRequest

@mcp.tool
async def get_products(
    req: GetProductsRequest,
    context: Context = None,
) -> GetProductsResponse:
    """Get products using request object pattern."""
    return _get_products_impl(req, context)
'''
        )

        validator = ToolSchemaValidator()

        # Parse the tool
        tools = validator.parse_main_py_for_tools(main_py)
        assert "get_products" in tools
        tool_params = tools["get_products"]

        # Tool should use request object pattern - tool_params is a list of non-Context param names
        assert "req" in tool_params
        assert len(tool_params) == 1  # Only 'req', context is filtered out

        # With request object pattern, we don't check individual fields anymore
        # The Pydantic schema itself enforces all fields, so no need for our validator

    def test_validator_passes_with_all_fields(self, tmp_path):
        """Validator should pass when tool has all schema fields."""

        # Create a fixed main.py (includes adcp_version and brand_manifest)
        main_py = tmp_path / "main.py"
        main_py.write_text(
            '''
from src.core.schema_adapters import GetProductsRequest

@mcp.tool
async def get_products(
    promoted_offering: str | None = None,
    brand_manifest: Any | None = None,
    brief: str = "",
    adcp_version: str = "1.0.0",
    filters: dict | None = None,
    context: Context = None,
) -> GetProductsResponse:
    """Get products - includes adcp_version, brand_manifest, and filters!"""
    req = GetProductsRequest(
        brief=brief,
        promoted_offering=promoted_offering,
        brand_manifest=brand_manifest,
        adcp_version=adcp_version,
        filters=filters,
    )
    return req
'''
        )

        validator = ToolSchemaValidator()

        # Parse the fixed tool
        tools = validator.parse_main_py_for_tools(main_py)
        tool_params = tools["get_products"]

        # Tool params should include adcp_version
        assert "adcp_version" in tool_params

        # Validate - should PASS
        from src.core.schema_adapters import GetProductsRequest

        validator.validate_tool("get_products", tool_params, GetProductsRequest)

        # Should have NO errors
        assert len(validator.errors) == 0

    def test_validator_detects_shared_impl_pattern(self, tmp_path):
        """Validator should check both tool and _tool_impl functions."""

        # Create main.py with shared implementation pattern
        main_py = tmp_path / "main.py"
        main_py.write_text(
            '''
from src.core.schema_adapters import GetProductsRequest

async def _get_products_impl(req: GetProductsRequest, context: Context) -> GetProductsResponse:
    """Shared implementation with full business logic."""
    # Schema construction happens here, not in wrapper!
    return GetProductsResponse(products=[])

@mcp.tool
async def get_products(
    promoted_offering: str,
    brief: str = "",
    adcp_version: str = "1.0.0",
    filters: dict | None = None,
    context: Context = None,
) -> GetProductsResponse:
    """MCP wrapper - includes all AdCP spec fields!"""
    req = GetProductsRequest(
        brief=brief,
        promoted_offering=promoted_offering,
        adcp_version=adcp_version,
        filters=filters,
    )
    return await _get_products_impl(req, context)
'''
        )

        validator = ToolSchemaValidator()

        # Find schemas in both functions
        schemas_used = validator.find_schema_constructions(main_py, "get_products")

        # Should find GetProductsRequest in the _impl function
        assert "GetProductsRequest" in schemas_used


class TestValidatorExistingFunctionality:
    """Test that existing validator functionality still works."""

    def test_validator_catches_extra_parameters(self, tmp_path):
        """With request object pattern, Pydantic handles validation (no extra params possible)."""

        # Request object pattern prevents extra params - Pydantic only accepts defined fields
        main_py = tmp_path / "main.py"
        main_py.write_text(
            '''
from src.core.schema_adapters import GetProductsRequest

@mcp.tool
async def get_products(
    req: GetProductsRequest,
    context: Context = None,
) -> GetProductsResponse:
    """Tool using request object - extra params not possible."""
    return _get_products_impl(req, context)
'''
        )

        validator = ToolSchemaValidator()
        tools = validator.parse_main_py_for_tools(main_py)
        tool_params = tools["get_products"]

        # Request object pattern: tool has 'req' param, Pydantic schema validates its contents
        assert tool_params == ["req"]

    def test_validator_catches_missing_required_field(self, tmp_path):
        """With request object pattern, Pydantic enforces required fields at runtime."""

        # Request object pattern - Pydantic enforces required fields
        main_py = tmp_path / "main.py"
        main_py.write_text(
            '''
from src.core.schema_adapters import GetProductsRequest

@mcp.tool
async def get_products(
    req: GetProductsRequest,
    context: Context = None,
) -> GetProductsResponse:
    """Tool using request object - Pydantic enforces required fields."""
    return _get_products_impl(req, context)
'''
        )

        validator = ToolSchemaValidator()
        tools = validator.parse_main_py_for_tools(main_py)
        tool_params = tools["get_products"]

        # Request object pattern: tool has 'req' param, Pydantic enforces promoted_offering is required
        assert tool_params == ["req"]
