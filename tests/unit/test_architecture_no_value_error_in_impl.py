"""Structural guard: ``raise ValueError(...)`` in business logic must shrink toward zero.

``ValueError`` is transport-agnostic but does not carry an AdCP error code, a
recovery hint, or context. At the transport boundary it is mapped to a synthetic
``AdCPValidationError`` (VALIDATION_ERROR), which loses the semantic specificity
the caller intended. Per the error-emission architecture, business logic should
raise typed AdCPSalesAgentError subclasses (AdCPValidationError, AdCPBudgetTooLowError,
AdCPMediaBuyNotFoundError, etc.) with explicit codes.

This guard counts ``raise ValueError(...)`` sites in ``src/core/tools/`` and
``src/adapters/`` per file, with a per-file CAP frozen at substrate landing.
Caps only SHRINK over time as PR 2 cleanup migrates sites to typed raises.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Per-file caps for ``raise ValueError(...)`` sites. Two categories of entries:
#
#   1. **Migration targets** — boundary-facing raises that should become typed
#      ``AdCPSalesAgentError`` subclasses. Each site carries a
#      ``# FIXME(#<gh-issue>): migrate to typed AdCPSalesAgentError raise``
#      comment so reviewers can grep to the cleanup work. PR 2 sub-batches
#      drain these. The citation is a GitHub issue, never a local beads id —
#      beads ids do not resolve for outside contributors (CLAUDE.md "Rules for
#      guards"), and check_fixme_citation_count.py ratchets the spelling.
#
#   2. **Internal contracts** — ``ValueError`` raised inside helper functions
#      to enforce programmer-error invariants (Pydantic validators, factory
#      "unknown type" guards, schema-config validators). These crash with a
#      stack trace if violated and the boundary catchall wraps any that escape.
#      Per the boundary-vs-internal rule (memory `feedback_valueerror_boundary_vs_internal`),
#      internal contracts stay as ValueError; PR 2 only migrates the boundary set.
#
# A future split (e.g. ``BOUNDARY_PER_FILE_CAP`` + ``INTERNAL_PER_FILE_CAP``)
# would make the distinction visible at the guard level. For now, both
# categories share the cap dict and shrink together as PR 2 lands.
VALUE_ERROR_PER_FILE_CAP: dict[str, int] = {
    "src/adapters/__init__.py": 0,
    "src/adapters/base.py": 0,
    "src/adapters/broadstreet/config_schema.py": 4,
    "src/adapters/gam/auth.py": 0,
    "src/adapters/gam/client.py": 0,
    # 3→0: the creative-input rejection sites now raise
    # AdCPCreativeRejectedError (CREATIVE_REJECTED/correctable).
    "src/adapters/gam/managers/orders.py": 0,
    # 22→7: the 15 buyer-correctable capability-gap sites now raise
    # AdCPCapabilityNotSupportedError (UNSUPPORTED_FEATURE/correctable); the 7
    # remaining ValueErrors are seller-side operational faults by design.
    "src/adapters/gam/managers/targeting.py": 0,
    # 2→1: the pricing-model capability gap is typed; the remaining ValueError
    # is the seller-config incompatible-override raise (deliberately untyped).
    "src/adapters/gam/pricing_compatibility.py": 0,
    "src/adapters/gam_implementation_config_schema.py": 4,
    "src/adapters/xandr.py": 0,
    "src/core/tools/media_buy_create.py": 2,  # null-session guard + agent_url HTTP(S) validation (internal contracts)
}

from tests.unit._architecture_helpers import REPO_ROOT, SCAN_DIRS, safe_parse
from tests.unit._architecture_helpers import rel as _rel


def _count_value_error_raises(filepath: Path) -> list[int]:
    """Return line numbers of ``raise ValueError(...)`` in the file."""
    tree = safe_parse(filepath)
    if tree is None:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call):
                func = exc.func
                if isinstance(func, ast.Name) and func.id == "ValueError":
                    lines.append(node.lineno)
    return lines


class TestNoValueErrorInImpl:
    """``raise ValueError(...)`` sites must stay within their per-file cap."""

    @pytest.mark.arch_guard
    def test_value_error_sites_within_caps(self):
        from tests.unit._per_file_cap_guard import assert_per_file_caps

        assert_per_file_caps(
            cap_dict=VALUE_ERROR_PER_FILE_CAP,
            count_sites=_count_value_error_raises,
            scan_dirs=SCAN_DIRS,
            site_label="raise ValueError",
            typed_raise_hint="convert to typed AdCPSalesAgentError raise (e.g., AdCPValidationError)",
            rel=_rel,
        )

    @pytest.mark.arch_guard
    def test_capped_files_still_exist(self):
        """Stale-cap detection."""
        from tests.unit._per_file_cap_guard import assert_capped_files_still_exist

        assert_capped_files_still_exist(VALUE_ERROR_PER_FILE_CAP, "VALUE_ERROR_PER_FILE_CAP", repo_root=REPO_ROOT)

    @pytest.mark.arch_guard
    def test_caps_only_shrink(self):
        """If a file has fewer sites than its cap, lower the cap to match."""
        from tests.unit._per_file_cap_guard import assert_caps_only_shrink

        assert_caps_only_shrink(VALUE_ERROR_PER_FILE_CAP, _count_value_error_raises, repo_root=REPO_ROOT)
