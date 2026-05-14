"""Structural guard: MCP and A2A wrapper parameters must use SDK types, not Any/dict.

Ensures that MCP tool wrapper functions and A2A _raw wrapper functions use proper
Pydantic SDK types for their parameters instead of Any or bare dict. This prevents
schema drift and ensures buyer agents see accurate tool schemas.
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

# Parameters allowed to use dict because no SDK type exists for them
ALLOWED_DICT_PARAMS = {
    "ext",  # AdCP ExtensionObject — intentionally free-form JSON
    "assignments",  # Bulk assignment map (creative_id -> package_ids), no SDK type
    "performance_data",  # Performance data list, no SDK type
}

# MCP wrapper functions to check (module_path, function_name)
MCP_WRAPPERS = [
    ("src.core.tools.products", "get_products"),
    ("src.core.tools.media_buy_create", "create_media_buy"),
    ("src.core.tools.media_buy_update", "update_media_buy"),
    ("src.core.tools.media_buy_delivery", "get_media_buy_delivery"),
    ("src.core.tools.media_buy_list", "get_media_buys"),
    ("src.core.tools.creatives.sync_wrappers", "sync_creatives"),
    ("src.core.tools.creatives.listing", "list_creatives"),
    ("src.core.tools.properties", "list_authorized_properties"),
    ("src.core.tools.accounts", "list_accounts"),
    ("src.core.tools.accounts", "sync_accounts"),
    ("src.core.tools.capabilities", "get_adcp_capabilities"),
    ("src.core.tools.creative_formats", "list_creative_formats"),
]

# A2A raw wrapper functions to check (module_path, function_name)
A2A_RAW_WRAPPERS = [
    ("src.core.tools.products", "get_products_raw"),
    ("src.core.tools.media_buy_create", "create_media_buy_raw"),
    ("src.core.tools.media_buy_update", "update_media_buy_raw"),
    ("src.core.tools.media_buy_delivery", "get_media_buy_delivery_raw"),
    ("src.core.tools.media_buy_list", "get_media_buys_raw"),
    ("src.core.tools.creatives.sync_wrappers", "sync_creatives_raw"),
    ("src.core.tools.creatives.listing", "list_creatives_raw"),
    ("src.core.tools.properties", "list_authorized_properties_raw"),
    ("src.core.tools.accounts", "list_accounts_raw"),
    ("src.core.tools.accounts", "sync_accounts_raw"),
    ("src.core.tools.capabilities", "get_adcp_capabilities_raw"),
    ("src.core.tools.creative_formats", "list_creative_formats_raw"),
    ("src.core.tools.signals", "get_signals_raw"),
    ("src.core.tools.signals", "activate_signal_raw"),
    ("src.core.tools.performance", "update_performance_index_raw"),
]

# Parameters that MUST use specific SDK types (param_name -> expected type name)
# These are params where dict[str, Any] or Any is wrong — a proper SDK type exists.
REQUIRED_SDK_TYPES = {
    "reporting_dimensions": "ReportingDimensions",
    "attribution_window": "AttributionWindow",
    "account": "AccountReference",
    "property_list": "PropertyListReference",
    "brand": "BrandReference",
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


def _get_resolved_hints(module_path: str, func_name: str) -> dict[str, Any]:
    """Get resolved type hints for a function, handling __future__ annotations."""
    import importlib

    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    try:
        return typing.get_type_hints(func, include_extras=True)
    except Exception:
        # Fall back to inspect.signature for functions where get_type_hints fails
        sig = inspect.signature(func)
        return {name: param.annotation for name, param in sig.parameters.items()}


def _check_no_any_params(module_path: str, func_name: str) -> list[str]:
    """Check that no parameter uses Any type (returns list of violations)."""
    hints = _get_resolved_hints(module_path, func_name)

    violations = []
    for name, annotation in hints.items():
        if name in ALLOWED_ANY_PARAMS:
            continue
        if name == "return":
            continue
        if _is_any_type(annotation):
            violations.append(f"  {name}: {annotation!r}")
    return violations


def _check_no_dict_params(module_path: str, func_name: str) -> list[str]:
    """Check that no parameter uses bare dict where an SDK type exists (returns list of violations)."""
    hints = _get_resolved_hints(module_path, func_name)

    violations = []
    for name, annotation in hints.items():
        if name in ALLOWED_ANY_PARAMS or name in ALLOWED_DICT_PARAMS:
            continue
        if name == "return":
            continue
        if _contains_dict(annotation):
            violations.append(f"  {name}: {annotation!r}")
    return violations


def _check_sdk_typed_params(module_path: str, func_name: str) -> list[str]:
    """Check that params with known SDK types use them, not dict (returns list of violations)."""
    hints = _get_resolved_hints(module_path, func_name)

    violations = []
    for name, annotation in hints.items():
        if name not in REQUIRED_SDK_TYPES:
            continue
        expected_type = REQUIRED_SDK_TYPES[name]
        if _contains_dict(annotation) or _is_any_type(annotation):
            violations.append(f"  {name}: {annotation!r} (expected {expected_type})")
    return violations


class TestMcpWrapperTypedParams:
    """MCP wrappers must not use Any or dict for domain parameters."""

    @pytest.mark.parametrize("module_path,func_name", MCP_WRAPPERS)
    def test_no_any_params(self, module_path: str, func_name: str):
        """No MCP wrapper parameter should use Any type."""
        violations = _check_no_any_params(module_path, func_name)
        assert not violations, (
            f"{module_path}.{func_name} has parameters with Any type:\n"
            + "\n".join(violations)
            + "\nUse proper SDK types instead of Any."
        )

    @pytest.mark.parametrize("module_path,func_name", MCP_WRAPPERS)
    def test_no_dict_params(self, module_path: str, func_name: str):
        """No MCP wrapper parameter should use bare dict (except allowed params)."""
        violations = _check_no_dict_params(module_path, func_name)
        assert not violations, (
            f"{module_path}.{func_name} has parameters with dict type:\n"
            + "\n".join(violations)
            + "\nUse proper SDK Pydantic types instead of dict."
        )

    @pytest.mark.parametrize("module_path,func_name", MCP_WRAPPERS)
    def test_sdk_typed_params(self, module_path: str, func_name: str):
        """Parameters with known SDK types must use them, not dict or Any."""
        violations = _check_sdk_typed_params(module_path, func_name)
        assert not violations, (
            f"{module_path}.{func_name} has params that should use SDK types:\n"
            + "\n".join(violations)
            + "\nUse the proper SDK Pydantic type for each parameter."
        )


class TestA2aRawWrapperTypedParams:
    """A2A _raw wrappers must not use Any or dict for domain parameters."""

    @pytest.mark.parametrize("module_path,func_name", A2A_RAW_WRAPPERS)
    def test_no_any_params(self, module_path: str, func_name: str):
        """No A2A _raw wrapper parameter should use Any type."""
        violations = _check_no_any_params(module_path, func_name)
        assert not violations, (
            f"{module_path}.{func_name} has parameters with Any type:\n"
            + "\n".join(violations)
            + "\nUse proper SDK types instead of Any."
        )

    @pytest.mark.parametrize("module_path,func_name", A2A_RAW_WRAPPERS)
    def test_no_dict_params(self, module_path: str, func_name: str):
        """No A2A _raw wrapper parameter should use bare dict (except allowed params)."""
        violations = _check_no_dict_params(module_path, func_name)
        assert not violations, (
            f"{module_path}.{func_name} has parameters with dict type:\n"
            + "\n".join(violations)
            + "\nUse proper SDK Pydantic types instead of dict."
        )

    @pytest.mark.parametrize("module_path,func_name", A2A_RAW_WRAPPERS)
    def test_sdk_typed_params(self, module_path: str, func_name: str):
        """Parameters with known SDK types must use them, not dict or Any."""
        violations = _check_sdk_typed_params(module_path, func_name)
        assert not violations, (
            f"{module_path}.{func_name} has params that should use SDK types:\n"
            + "\n".join(violations)
            + "\nUse the proper SDK Pydantic type for each parameter."
        )
