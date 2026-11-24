"""BDD scenario binding for BR-ADMIN-SUPER-ADMIN.

The all-tenants index, driven through ``AdminAccountEnv`` on both admin transports. Step
definitions are in tests/bdd/steps/domain/admin_super_admin.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-SUPER-ADMIN.feature")
