"""BDD scenario binding for UC-002 account-access scoping (salesagent-ym1c).

Natural-key account resolution must be scoped to the requesting agent's
accessible accounts. See the feature file header for rationale.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-002-account-access.feature")
