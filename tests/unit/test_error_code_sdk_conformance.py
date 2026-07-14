"""Every spec-claimed error code emitted by exceptions.py exists in the SDK enum.

The ``_default_error_code`` ClassVars in ``src/core/exceptions.py`` are string
literals by local convention (150+ sibling sites compare them as strings). A
literal can silently drift from the AdCP spec — a typo like
``MEDIA_BUY_NOTFOUND`` would ship a non-spec code to buyers with no test
noticing. This tripwire pins every emitted code against ``adcp.ErrorCode``.

Codes that deliberately have NO SDK counterpart (internal codes, translated
at the wire boundary) live in the shrink-only allowlist below. If a code
gains an SDK counterpart, or its exception class is deleted, the stale-entry
check forces the allowlist entry to be removed.
"""

from __future__ import annotations

from adcp import ErrorCode

import src.core.exceptions as exceptions_module
from src.core.exceptions import AdCPError

# Codes emitted by AdCPError subclasses with no adcp.ErrorCode counterpart.
# Internal or adapter-specific; several are translated to a spec code at the
# wire boundary (e.g. TASK_NOT_FOUND → INVALID_REQUEST, see
# AdCPTaskNotFoundError). Shrink-only: add a new entry only when the SDK
# genuinely lacks the code, remove entries as the SDK catches up.
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

    Uses ``AdCPError.iter_concrete_subclasses`` — the single source of truth for
    this walk — so it dedupes diamond inheritance and skips abstract bases that
    are never emitted, which a hand-rolled ``__subclasses__`` stack does not.
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


def test_every_spec_claimed_error_code_exists_in_sdk_enum() -> None:
    assert exceptions_module is not None  # subclasses registered via import
    sdk_codes = {e.value for e in ErrorCode}
    emitted = _all_emitted_codes()
    assert emitted, "No _default_error_code found — subclass walk is broken"

    unknown = sorted(emitted - sdk_codes - _INTERNAL_WIRE_CODES)
    assert not unknown, (
        f"Error code(s) {unknown} are emitted by exceptions.py but exist in "
        "neither adcp.ErrorCode nor the internal allowlist. Either fix the "
        "typo/drift against the spec, or — if the code is genuinely internal — "
        "add it to _INTERNAL_WIRE_CODES with a translation note."
    )


def test_internal_allowlist_has_no_stale_entries() -> None:
    sdk_codes = {e.value for e in ErrorCode}
    emitted = _all_emitted_codes()

    graduated = sorted(_INTERNAL_WIRE_CODES & sdk_codes)
    assert not graduated, (
        f"Allowlisted code(s) {graduated} now exist in adcp.ErrorCode — remove "
        "them from _INTERNAL_WIRE_CODES so drift detection covers them."
    )

    unused = sorted(_INTERNAL_WIRE_CODES - emitted)
    assert not unused, (
        f"Allowlisted code(s) {unused} are no longer emitted by any AdCPError "
        "subclass — remove the stale entries (shrink-only allowlist)."
    )
