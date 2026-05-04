"""Structural guard: MCP wrapper parameters must use SDK types, not Any/dict.

Ensures that MCP tool wrapper functions use proper Pydantic SDK types for their
parameters instead of Any or bare dict. This prevents schema drift and ensures
FastMCP generates accurate tool schemas for buyer agents.
"""

import inspect
import types
import typing
from typing import Any

import pytest

# Parameters that are allowed to use Any because they have no SDK type
# or are transport infrastructure (ctx, etc.)
ALLOWED_ANY_PARAMS = {
    "ctx",  # FastMCP Context — transport infra
}

# MCP wrapper functions to check (module_path, function_name)
MCP_WRAPPERS = [
    ("src.core.tools.products", "get_products"),
    ("src.core.tools.media_buy_create", "create_media_buy"),
    ("src.core.tools.media_buy_update", "update_media_buy"),
    ("src.core.tools.media_buy_delivery", "get_media_buy_delivery"),
    ("src.core.tools.creatives.sync_wrappers", "sync_creatives"),
    ("src.core.tools.creatives.listing", "list_creatives"),
    ("src.core.tools.properties", "list_authorized_properties"),
    ("src.core.tools.accounts", "list_accounts"),
    ("src.core.tools.accounts", "sync_accounts"),
    ("src.core.tools.capabilities", "get_adcp_capabilities"),
    ("src.core.tools.creative_formats", "list_creative_formats"),
]

# Parameters that MUST use specific SDK types (param_name -> expected type name)
# These are params where dict[str, Any] or Any is wrong — a proper SDK type exists.
REQUIRED_SDK_TYPES = {
    "reporting_dimensions": "ReportingDimensions",
    "attribution_window": "AttributionWindow",
    "account": "AccountReference",
    "property_list": "PropertyListReference",
}


def _get_union_args(annotation: Any) -> tuple[Any, ...]:
    """Extract args from both typing.Union and Python 3.10+ X | Y unions."""
    if isinstance(annotation, types.UnionType):
        return annotation.__args__
    origin = getattr(annotation, "__origin__", None)
    if origin is typing.Union:
        return typing.get_args(annotation)
    return ()


def _is_any_type(annotation: Any) -> bool:
    """Check if annotation is Any or contains Any in a Union.

    Catches: Any, Any | None, typing.Any | None
    """
    if annotation is Any:
        return True
    args = _get_union_args(annotation)
    return any(a is Any for a in args)


def _contains_dict(annotation: Any) -> bool:
    """Check if annotation contains dict (bare or parameterized).

    Catches: dict, dict | None, dict[str, Any] | None
    """
    if annotation is dict:
        return True
    origin = getattr(annotation, "__origin__", None)
    if origin is dict:
        return True
    for a in _get_union_args(annotation):
        if _contains_dict(a):
            return True
    return False


class TestMcpWrapperTypedParams:
    """MCP wrappers must not use Any or dict for domain parameters."""

    @pytest.mark.parametrize("module_path,func_name", MCP_WRAPPERS)
    def test_no_any_params(self, module_path: str, func_name: str):
        """No MCP wrapper parameter should use Any type."""
        import importlib

        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        sig = inspect.signature(func)

        violations = []
        for name, param in sig.parameters.items():
            if name in ALLOWED_ANY_PARAMS:
                continue
            if _is_any_type(param.annotation):
                violations.append(f"  {name}: {param.annotation!r}")

        assert not violations, (
            f"{module_path}.{func_name} has parameters with Any type:\n"
            + "\n".join(violations)
            + "\nUse proper SDK types instead of Any."
        )

    @pytest.mark.parametrize("module_path,func_name", MCP_WRAPPERS)
    def test_sdk_typed_params(self, module_path: str, func_name: str):
        """Parameters with known SDK types must use them, not dict."""
        import importlib

        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        sig = inspect.signature(func)

        violations = []
        for name, param in sig.parameters.items():
            if name not in REQUIRED_SDK_TYPES:
                continue
            expected_type = REQUIRED_SDK_TYPES[name]
            if _contains_dict(param.annotation):
                violations.append(f"  {name}: {param.annotation!r} (expected {expected_type})")

        assert not violations, (
            f"{module_path}.{func_name} has dict-typed params that should use SDK types:\n"
            + "\n".join(violations)
            + "\nUse the proper SDK Pydantic type for each parameter."
        )
