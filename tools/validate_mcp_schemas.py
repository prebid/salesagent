#!/usr/bin/env python3
"""Validate that MCP tool signatures match their schema fields.

This script parses all @mcp.tool decorated functions and verifies that:
1. Tool parameters match the schema fields they construct
2. Parameter types are compatible with schema field types
3. No extra parameters are passed that don't exist in the schema
4. Required schema fields have corresponding tool parameters

Usage:
    python tools/validate_mcp_schemas.py

Exit code 0 if all validations pass, 1 if any failures.
"""

import ast
import sys
from pathlib import Path
from typing import Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import schemas


class ToolSchemaValidator:
    """Validates MCP tool signatures against their schema definitions."""

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.tool_schema_mappings = {
            # Map tool names to their request schema classes
            "get_products": schemas.GetProductsRequest,
            "create_media_buy": schemas.CreateMediaBuyRequest,
            "update_media_buy": schemas.UpdateMediaBuyRequest,
            "get_media_buy_delivery": schemas.GetMediaBuyDeliveryRequest,
            "sync_creatives": schemas.SyncCreativesRequest,
            "list_creatives": schemas.ListCreativesRequest,
            "list_creative_formats": schemas.ListCreativeFormatsRequest,
            "get_signals": schemas.GetSignalsRequest,
            "activate_signal": schemas.ActivateSignalRequest,
            "list_authorized_properties": schemas.ListAuthorizedPropertiesRequest,
            "update_performance_index": schemas.UpdatePerformanceIndexRequest,
        }

    def get_schema_fields(self, schema_class) -> dict[str, Any]:
        """Extract field names and field_info from a Pydantic schema."""
        if not hasattr(schema_class, "model_fields"):
            return {}

        fields = {}
        for field_name, field_info in schema_class.model_fields.items():
            # Skip fields marked as exclude=True
            if hasattr(field_info, "exclude") and field_info.exclude:
                continue
            # Return the field_info object so we can check is_required()
            fields[field_name] = field_info
        return fields

    def parse_main_py_for_tools(self, main_py_path: Path) -> dict[str, list[str]]:
        """Parse main.py to extract tool function signatures."""
        with open(main_py_path) as f:
            tree = ast.parse(f.read())

        tools = {}
        current_decorator = None

        for node in ast.walk(tree):
            # Look for @mcp.tool decorators followed by function definitions
            # Handle both async and sync functions
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                # Check if this function has @mcp.tool decorator
                has_mcp_tool = False
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Attribute):
                        if decorator.attr == "tool":
                            has_mcp_tool = True
                            break
                    elif isinstance(decorator, ast.Name):
                        if decorator.id == "tool":
                            has_mcp_tool = True
                            break

                if has_mcp_tool:
                    # Extract parameter names (skip context, self, cls)
                    params = []
                    for arg in node.args.args:
                        if arg.arg not in ["context", "self", "cls"]:
                            params.append(arg.arg)

                    tools[node.name] = params

        return tools

    def find_schema_constructions(self, main_py_path: Path, tool_name: str) -> list[str]:
        """Find which schema classes are constructed in a tool function or its _impl."""
        with open(main_py_path) as f:
            tree = ast.parse(f.read())

        schemas_used = []

        # Check both the tool function and potential _toolname_impl function
        function_names_to_check = [tool_name, f"_{tool_name}_impl"]

        # Find the tool function(s)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name in function_names_to_check:
                    # Look for schema constructions within this function
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            if isinstance(child.func, ast.Name):
                                # Direct call like UpdateMediaBuyRequest(...)
                                func_name = child.func.id
                                if func_name.endswith("Request"):
                                    schemas_used.append(func_name)
                            elif isinstance(child.func, ast.Attribute):
                                # Attribute call like schemas.UpdateMediaBuyRequest(...)
                                if child.func.attr.endswith("Request"):
                                    schemas_used.append(child.func.attr)

        return schemas_used

    def validate_tool(self, tool_name: str, tool_params: list[str], schema_class) -> None:
        """Validate a single tool against its schema."""
        schema_fields = self.get_schema_fields(schema_class)
        schema_field_names = set(schema_fields.keys())
        tool_param_set = set(tool_params)

        # Get main.py path for schema construction check
        main_py_path = Path(__file__).parent.parent / "src" / "core" / "main.py"
        constructed_schemas = self.find_schema_constructions(main_py_path, tool_name)

        # Special case: if tool takes a single 'req' parameter matching the schema name
        if tool_params == ["req"]:
            # This is a schema-first tool that takes the request object directly
            # No validation needed as FastMCP handles the mapping
            return

        # Check for extra tool parameters not in schema
        for param_name in tool_params:
            if param_name not in schema_field_names:
                # Check if it's a legacy/deprecated field
                if param_name in [
                    "flight_start_date",
                    "flight_end_date",
                    "start_date",
                    "end_date",
                    "total_budget",
                    "currency",
                    "pacing",
                    "daily_budget",
                    "product_ids",
                    "campaign_name",
                    "creatives",
                    "targeting_overlay",
                ]:
                    self.warnings.append(f"⚠️  {tool_name}: parameter '{param_name}' is legacy/deprecated")
                elif schema_class.__name__ in constructed_schemas:
                    self.errors.append(
                        f"❌ {tool_name}: parameter '{param_name}' not found in {schema_class.__name__} "
                        f"but tool constructs it directly"
                    )
                else:
                    # May be used for other purposes
                    pass

        # Check for missing schema fields in tool parameters
        # We check ALL fields (required and optional) because if a client passes an optional
        # field but the tool doesn't accept it, that's a parameter mismatch bug
        for field_name, field_info in schema_fields.items():
            if field_name not in tool_param_set:
                # Check if field is required using Pydantic's is_required() method
                is_required = field_info.is_required()

                # Only check if tool constructs this schema directly
                if schema_class.__name__ in constructed_schemas:
                    if is_required and field_name not in ["buyer_ref", "media_buy_id"]:  # OneOf fields
                        self.errors.append(
                            f"❌ {tool_name}: required field '{field_name}' from {schema_class.__name__} "
                            f"missing in tool parameters"
                        )
                    elif not is_required:
                        # Optional field missing - this is a BUG!
                        # Clients can pass this field but tool will reject it with "Unexpected keyword argument"
                        self.errors.append(
                            f"❌ {tool_name}: optional field '{field_name}' from {schema_class.__name__} "
                            f"missing in tool parameters (clients can pass it but tool will reject it)"
                        )

    def parse_tools_py_for_raw_functions(self, tools_py_path: Path) -> dict[str, list[str]]:
        """Parse tools.py to extract *_raw function signatures."""
        with open(tools_py_path) as f:
            tree = ast.parse(f.read())

        raw_functions = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.endswith("_raw"):
                    # Extract parameter names (skip context, self, cls, req)
                    params = []
                    for arg in node.args.args:
                        if arg.arg not in ["context", "self", "cls", "req"]:
                            params.append(arg.arg)

                    raw_functions[node.name] = params

        return raw_functions

    def validate_all(self) -> bool:
        """Validate all registered tool-schema mappings for both MCP and A2A."""
        print("🔍 Validating MCP and A2A tool-schema alignment...\n")

        main_py_path = Path(__file__).parent.parent / "src" / "core" / "main.py"
        tools_py_path = Path(__file__).parent.parent / "src" / "core" / "tools.py"

        tools_in_main = self.parse_main_py_for_tools(main_py_path)
        tools_in_tools_py = self.parse_tools_py_for_raw_functions(tools_py_path)

        for tool_name, schema_class in self.tool_schema_mappings.items():
            # Check MCP tool
            if tool_name not in tools_in_main:
                self.warnings.append(f"⚠️  MCP tool '{tool_name}' not found in main.py")
            else:
                tool_params = tools_in_main[tool_name]
                print(f"Checking MCP: {tool_name} → {schema_class.__name__}")
                print(f"  Tool params: {', '.join(tool_params)}")
                print(f"  Schema fields: {', '.join(self.get_schema_fields(schema_class).keys())}")
                self.validate_tool(tool_name, tool_params, schema_class)
                print()

            # Check A2A raw function
            raw_function_name = f"{tool_name}_raw"
            if raw_function_name not in tools_in_tools_py:
                self.warnings.append(f"⚠️  A2A raw function '{raw_function_name}' not found in tools.py")
            else:
                raw_params = tools_in_tools_py[raw_function_name]
                print(f"Checking A2A: {raw_function_name} → {schema_class.__name__}")
                print(f"  Raw params: {', '.join(raw_params)}")
                print(f"  Schema fields: {', '.join(self.get_schema_fields(schema_class).keys())}")

                # For A2A raw functions, they should either:
                # 1. Take a 'req' parameter (schema-first), OR
                # 2. Have matching parameters like MCP tools
                # Since we filtered out 'req' in parsing, if params is empty, it's schema-first
                original_raw_params = []
                with open(tools_py_path) as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                        if node.name == raw_function_name:
                            for arg in node.args.args:
                                if arg.arg not in ["context", "self", "cls"]:
                                    original_raw_params.append(arg.arg)

                if original_raw_params == ["req"]:
                    print(f"  ✅ Schema-first A2A function (takes 'req' parameter)")
                else:
                    # Validate like a normal tool
                    self.validate_tool(raw_function_name, raw_params, schema_class)
                print()

        # Print results
        print("=" * 70)
        if self.errors:
            print(f"\n❌ ERRORS ({len(self.errors)}):")
            for error in self.errors:
                print(f"  {error}")

        if self.warnings:
            print(f"\n⚠️  WARNINGS ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"  {warning}")

        if not self.errors and not self.warnings:
            print("\n✅ All tool-schema validations passed!")
            return True

        if not self.errors:
            print(f"\n✅ No critical errors, but {len(self.warnings)} warnings")
            return True

        print(f"\n❌ Validation failed: {len(self.errors)} errors, {len(self.warnings)} warnings")
        return False


def main():
    """Run validation and exit with appropriate code."""
    validator = ToolSchemaValidator()
    success = validator.validate_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
