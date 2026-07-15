"""Exact-membership pins for the workflow-step cancellation taxonomy.

``CANCELLABLE_STEP_STATUSES`` is the single source of truth for which statuses a buyer
cancel (A2A ``tasks/cancel`` → ``WorkflowRepository.cancel_if_cancellable``) is still safe
from. These tests pin the *exact* partition — every included status and every excluded
status — as a pure-value contract, independent of any DB-backed behavior test. A silent
addition (e.g. re-adding ``in_progress`` or ``approved``, which would let a cancel strand a
real ad-server order behind a canceled task) fails here immediately.
"""

from src.core.database.repositories.workflow import (
    CANCELLABLE_STEP_STATUSES,
    TERMINAL_STEP_STATUSES,
)

# Hardcoded expectations (NOT derived from the production constants) so a drift in the
# source set is caught, not mirrored.
_CANCELLABLE = {"pending", "requires_approval", "pending_approval"}
# Non-terminal statuses that are nonetheless NOT cancellable because irreversible external
# work has committed (``approved``) or is underway (``in_progress``).
_NONCANCELLABLE_NONTERMINAL = {"in_progress", "approved"}
_TERMINAL = {"completed", "rejected", "failed", "canceled"}


def test_cancellable_set_is_exactly_the_three_pre_sideeffect_statuses():
    assert CANCELLABLE_STEP_STATUSES == _CANCELLABLE


def test_terminal_set_is_exact():
    assert TERMINAL_STEP_STATUSES == _TERMINAL


def test_every_noncancellable_status_is_excluded():
    # in_progress + approved (work underway/committed) and every terminal status must be
    # absent from the cancellable set.
    for status in _NONCANCELLABLE_NONTERMINAL | _TERMINAL:
        assert status not in CANCELLABLE_STEP_STATUSES, f"{status!r} must NOT be cancellable"


def test_cancellable_and_terminal_are_disjoint():
    assert CANCELLABLE_STEP_STATUSES.isdisjoint(TERMINAL_STEP_STATUSES)
