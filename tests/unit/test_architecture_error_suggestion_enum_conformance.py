"""Oracle: each canonical buyer-facing suggestion constant matches the pinned
``error-code.json`` ``enumMetadata`` ``suggestion`` for its error code.

Companion to ``test_architecture_error_recovery_enum_conformance.py`` (which pins
``recovery``). The ``enumMetadata`` block is normative — every error code carries
its own canonical ``suggestion``, and a module-level constant that supplies the
buyer-facing hint for one code must not carry another code's text.

This locks in the per-code split where ``VALIDATION_ERROR_SUGGESTION`` had drifted
to ``INVALID_REQUEST``'s canonical hint ("check request parameters and fix") while
being emitted on ``VALIDATION_ERROR`` paths. It reddens if the two constants are
swapped, mis-edited, or the spec advances a suggestion without the constant
following. A per-site test asserting a hardcoded literal cannot catch a divergence
between the constant and the spec — this oracle grounds it in the pinned enum.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import exceptions

_PINNED_ENUM_PATH = Path(__file__).parent.parent / "fixtures" / "adcp_schemas_pinned" / "enums" / "error-code.json"


def _pinned_suggestion_by_code() -> dict[str, str]:
    """Return ``{error_code: suggestion}`` from the pinned enumMetadata block."""
    meta = json.loads(_PINNED_ENUM_PATH.read_text())["enumMetadata"]
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
    ("POLICY_VIOLATION_SUGGESTION", "POLICY_VIOLATION"),
]


def test_pinned_enum_suggestions_loaded() -> None:
    """Meta-guard: the pinned enum loaded a representative set of suggestions, so the
    parametrized oracle below can never silently degrade to zero graded cases."""
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
