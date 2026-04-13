"""BDD scenario binding for BR-ADMIN-ACCOUNTS: Admin Account Management.

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions from the
hand-authored admin feature file. Step definitions are in
tests/bdd/steps/domain/admin_accounts.py (imported via conftest.py).

beads: salesagent-oj0.1.2
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-ACCOUNTS.feature")
