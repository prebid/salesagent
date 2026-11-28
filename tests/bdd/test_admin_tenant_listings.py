"""BDD scenario binding for BR-ADMIN-TENANT-LISTINGS: the two listings of every tenant a deployment holds.

The all-tenants index and the tenant management API's list, driven through
``AdminTenantListingEnv`` on both admin transports. Step definitions are in
tests/bdd/steps/domain/admin_tenant_listings.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-TENANT-LISTINGS.feature")
