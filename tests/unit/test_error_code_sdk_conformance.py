"""Every spec-claimed error code emitted by exceptions.py exists in the pinned AdCP enum.

The ``_default_error_code`` ClassVars in ``src/core/exceptions.py`` are string
literals by local convention (150+ sibling sites compare them as strings). A
literal can silently drift from the AdCP spec — a typo like
``MEDIA_BUY_NOTFOUND`` would ship a non-spec code to buyers with no test
noticing. This tripwire pins every emitted code against the vendored, pinned
AdCP 3.1.1 ``enums/error-code.json`` (canonical per ``docs/adcp-spec-version.md``)
— the same schema the harness grades wire recovery against
(``tests/harness/transport.py``). ``adcp.ErrorCode``, the SDK's generated
re-export, is a SECONDARY cross-check: it equals the pinned schema at the
current pin, but the vendored schema is the authority, so a future SDK regen
that drops or renames a code fails the cross-check here instead of silently
moving the baseline.

Codes that deliberately have NO spec counterpart (internal codes, translated
at the wire boundary) live in the shrink-only allowlist below. If a code
gains a spec counterpart, or its exception class is deleted, the stale-entry
check forces the allowlist entry to be removed.
"""

from __future__ import annotations

import json
from pathlib import Path

from adcp import ErrorCode

import src.core.exceptions as exceptions_module
from src.core.exceptions import AdCPError

# The pinned AdCP error-code enum is the AUTHORITY (canonical per
# docs/adcp-spec-version.md) — the same vendored schema the harness grades wire
# recovery against. adcp.ErrorCode is its generated SDK proxy, used below only
# as a secondary cross-check.
_PINNED_ERROR_ENUM = (
    Path(__file__).resolve().parents[1] / "fixtures" / "adcp_schemas_pinned" / "enums" / "error-code.json"
)


def _pinned_spec_codes() -> set[str]:
    """The canonical AdCP error-code vocabulary from the pinned enum schema."""
    return set(json.loads(_PINNED_ERROR_ENUM.read_text())["enum"])


# Codes emitted by AdCPError subclasses with no spec counterpart in the pinned
# enum. Internal or adapter-specific; several are translated to a spec code at
# the wire boundary (e.g. TASK_NOT_FOUND → INVALID_REQUEST, see
# AdCPTaskNotFoundError). Shrink-only: add a new entry only when the spec
# genuinely lacks the code, remove entries as the spec catches up.
_INTERNAL_WIRE_CODES: frozenset[str] = frozenset(
    {
        "ACTIVATION_WORKFLOW_FAILED",
        "FORMAT_NOT_FOUND",
        "GAM_UPDATE_FAILED",
        "INTERNAL_ERROR",
        "INVENTORY_UNAVAILABLE",
        "LINE_ITEM_CREATION_FAILED",
        "MEDIA_BUY_REJECTED",
        "NOT_FOUND",
        "PARTIAL_FAILURE",
        "TASK_NOT_FOUND",
        "WORKFLOW_CREATION_FAILED",
    }
)


def _all_emitted_codes() -> set[str]:
    """Collect the ``_default_error_code`` every concrete AdCPError subclass emits.

    Routes through ``AdCPError.iter_concrete_subclasses`` — the single source of
    truth for this walk (it also backs the wire-code→status table) — which
    dedupes diamond inheritance and would skip abstract bases if any were
    introduced, rather than re-implementing the traversal here. Reads the
    effective (inherited) code per concrete class and adds ``AdCPError``'s own
    default, since the walk yields descendants only.
    """
    codes = {
        code
        for cls in AdCPError.iter_concrete_subclasses()
        if isinstance((code := getattr(cls, "_default_error_code", None)), str)
    }
    # AdCPError itself is the emitted fallback for unmapped errors; its own
    # default (INTERNAL_ERROR) is not a descendant, so add it explicitly.
    base_code = AdCPError.__dict__.get("_default_error_code")
    if isinstance(base_code, str):
        codes.add(base_code)
    return codes


def test_every_spec_claimed_error_code_exists_in_pinned_enum() -> None:
    assert exceptions_module is not None  # subclasses registered via import
    spec_codes = _pinned_spec_codes()
    emitted = _all_emitted_codes()
    assert emitted, "No _default_error_code found — subclass walk is broken"

    unknown = sorted(emitted - spec_codes - _INTERNAL_WIRE_CODES)
    assert not unknown, (
        f"Error code(s) {unknown} are emitted by exceptions.py but exist in "
        "neither the pinned AdCP enums/error-code.json nor the internal "
        "allowlist. Either fix the typo/drift against the spec, or — if the "
        "code is genuinely internal — add it to _INTERNAL_WIRE_CODES with a "
        "translation note."
    )


def test_sdk_enum_still_carries_every_spec_claimed_code() -> None:
    """Secondary cross-check: the SDK proxy still carries every emitted spec code.

    ``adcp.ErrorCode`` is generated from the spec; at the current pin it is a
    superset of the pinned enum. If a future SDK regen drops or renames a code
    we emit, this fires — surfacing the drift the pinned-primary grading above
    would not otherwise catch (it grades against the frozen vendored file).
    """
    sdk_codes = {e.value for e in ErrorCode}
    emitted = _all_emitted_codes()

    missing = sorted(emitted - sdk_codes - _INTERNAL_WIRE_CODES)
    assert not missing, (
        f"Spec-claimed emitted code(s) {missing} are in the pinned AdCP enum "
        "but no longer in adcp.ErrorCode — the SDK regen dropped/renamed them. "
        "Reconcile the pin (pyproject adcp bump) against the vendored schema."
    )


def test_internal_allowlist_has_no_stale_entries() -> None:
    spec_codes = _pinned_spec_codes()
    emitted = _all_emitted_codes()

    graduated = sorted(_INTERNAL_WIRE_CODES & spec_codes)
    assert not graduated, (
        f"Allowlisted code(s) {graduated} now exist in the pinned AdCP enum — "
        "remove them from _INTERNAL_WIRE_CODES so drift detection covers them."
    )

    unused = sorted(_INTERNAL_WIRE_CODES - emitted)
    assert not unused, (
        f"Allowlisted code(s) {unused} are no longer emitted by any AdCPError "
        "subclass — remove the stale entries (shrink-only allowlist)."
    )
