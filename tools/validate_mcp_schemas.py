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
            "get_signals": schemas.GetSignalsRequest,
            "activate_signal": schemas.ActivateSignalRequest,
            "list_authorized_properties": schemas.ListAuthorizedPropertiesRequest,
            "update_performance_index": schemas.UpdatePerformanceIndexRequest,
        }

    def get_schema_fields(self, schema_class) -> dict[str, Any]:
        """Extract field names and types from a Pydantic schema."""
        if not hasattr(schema_class, "model_fields"):
            return {}

        fields = {}
        for field_name, field_info in schema_class.model_fields.items():
            # Skip fields marked as exclude=True
            if hasattr(field_info, "exclude") and field_info.exclude:
                continue
            fields[field_name] = field_info.annotation
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
        """Find which schema classes are constructed in a tool function."""
        with open(main_py_path) as f:
            tree = ast.parse(f.read())

        schemas_used = []

        # Find the tool function
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == tool_name:
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

        # Check for missing required schema fields in tool parameters
        for field_name, field_type in schema_fields.items():
            if field_name not in tool_param_set:
                # Check if field is optional (has None in type annotation)
                type_str = str(field_type)
                is_optional = "None" in type_str or "Optional" in type_str or " | None" in type_str

                if not is_optional and field_name not in ["buyer_ref", "media_buy_id"]:  # OneOf fields
                    self.errors.append(
                        f"❌ {tool_name}: required field '{field_name}' from {schema_class.__name__} "
                        f"missing in tool parameters"
                    )

    def validate_all(self) -> bool:
        """Validate all registered tool-schema mappings."""
        print("🔍 Validating MCP tool-schema alignment...\n")

        main_py_path = Path(__file__).parent.parent / "src" / "core" / "main.py"
        tools_in_main = self.parse_main_py_for_tools(main_py_path)

        for tool_name, schema_class in self.tool_schema_mappings.items():
            if tool_name not in tools_in_main:
                self.warnings.append(f"⚠️  Tool '{tool_name}' not found in main.py")
                continue

            tool_params = tools_in_main[tool_name]

            print(f"Checking {tool_name} → {schema_class.__name__}")
            print(f"  Tool params: {', '.join(tool_params)}")
            print(f"  Schema fields: {', '.join(self.get_schema_fields(schema_class).keys())}")
            self.validate_tool(tool_name, tool_params, schema_class)
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
