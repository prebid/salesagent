"""BDD scenario binding for UC-030: Manage Governance Binding (sync_governance).

Binds the compiled BR-UC-030 feature via pytest-bdd's ``scenarios()``. The
in-scope ``@sync`` scenarios execute against GovernanceSyncEnv across a2a/mcp/rest
(wired in tests/bdd/conftest.py); the ``@check`` scenarios (check_governance —
an undeclared capability) and the deferred/idempotency/boundary scenarios are
routed to ``_UC030_XFAIL_TAGS`` with documented reasons. Step definitions come
from ``tests.bdd.steps.domain.uc030_governance`` (+ shared generic steps).

beads: #1329 (UC-030)
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-030-manage-governance-binding.feature")
