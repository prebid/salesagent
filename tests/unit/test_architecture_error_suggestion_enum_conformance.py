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

import pytest

from src.core import exceptions
from src.core.exceptions import AdCPError, translate_error_code
from tests.helpers import pinned_schema
from tests.unit._architecture_helpers import assert_violations_match_allowlist


def _pinned_suggestion_by_code() -> dict[str, str]:
    """Return ``{error_code: suggestion}`` from the vendored enumMetadata block."""
    meta = pinned_schema.load_vendored("error-code.json")["enumMetadata"]
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
    ("AUTH_REQUIRED_SUGGESTION", "AUTH_REQUIRED"),
]

# Per-class ``_default_suggestion`` ClassVars that intentionally diverge from
# the pinned enum. Shrink only — never grow without a tracker. Empty after
# aligning AdCPAuthenticationError to the pinned AUTH_REQUIRED suggestion.
_DEFAULT_SUGGESTION_ALLOWLIST: frozenset[str] = frozenset()


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


def test_default_suggestion_classvars_match_pinned_enum() -> None:
    """Each concrete subclass that declares ``_default_suggestion`` must match the
    pinned enum suggestion for its *wire* code (after ERROR_CODE_MAPPING).

    Module-level constants are graded above via getattr; ClassVars fall outside
    that scan. Deleting ``AdCPTaskNotFoundError._default_suggestion`` must redden
    here. Allowlisted divergences are pre-existing and must shrink only.
    """
    graded = [c for c in AdCPError.iter_concrete_subclasses() if getattr(c, "_default_suggestion", None) is not None]
    assert graded, "Expected at least one AdCPError subclass to declare _default_suggestion"
    declaring_names = {c.__name__ for c in graded}
    assert "AdCPTaskNotFoundError" in declaring_names, (
        "AdCPTaskNotFoundError must declare _default_suggestion (wire REFERENCE_NOT_FOUND oracle)"
    )
    mismatched: set[str] = set()
    for cls in graded:
        wire = translate_error_code(cls._default_error_code)
        if wire not in _SUGGESTION_BY_CODE or cls._default_suggestion != _SUGGESTION_BY_CODE[wire]:
            mismatched.add(cls.__name__)
    assert_violations_match_allowlist(
        mismatched,
        set(_DEFAULT_SUGGESTION_ALLOWLIST),
        fix_hint=(
            "ClassVar _default_suggestion must match the pinned enum suggestion for the "
            "class's wire code (after ERROR_CODE_MAPPING). Fix the ClassVar, or shrink "
            "_DEFAULT_SUGGESTION_ALLOWLIST only when a divergence is intentional/tracked."
        ),
    )


def test_unknown_wire_code_classifies_as_mismatch() -> None:
    """Negative self-test: internal-only code missing from the pin enters mismatched.

    Grades the ``wire not in _SUGGESTION_BY_CODE`` arm of the ClassVar oracle —
    both production classes' codes are in the pin, so that operand is otherwise
    unexercised.
    """

    class _InternalOnly(AdCPError):
        _default_error_code = "INTERNAL_ONLY_NO_PIN"
        _default_suggestion = "not a pinned suggestion"

    wire = translate_error_code(_InternalOnly._default_error_code)
    assert wire not in _SUGGESTION_BY_CODE or _InternalOnly._default_suggestion != _SUGGESTION_BY_CODE.get(wire)
    # The classification predicate used by the oracle must treat this as mismatched.
    mismatched = wire not in _SUGGESTION_BY_CODE or _InternalOnly._default_suggestion != _SUGGESTION_BY_CODE.get(
        wire, object()
    )
    assert mismatched is True
