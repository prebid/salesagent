"""BDD scenario binding for UC-011: Manage Accounts.

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions
from the compiled feature file. Step definitions are imported via conftest.py.

All scenarios are xfail until production code is implemented:
- Account ORM models + migrations
- AccountRepository + AccountUoW
- _sync_accounts_impl + _list_accounts_impl
- MCP + A2A wrappers
- AccountSyncEnv + AccountListEnv harness environments
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-011-manage-accounts.feature")
