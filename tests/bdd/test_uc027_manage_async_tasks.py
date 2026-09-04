"""BDD scenarios for UC-027: manage async tasks (get_task / complete_task).

Binds BR-UC-027 (whole feature; unwired scenarios xfail at the harness fixture)
and the locally-added sibling-principal isolation feature (#1812 review).

Step definitions live in ``tests.bdd.steps.domain.uc027_manage_async_tasks``
(registered via ``pytest_plugins``) so the BDD step-scanning guards see them.
"""

from __future__ import annotations

from pytest_bdd import scenarios

# Whole-feature binding is the repo convention the CI shard-splitter requires.
scenarios("features/BR-UC-027-manage-async-tasks.feature")
scenarios("features/local-uc027-sibling-principal-isolation.feature")
