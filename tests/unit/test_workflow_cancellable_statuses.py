"""Exact-membership pins for the workflow-step cancellation taxonomy.

``CANCELLABLE_STEP_STATUSES`` is the single source of truth for which statuses a buyer
cancel (A2A ``tasks/cancel`` → ``WorkflowRepository.cancel_if_cancellable``) is still safe
from. These tests pin the *exact* partition — every included status and every excluded
status — as a pure-value contract, independent of any DB-backed behavior test. A silent
addition (e.g. re-adding ``in_progress`` or ``approved``, which would let a cancel strand a
real ad-server order behind a canceled task) fails here immediately.
"""

from src.core.database.repositories.workflow import (
    APPROVABLE_STEP_STATUSES,
    CANCELLABLE_STEP_STATUSES,
    TERMINAL_STEP_STATUSES,
)

# Hardcoded expectations (NOT derived from the production constants) so a drift in the
# source set is caught, not mirrored. ``approval`` is the legacy adapter-emitted alias of
# ``requires_approval`` (a pre-side-effect awaiting-decision state) — cancellable for the same
# reason, and carried here until the alias is normalized away (#1659).
_CANCELLABLE = {"pending", "requires_approval", "pending_approval", "approval"}
# Non-terminal statuses that are nonetheless NOT cancellable because irreversible external
# work has committed (``approved``) or is underway (``in_progress``).
_NONCANCELLABLE_NONTERMINAL = {"in_progress", "approved"}
_TERMINAL = {"completed", "rejected", "failed", "canceled"}
# Statuses an approve/reject compare-and-set may fire FROM (still awaiting a decision). MUST
# exclude ``approved`` so a second approver can't win approved→approved and duplicate the
# irreversible adapter work, and so a reject can't run approved→rejected on a live order.
# ``approval`` is the legacy adapter-emitted alias of ``requires_approval`` (base_workflow /
# GAM / Broadstreet) — it MUST be approvable or those live human workflows can't be actioned.
_APPROVABLE = {"requires_approval", "pending_approval", "approval"}


def test_cancellable_set_is_exactly_the_pre_sideeffect_statuses():
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


def test_approvable_set_is_exactly_the_pending_decision_statuses():
    assert APPROVABLE_STEP_STATUSES == _APPROVABLE


def test_approved_is_not_approvable():
    # The crux of the double-approve fix: an already-approved step is NOT a valid source for
    # another approve/reject compare-and-set.
    assert "approved" not in APPROVABLE_STEP_STATUSES
    for status in _TERMINAL | {"in_progress", "pending"}:
        assert status not in APPROVABLE_STEP_STATUSES, f"{status!r} must not be approvable"
