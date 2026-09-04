"""Oracle: each canonical buyer-facing suggestion constant matches the pinned
``error-code.json`` ``enumMetadata`` ``suggestion`` for its error code.

Companion to ``test_architecture_error_recovery_enum_conformance.py`` (which pins
``recovery``). The ``enumMetadata`` block is normative — every error code carries
its own canonical ``suggestion``, and a module-level constant that supplies the
buyer-facing hint for one code must not carry another code's text.

Reads suggestions through ``tests.helpers.pinned_schema`` (installed SDK tree) —
same pin as the recovery sibling — so one enum, one pin.
"""

from __future__ import annotations

import pytest

from src.core import exceptions
from src.core.exceptions import (
    REFERENCE_NOT_FOUND_MESSAGE,
    AdCPAuthenticationError,
    AdCPAuthRequiredError,
    AdCPError,
    AdCPFormatNotFoundError,
    translate_error_code,
)
from tests.helpers import pinned_schema


def _pinned_suggestion_by_code() -> dict[str, str]:
    """Return ``{error_code: suggestion}`` from the pinned enumMetadata block."""
    meta = pinned_schema.load("error-code.json")["enumMetadata"]
    return {
        code: entry["suggestion"] for code, entry in meta.items() if isinstance(entry, dict) and entry.get("suggestion")
    }


_SUGGESTION_BY_CODE = _pinned_suggestion_by_code()

# (module_constant_name, error_code) for every constant that exposes a code's
# canonical buyer-facing suggestion. Add a row when a new canonical-suggestion
# constant is introduced so it is pinned to the spec from birth.
_CANONICAL_SUGGESTION_CONSTANTS = [
    ("INVALID_REQUEST_SUGGESTION", "INVALID_REQUEST"),
    ("VALIDATION_ERROR_SUGGESTION", "VALIDATION_ERROR"),
    ("REFERENCE_NOT_FOUND_SUGGESTION", "REFERENCE_NOT_FOUND"),
]

# Classes whose ``_default_suggestion`` deliberately diverges from the pin today
# (pre-existing AUTH wording). Named allowlist — shrink only.
_SUGGESTION_PIN_ALLOWLIST: frozenset[type[AdCPError]] = frozenset(
    {
        AdCPAuthenticationError,
        AdCPAuthRequiredError,
    }
)


def test_pinned_enum_suggestions_loaded() -> None:
    """Meta-guard: the pinned enum loaded a representative set of suggestions."""
    assert len(_SUGGESTION_BY_CODE) >= 50, (
        f"Expected the pinned enumMetadata to define suggestions for many codes, got {len(_SUGGESTION_BY_CODE)}"
    )


@pytest.mark.parametrize(
    ("const_name", "code"),
    _CANONICAL_SUGGESTION_CONSTANTS,
    ids=[name for name, _ in _CANONICAL_SUGGESTION_CONSTANTS],
)
def test_suggestion_constant_matches_pinned_enum(const_name: str, code: str) -> None:
    """Each canonical-suggestion constant must equal the pinned enum's suggestion for its code."""
    assert code in _SUGGESTION_BY_CODE, (
        f"{code!r} carries no suggestion in the pinned error-code.json enumMetadata; "
        f"cannot ground {const_name}. Advance the pin or fix the mapping."
    )
    actual = getattr(exceptions, const_name)
    expected = _SUGGESTION_BY_CODE[code]
    assert actual == expected, (
        f"{const_name} = {actual!r} but the pinned error-code.json enumMetadata says the "
        f"{code} suggestion is {expected!r}. A code's canonical suggestion constant must carry "
        f"that code's text, not another code's: fix the constant, or advance the pin if the spec "
        f"changed the suggestion."
    )


def _classes_with_default_suggestion() -> list[type[AdCPError]]:
    return sorted(
        (
            c
            for c in AdCPError.iter_concrete_subclasses()
            if getattr(c, "_default_suggestion", None) is not None and c not in _SUGGESTION_PIN_ALLOWLIST
        ),
        key=lambda c: c.__name__,
    )


@pytest.mark.parametrize("cls", _classes_with_default_suggestion(), ids=lambda c: c.__name__)
def test_default_suggestion_matches_wire_code_pin(cls: type[AdCPError]) -> None:
    """Non-allowlisted ``_default_suggestion`` must equal the wire code's pinned hint."""
    wire = translate_error_code(cls._default_error_code)
    assert wire in _SUGGESTION_BY_CODE, f"{wire!r} carries no suggestion in the pinned error-code.json enumMetadata"
    assert cls._default_suggestion == _SUGGESTION_BY_CODE[wire]


def test_format_not_found_default_message_is_repo_authored_literal() -> None:
    """``message`` is repo-authored (enum defines none) — grade the literal, not identity."""
    assert AdCPFormatNotFoundError._default_message == REFERENCE_NOT_FOUND_MESSAGE
    assert AdCPFormatNotFoundError._default_message == "Reference not found"


def test_suggestion_pin_allowlist_is_exact() -> None:
    """Allowlisted classes still declare a non-None suggestion (stale-entry guard)."""
    for cls in _SUGGESTION_PIN_ALLOWLIST:
        assert cls._default_suggestion is not None, f"{cls.__name__} allowlisted but has no _default_suggestion"
