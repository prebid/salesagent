"""Guard: both create_media_buy approval paths run the shared creative validation.

Disease pattern (PR #1430 review): the manual-approval
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


def _manual_branch_span(tree: ast.Module) -> tuple[int, int] | None:
    """Line span of the manual-approval If BODY (test references manual_approval_required).

    Several Ifs may reference the name (e.g. one-line guards); the manual-approval
    branch is the WIDEST such span — the multi-hundred-line pending-path body.
    The span covers ``body`` only: the ``orelse`` (auto path) must stay OUTSIDE,
    otherwise a call in the else branch would be credited to the manual branch
    (a latent false-negative fixed in the #1430 guard meta-drift cleanup).
    """
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and any(
            isinstance(n, ast.Name) and n.id == "manual_approval_required" for n in ast.walk(node.test)
        ):
            spans.append((node.body[0].lineno, node.body[-1].end_lineno or node.body[-1].lineno))
    if not spans:
        return None
    return max(spans, key=lambda s: s[1] - s[0])


def shared_validator_calls_per_branch(tree: ast.Module) -> tuple[int, int]:
    """(calls inside the manual-approval branch, calls outside it).

    Module-wide counting alone would pass with both calls in one branch
    (PR #1430 review nit) — split by the manual-approval If's line span.
    """
    span = _manual_branch_span(tree)
    inside = outside = 0
    for call in iter_call_expressions(tree, name=_SHARED_VALIDATOR):
        if span and span[0] <= call.lineno <= span[1]:
            inside += 1
        else:
            outside += 1
    return inside, outside


class TestCreatePathsShareCreativeValidation:
    """Structural guard: pending and auto create paths share creative validation."""

    @pytest.mark.arch_guard
    def test_both_create_paths_call_shared_validator(self):
        tree = parse_module(_MEDIA_BUY_CREATE)
        inside, outside = shared_validator_calls_per_branch(tree)
        assert inside >= 1 and outside >= 1, (
            f"Expected {_SHARED_VALIDATOR} to be called in BOTH create paths of "
            f"media_buy_create.py — found {inside} inside the manual-approval branch "
            f"and {outside} outside it (auto path). "
            "Removing either call re-opens the approval-mode validation divergence "
            "(PR #1430 review): missing creative_ids silently "
            "skipped on the pending path, or VALIDATION_ERROR instead of "
            "CREATIVE_REJECTED for format mismatch."
        )


class TestDetectorMetaTests:
    """Meta-tests target the LIVE per-branch detector with known-bad fixtures."""

    @pytest.mark.arch_guard
    def test_detector_catches_missing_manual_branch_call(self):
        """Known-bad mutation: the manual branch lost its call — inside == 0."""
        tree = ast.parse(
            "def _create_media_buy_impl():\n"
            "    if manual_approval_required:\n"
            "        persist_pending()\n"
            "    _pre_validate_package_creatives(a, b, c, d)\n"
        )
        assert shared_validator_calls_per_branch(tree) == (0, 1)

    @pytest.mark.arch_guard
    def test_detector_catches_both_calls_in_one_branch(self):
        """Known-bad mutation: both calls inside the manual branch — outside == 0.

        Module-wide counting would pass this; the per-branch split must not.
        """
        tree = ast.parse(
            "def _create_media_buy_impl():\n"
            "    if manual_approval_required:\n"
            "        _pre_validate_package_creatives(a, b, c, d)\n"
            "        _pre_validate_package_creatives(a, b, c, d)\n"
        )
        assert shared_validator_calls_per_branch(tree) == (2, 0)

    @pytest.mark.arch_guard
    def test_call_in_else_branch_counts_as_outside(self):
        """The orelse (auto path) is OUTSIDE the manual-branch span.

        The retired max-over-walk span included the else branch, so this
        healthy shape — one call per path — would have read as (2, 0) and a
        deleted auto-path call could hide behind the manual one.
        """
        tree = ast.parse(
            "def _create_media_buy_impl():\n"
            "    if manual_approval_required:\n"
            "        _pre_validate_package_creatives(a, b, c, d)\n"
            "    else:\n"
            "        _pre_validate_package_creatives(a, b, c, d)\n"
        )
        assert shared_validator_calls_per_branch(tree) == (1, 1)

    @pytest.mark.arch_guard
    def test_definition_only_counts_nothing(self):
        tree = ast.parse("def _pre_validate_package_creatives(a, b, c, d):\n    pass\n")
        assert shared_validator_calls_per_branch(tree) == (0, 0)
