"""Unit tests for the single enum-normalization helper, ``enum_value``.

``enum_value`` is the project's single source of truth for extracting the
string value of an enum (or duck-typed enum proxy). It is the superset that
absorbed the former ``resolve_enum_value`` duplicate from
``src.core.validation_helpers`` — these tests lock the superset contract so a
future regression cannot silently reintroduce a narrower copy.
"""

from __future__ import annotations

from enum import StrEnum

from src.core.enum_helpers import enum_value
from src.core.helpers import enum_value as enum_value_reexport


class _Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class _DuckValue:
    """Duck-typed enum proxy (SDK wrapper / MagicMock shape): has ``.value``."""

    def __init__(self, value: str) -> None:
        self.value = value


def test_enum_member_returns_value() -> None:
    assert enum_value(_Color.RED) == "red"
    assert enum_value(_Color.BLUE) == "blue"


def test_plain_string_passes_through() -> None:
    assert enum_value("system_x") == "system_x"


def test_empty_string_passes_through() -> None:
    # targeting_capabilities passes item.get("system", "") — empty string must
    # survive unchanged (used as a dict key), not become None.
    assert enum_value("") == ""


def test_none_returns_none() -> None:
    # This is the superset behavior that justified keeping enum_value over the
    # former resolve_enum_value (-> str). Optional enum fields forward cleanly.
    assert enum_value(None) is None


def test_duck_typed_value_is_stringified() -> None:
    assert enum_value(_DuckValue("metro")) == "metro"
    # Non-str .value is coerced to str.
    assert enum_value(_DuckValue(42)) == "42"


def test_helpers_reexport_is_same_callable() -> None:
    # src.core.helpers re-exports the one true helper — not a second copy.
    assert enum_value_reexport is enum_value
