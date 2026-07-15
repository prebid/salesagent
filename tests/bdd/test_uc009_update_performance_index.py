"""BDD scenario binding for UC-009: Update Performance Index.

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions from the
generated feature file. Step definitions are imported via conftest.py.

Wired set (salesagent-cmjm / salesagent-8wf2): the five @T-UC-009-main-*
scenarios run a real update_performance_index through every transport on
PerformanceEnv. Every other scenario stays dormant via the UC-009 fixture
catch-all xfail.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-009-update-performance-index.feature")
