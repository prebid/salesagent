"""Oracle: every ``AdCPError`` subclass's ``_default_recovery`` matches the pinned
``error-code.json`` ``enumMetadata`` recovery classification (#1417).

The ``enumMetadata`` block is normative — its ``$comment`` states: "SDKs MUST
consume this block ... the recovery classification embedded in that prose is
normative and MUST match the value here." This oracle locks in the auth-family
recovery fix (#1417: ``AUTH_REQUIRED`` is ``correctable``) and prevents
any exception class from drifting away from the spec's buyer-facing retry
semantics. Per-class tests that assert a hardcoded literal cannot catch a
divergence between the class and the spec — this parametrized oracle does.

Codes absent from the pinned enum (internal/adapter-only codes that have no AdCP
wire equivalent — e.g. ``WORKFLOW_CREATION_FAILED``, ``GAM_UPDATE_FAILED``) cannot
be graded against an enum that does not contain them; they are reported by
``test_internal_only_codes_are_documented`` rather than silently skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.exceptions import AdCPError

_PINNED_ENUM_PATH = Path(__file__).parent.parent / "fixtures" / "adcp_schemas_pinned" / "enums" / "error-code.json"


def _pinned_recovery_by_code() -> dict[str, str]:
    """Return ``{error_code: recovery}`` from the pinned enumMetadata block."""
    meta = json.loads(_PINNED_ENUM_PATH.read_text())["enumMetadata"]
    return {code: entry["recovery"] for code, entry in meta.items() if isinstance(entry, dict) and "recovery" in entry}


_RECOVERY_BY_CODE = _pinned_recovery_by_code()


def _adcp_error_subclasses() -> list[type[AdCPError]]:
    # Walk the concrete-subclass tree (the production single source of truth used by
    # tool_error_logging._build_error_code_to_status), not just the exceptions module
    # namespace — future-proofs the oracle against a subclass defined outside
    # exceptions.py that inspect.getmembers would silently miss. (#1417)
    return list(AdCPError.iter_concrete_subclasses())


_GRADED_CLASSES = sorted(
    (c for c in _adcp_error_subclasses() if c._default_error_code in _RECOVERY_BY_CODE),
    key=lambda c: c.__name__,
)


def test_pinned_enum_metadata_loaded() -> None:
    """Meta-guard: the pinned enum loaded and graded a representative set, so the
    parametrized oracle below can never silently degrade to zero cases."""
    assert len(_RECOVERY_BY_CODE) >= 50, (
        f"Expected the pinned enumMetadata to define recovery for many codes, got {len(_RECOVERY_BY_CODE)}"
    )
    assert len(_GRADED_CLASSES) >= 25, f"Expected to grade many AdCPError subclasses, got {len(_GRADED_CLASSES)}"


@pytest.mark.parametrize("cls", _GRADED_CLASSES, ids=lambda c: c.__name__)
def test_default_recovery_matches_pinned_enum(cls: type[AdCPError]) -> None:
    """Each subclass's ``_default_recovery`` must equal the pinned enum's normative recovery."""
    code = cls._default_error_code
    expected = _RECOVERY_BY_CODE[code]
    assert cls._default_recovery == expected, (
        f"{cls.__name__} (code {code!r}) declares recovery={cls._default_recovery!r} "
        f"but the pinned error-code.json enumMetadata says {expected!r}. The enumMetadata "
        f"recovery is normative (xc2j): fix the class, or advance the pin if the spec changed."
    )


def test_internal_only_codes_are_documented() -> None:
    """Codes carried by exception classes but absent from the pinned enum are
    internal/adapter-only (no AdCP wire equivalent). Pin the known set so a NEW
    exception class with a non-spec code is surfaced for review rather than
    silently escaping the recovery oracle."""
    internal_codes = {
        c._default_error_code for c in _adcp_error_subclasses() if c._default_error_code not in _RECOVERY_BY_CODE
    }
    known_internal = {
        "NOT_FOUND",
        "TASK_NOT_FOUND",
        "FORMAT_NOT_FOUND",
        "WORKFLOW_CREATION_FAILED",
        "LINE_ITEM_CREATION_FAILED",
        "PARTIAL_FAILURE",
        "ACTIVATION_WORKFLOW_FAILED",
        "GAM_UPDATE_FAILED",
        "MEDIA_BUY_REJECTED",
        "INVENTORY_UNAVAILABLE",
    }
    unexpected = internal_codes - known_internal
    assert not unexpected, (
        f"New non-spec error code(s) {sorted(unexpected)} are not in the pinned enum and so "
        f"escape the recovery oracle. Either add the code to the AdCP error-code enum (and the "
        f"pin) or, if it is genuinely internal-only, add it to known_internal here."
    )
