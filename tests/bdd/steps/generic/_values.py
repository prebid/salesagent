"""Shared value-normalization helpers for BDD assertions."""

from __future__ import annotations

from typing import Any


def enum_value(value: Any) -> Any:
    """Return the wire value for enums while leaving plain values untouched."""
    return getattr(value, "value", value)
