"""Enum normalization helpers.

This module has ZERO project-level imports to avoid circular dependency issues
when used from both ``src.core.schemas`` and ``src.core.tools``.
"""

from enum import Enum
from typing import Any, overload


@overload
def enum_value(v: None) -> None: ...
@overload
def enum_value(v: Any) -> str: ...


def enum_value(v: Any) -> str | None:
    """Extract the string value from an enum, or return ``str(v)`` for non-enums.

    Returns ``None`` when *v* is ``None``, so callers that forward optional
    enum fields don't need a separate null-guard.

    Checks ``isinstance(v, Enum)`` first (precise), then falls back to
    ``hasattr(v, 'value')`` for duck-typed enum proxies (e.g., SDK-generated
    wrapper types or MagicMock in tests). Plain strings pass through via
    ``str(v)`` unchanged.
    """
    if v is None:
        return None
    if isinstance(v, Enum):
        return v.value
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)
