"""BDD scenario binding for BR-ADMIN-SIGNING-KEYS (#2288).

The signing-keys page and the setup checklist, driven through ``AdminSigningKeysEnv`` on
both admin transports. Step definitions are in tests/bdd/steps/domain/admin_signing_keys.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-SIGNING-KEYS.feature")
