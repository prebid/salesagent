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

import importlib
import json
from pathlib import Path

import pytest

_PINNED_ENUM_PATH = Path(__file__).parent.parent / "fixtures" / "adcp_schemas_pinned" / "enums" / "error-code.json"


def _pinned_suggestion_by_code() -> dict[str, str]:
    """Return ``{error_code: suggestion}`` from the pinned enumMetadata block."""
    meta = json.loads(_PINNED_ENUM_PATH.read_text())["enumMetadata"]
    return {
        code: entry["suggestion"] for code, entry in meta.items() if isinstance(entry, dict) and entry.get("suggestion")
    }


_SUGGESTION_BY_CODE = _pinned_suggestion_by_code()

# (module_path, constant_name, error_code) for every module-level constant that exposes a
# code's canonical buyer-facing suggestion. Module-qualified so a canonical constant born in
# ANY module (not just src.core.exceptions) is pinned to the spec — a constant that resolved
# only through ``getattr(exceptions, ...)`` silently escaped this oracle (#1329).
# Add a row when a new canonical-suggestion constant is introduced so it is pinned from birth.
_CANONICAL_SUGGESTION_CONSTANTS = [
    ("src.core.exceptions", "INVALID_REQUEST_SUGGESTION", "INVALID_REQUEST"),
    ("src.core.exceptions", "VALIDATION_ERROR_SUGGESTION", "VALIDATION_ERROR"),
    ("src.core.exceptions", "CREDENTIAL_IN_ARGS_SUGGESTION", "CREDENTIAL_IN_ARGS"),
    ("src.core.tools.governance", "_UNRESOLVED_ACCOUNT_SUGGESTION", "ACCOUNT_NOT_FOUND"),
]


def test_pinned_enum_suggestions_loaded() -> None:
    """Meta-guard: the pinned enum loaded a representative set of suggestions, so the
    parametrized oracle below can never silently degrade to zero graded cases."""
    assert len(_SUGGESTION_BY_CODE) >= 50, (
        f"Expected the pinned enumMetadata to define suggestions for many codes, got {len(_SUGGESTION_BY_CODE)}"
    )


@pytest.mark.parametrize(
    ("module_path", "const_name", "code"),
    _CANONICAL_SUGGESTION_CONSTANTS,
    ids=[f"{module.rsplit('.', 1)[-1]}.{name}" for module, name, _ in _CANONICAL_SUGGESTION_CONSTANTS],
)
def test_suggestion_constant_matches_pinned_enum(module_path: str, const_name: str, code: str) -> None:
    """Each canonical-suggestion constant must equal the pinned enum's suggestion for its code."""
    assert code in _SUGGESTION_BY_CODE, (
        f"{code!r} carries no suggestion in the pinned error-code.json enumMetadata; "
        f"cannot ground {const_name}. Advance the pin or fix the mapping."
    )
    actual = getattr(importlib.import_module(module_path), const_name)
    expected = _SUGGESTION_BY_CODE[code]
    assert actual == expected, (
        f"{module_path}.{const_name} = {actual!r} but the pinned error-code.json enumMetadata says the "
        f"{code} suggestion is {expected!r}. A code's canonical suggestion constant must carry "
        f"that code's text, not another code's: fix the constant, or advance the pin if the spec "
        f"changed the suggestion."
    )
