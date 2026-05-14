"""Structural guard: MCP wrapper scalar params must have Field(description=...).

Ensures that scalar parameters (str, int, float, bool) in MCP tool wrapper
functions use Annotated[type, Field(description=...)] so buyer agents see
meaningful descriptions in the JSON Schema, not just parameter names.
"""

import types
import typing
from typing import Any

import pytest
from pydantic.fields import FieldInfo

from tests.unit.test_architecture_wrapper_typed_params import MCP_WRAPPERS

# Parameters to skip — transport infrastructure or non-domain params
SKIP_PARAMS = {
    "ctx",  # FastMCP Context — transport infra
    "return",  # Return type annotation
}

# Base scalar types that need descriptions
SCALAR_TYPES = {str, int, float, bool}


def _get_base_types(annotation: Any) -> set[type]:
    """Extract the concrete base types from an annotation, unwrapping Annotated and Union."""
    # Unwrap Annotated first
    if hasattr(annotation, "__metadata__"):
        annotation = annotation.__args__[0]

    # Handle Union types (X | Y, Optional[X])
    if isinstance(annotation, types.UnionType):
        result = set()
        for arg in annotation.__args__:
            result.update(_get_base_types(arg))
        return result
    origin = getattr(annotation, "__origin__", None)
    if origin is typing.Union:
        result = set()
        for arg in typing.get_args(annotation):
            result.update(_get_base_types(arg))
        return result

    # Discard NoneType
    if annotation is type(None):
        return set()

    return {annotation}


def _is_scalar_type(annotation: Any) -> bool:
    """Check if annotation resolves to a scalar type (str, int, float, bool).

    Returns False for Pydantic models, lists, dicts, and other complex types.
    """
    base_types = _get_base_types(annotation)
    if not base_types:
        return False
    # All non-None base types must be scalar
    return all(t in SCALAR_TYPES for t in base_types)


def _has_field_description(annotation: Any) -> bool:
    """Check if annotation uses Annotated[..., Field(description=...)]."""
    metadata = getattr(annotation, "__metadata__", None)
    if metadata is None:
        return False
    for meta in metadata:
        if isinstance(meta, FieldInfo) and meta.description:
            return True
    return False


class TestMcpWrapperFieldDescriptions:
    """MCP wrapper scalar params must have Field(description=...)."""

    @pytest.mark.parametrize("module_path,func_name", MCP_WRAPPERS)
    def test_scalar_params_have_descriptions(self, module_path: str, func_name: str):
        """Every scalar param in MCP wrappers must have a Field description."""
        import importlib

        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        try:
            hints = typing.get_type_hints(func, include_extras=True)
        except Exception:
            import inspect

            sig = inspect.signature(func)
            hints = {n: p.annotation for n, p in sig.parameters.items()}

        violations = []
        for name, annotation in hints.items():
            if name in SKIP_PARAMS:
                continue
            if not _is_scalar_type(annotation):
                continue
            if not _has_field_description(annotation):
                violations.append(f"  {name}: {annotation!r}")

        assert not violations, (
            f"{module_path}.{func_name} has scalar params without Field(description=...):\n"
            + "\n".join(violations)
            + "\nUse Annotated[type, Field(description='...')] for buyer agent visibility."
        )
