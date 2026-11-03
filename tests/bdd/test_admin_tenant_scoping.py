"""BDD scenario binding for BR-ADMIN-TENANT-SCOPING: tenant-scoped admin routes (#2203).

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions from the
hand-authored admin feature file. Step definitions are in
tests/bdd/steps/domain/admin_tenant_scoping.py (imported via conftest.py).

"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-TENANT-SCOPING.feature")
