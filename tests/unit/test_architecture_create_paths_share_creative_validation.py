"""Guard: both create_media_buy approval paths run the shared creative validation.

Disease pattern (salesagent-kk15 / salesagent-39n0): the manual-approval
(pending) branch of ``_create_media_buy_impl`` duplicated the auto path's
creative-association logic minus its checks — missing creative_ids were
silently skipped (pending SUCCESS) and format mismatch raised a different
wire code (VALIDATION_ERROR vs CREATIVE_REJECTED). Same buyer input must be
rejected identically regardless of the tenant's approval mode.

The fix routes BOTH paths through ``_pre_validate_package_creatives`` (which
wraps ``_validate_creatives_before_adapter_call``) before anything persists.
This guard pins that shape: removing either call-site re-opens the divergence.
The behavioral contract itself is pinned by
``tests/integration/test_create_media_buy_behavioral.py::
TestManualApprovalPathCreativeValidation``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions, parse_module

_MEDIA_BUY_CREATE = Path(__file__).resolve().parents[2] / "src" / "core" / "tools" / "media_buy_create.py"

_SHARED_VALIDATOR = "_pre_validate_package_creatives"
_REQUIRED_CALL_SITES = 2  # pending branch + auto path


def count_shared_validator_calls(tree: ast.Module) -> int:
    """Count call sites of the shared pre-validation helper (definition excluded)."""
    count = 0
    for call in iter_call_expressions(tree, name=_SHARED_VALIDATOR):
        del call
        count += 1
    return count


class TestCreatePathsShareCreativeValidation:
    """Structural guard: pending and auto create paths share creative validation."""

    @pytest.mark.arch_guard
    def test_both_create_paths_call_shared_validator(self):
        tree = parse_module(_MEDIA_BUY_CREATE)
        calls = count_shared_validator_calls(tree)
        assert calls >= _REQUIRED_CALL_SITES, (
            f"Expected >= {_REQUIRED_CALL_SITES} call sites of {_SHARED_VALIDATOR} in "
            f"media_buy_create.py (manual-approval branch + auto path), found {calls}. "
            "Removing either call re-opens the approval-mode validation divergence "
            "(salesagent-kk15 / salesagent-39n0): missing creative_ids silently "
            "skipped on the pending path, or VALIDATION_ERROR instead of "
            "CREATIVE_REJECTED for format mismatch."
        )


class TestDetectorMetaTests:
    """Meta-tests: the counter sees calls and ignores the definition."""

    @pytest.mark.arch_guard
    def test_counter_counts_calls(self):
        tree = ast.parse(
            "def _create_media_buy_impl():\n"
            "    _pre_validate_package_creatives(a, b, c, d)\n"
            "    _pre_validate_package_creatives(a, b, c, d)\n"
        )
        assert count_shared_validator_calls(tree) == 2

    @pytest.mark.arch_guard
    def test_counter_ignores_definition_only(self):
        tree = ast.parse("def _pre_validate_package_creatives(a, b, c, d):\n    pass\n")
        assert count_shared_validator_calls(tree) == 0
