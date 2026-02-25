#!/usr/bin/env python3
"""
Test that MCP tool function signatures match their schema type definitions.

This test validates that:
1. MCP tool parameter types match the corresponding schema field types
2. Parameters that accept arrays in the schema also accept arrays in the tool signature
3. Union types are properly propagated from schema to tool signature

This would have caught the status_filter bug where:
- Schema defined: str | list[str] | None
- Tool signature had: str | None (missing list[str])
"""

import inspect
import typing
from typing import Any, get_args, get_origin

import pytest


def normalize_type(type_hint: Any) -> set[str]:
    """Normalize a type hint to a set of base type names for comparison.

    Returns simplified type names like {'str', 'list', 'None'} for union types.
    """
    if type_hint is None:
        return {"None"}

    origin = get_origin(type_hint)
    args = get_args(type_hint)

    # Handle Union types (including | syntax which becomes Union)
    if origin is typing.Union:
        result = set()
        for arg in args:
            result.update(normalize_type(arg))
        return result

    # Handle list types
    if origin is list:
        return {"list"}

    # Handle dict types
    if origin is dict:
        return {"dict"}

    # Handle None
    if type_hint is type(None):
        return {"None"}

    # Handle basic types
    if isinstance(type_hint, type):
        return {type_hint.__name__}

    # Handle string annotations
    if isinstance(type_hint, str):
        if "list" in type_hint.lower():
            return {"list", "str"}  # Simplified
        return {type_hint}

    return {str(type_hint)}


def get_function_param_types(func) -> dict[str, set[str]]:
    """Extract parameter types from a function signature."""
    sig = inspect.signature(func)
    hints = typing.get_type_hints(func) if hasattr(func, "__annotations__") else {}

    result = {}
    for param_name, param in sig.parameters.items():
        if param_name in ["self", "cls", "ctx", "context"]:
            continue

        type_hint = hints.get(param_name, param.annotation)
        if type_hint is inspect.Parameter.empty:
            result[param_name] = set()
        else:
            result[param_name] = normalize_type(type_hint)

    return result


def get_schema_field_types(schema_class) -> dict[str, set[str]]:
    """Extract field types from a Pydantic schema."""
    result = {}
    for field_name, field_info in schema_class.model_fields.items():
        annotation = field_info.annotation
        result[field_name] = normalize_type(annotation)
    return result


class TestMCPToolTypeAlignment:
    """Test that MCP tool signatures match schema definitions."""

    def test_get_media_buy_delivery_status_filter_type(self):
        """Test that get_media_buy_delivery accepts status_filter as array.

        Regression test for: status_filter defined as str | list[str] | None in schema
        but MCP tool only accepted str | None.
        """
        from src.core.schemas import GetMediaBuyDeliveryRequest
        from src.core.tools.media_buy_delivery import get_media_buy_delivery

        # Get types from schema
        schema_types = get_schema_field_types(GetMediaBuyDeliveryRequest)
        schema_status_filter = schema_types.get("status_filter", set())

        # Get types from function
        func_types = get_function_param_types(get_media_buy_delivery)
        func_status_filter = func_types.get("status_filter", set())

        # Schema allows list - function must also allow list
        if "list" in schema_status_filter:
            assert "list" in func_status_filter, (
                f"get_media_buy_delivery.status_filter should accept list type.\n"
                f"Schema type: {schema_status_filter}\n"
                f"Function type: {func_status_filter}"
            )

    def test_all_mcp_tools_array_parameters_match_schema(self):
        """Test all MCP tools accept arrays when their schemas define array types.

        This is a generalized test that catches any tool where the schema
        allows an array but the function signature doesn't.
        """
        # Import tools and their corresponding schemas
        from src.core.schemas import (
            CreateMediaBuyRequest,
            GetMediaBuyDeliveryRequest,
            GetProductsRequest,
            ListCreativesRequest,
            UpdateMediaBuyRequest,
        )
        from src.core.tools.creatives import list_creatives
        from src.core.tools.media_buy_create import create_media_buy
        from src.core.tools.media_buy_delivery import get_media_buy_delivery
        from src.core.tools.media_buy_update import update_media_buy
        from src.core.tools.products import get_products

        # Note: get_signals removed — signals is dead code (UC-008)
        tool_schema_pairs = [
            (get_media_buy_delivery, GetMediaBuyDeliveryRequest, "get_media_buy_delivery"),
            (create_media_buy, CreateMediaBuyRequest, "create_media_buy"),
            (update_media_buy, UpdateMediaBuyRequest, "update_media_buy"),
            (get_products, GetProductsRequest, "get_products"),
            (list_creatives, ListCreativesRequest, "list_creatives"),
        ]

        issues = []

        for func, schema_class, name in tool_schema_pairs:
            schema_types = get_schema_field_types(schema_class)
            func_types = get_function_param_types(func)

            for param_name in func_types:
                if param_name not in schema_types:
                    # Parameter not in schema (like webhook_url) - skip
                    continue

                schema_type = schema_types[param_name]
                func_type = func_types[param_name]

                # If schema allows list, function must also allow list
                if "list" in schema_type and "list" not in func_type:
                    issues.append(
                        f"{name}.{param_name}: Schema allows list but function doesn't.\n"
                        f"  Schema: {schema_type}\n"
                        f"  Function: {func_type}"
                    )

                # If schema allows dict, function must also allow dict
                if "dict" in schema_type and "dict" not in func_type:
                    issues.append(
                        f"{name}.{param_name}: Schema allows dict but function doesn't.\n"
                        f"  Schema: {schema_type}\n"
                        f"  Function: {func_type}"
                    )

        assert not issues, "Found MCP tool type mismatches with schemas:\n\n" + "\n\n".join(issues)

    def test_raw_functions_match_mcp_tools(self):
        """Test that _raw functions have the same parameter types as MCP tools.

        The _raw functions (for A2A) should accept the same types as MCP tools.
        """
        from src.core.tools.media_buy_delivery import (
            get_media_buy_delivery,
            get_media_buy_delivery_raw,
        )

        mcp_types = get_function_param_types(get_media_buy_delivery)
        raw_types = get_function_param_types(get_media_buy_delivery_raw)

        # Compare common parameters
        for param in set(mcp_types.keys()) & set(raw_types.keys()):
            # Skip special params
            if param in ["webhook_url", "push_notification_config"]:
                continue

            assert mcp_types[param] == raw_types[param], (
                f"get_media_buy_delivery.{param} type mismatch between MCP and raw:\n"
                f"  MCP: {mcp_types[param]}\n"
                f"  Raw: {raw_types[param]}"
            )


class TestParameterTypeDocumentation:
    """Document parameter types for reference and debugging."""

    def test_document_delivery_parameter_types(self):
        """Document get_media_buy_delivery parameter types for reference."""
        from src.core.schemas import GetMediaBuyDeliveryRequest
        from src.core.tools.media_buy_delivery import (
            get_media_buy_delivery,
            get_media_buy_delivery_raw,
        )

        print("\n" + "=" * 80)
        print("GET_MEDIA_BUY_DELIVERY PARAMETER TYPES")
        print("=" * 80)

        print("\nSchema (GetMediaBuyDeliveryRequest):")
        for name, field in GetMediaBuyDeliveryRequest.model_fields.items():
            print(f"  {name}: {field.annotation}")

        print("\nMCP Tool (get_media_buy_delivery):")
        hints = typing.get_type_hints(get_media_buy_delivery)
        for name, hint in hints.items():
            if name not in ["return"]:
                print(f"  {name}: {hint}")

        print("\nRaw Function (get_media_buy_delivery_raw):")
        hints = typing.get_type_hints(get_media_buy_delivery_raw)
        for name, hint in hints.items():
            if name not in ["return"]:
                print(f"  {name}: {hint}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
