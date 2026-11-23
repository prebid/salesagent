"""BDD scenario binding for BR-ADMIN-WORKFLOW-STEP-REVIEW: the step review page (GHSA-2968-2px9-6rjm).

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions from the
hand-authored admin feature file. Step definitions are in
tests/bdd/steps/domain/admin_workflow_step_review.py (imported via conftest.py).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-WORKFLOW-STEP-REVIEW.feature")
