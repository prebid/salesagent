"""E2E transport for the tenant-scoped admin route tests of #2203.

The contract lives in tests/admin/test_tenant_scoped_routes_auth.py. Importing its three
classes here collects them a second time against the ``scoping_env`` below, which drives the
same harness through ``requests.Session`` against nginx -> FastAPI -> admin blueprints, as
tests/e2e/test_admin_bdd_e2e.py does for BR-ADMIN-ACCOUNTS.

Requires: ADCP_SALES_PORT set (Docker stack running). The stack runs with
ADCP_AUTH_TEST_MODE=true; the harness logs in through ``/test/auth`` as the non-admin
``test_tenant_user`` identity against a tenant that is never the target, which keeps the
test-mode bypass out of every membership decision.
"""

from __future__ import annotations

import pytest

# Collected here, not just imported: pytest picks up every Test* class in the module namespace.
from tests.admin.test_tenant_scoped_routes_auth import (  # noqa: F401
    TestActiveMemberUnchanged,
    TestAnonymousDenied,
    TestNonMemberDenied,
)
from tests.e2e.conftest import admin_stack_env
from tests.harness.admin_tenant_scoping import AdminTenantScopingEnv


@pytest.fixture()
def scoping_env(docker_services_e2e):
    """``AdminTenantScopingEnv`` in e2e mode, with the target tenant already seeded."""
    with admin_stack_env(docker_services_e2e, lambda: AdminTenantScopingEnv(mode="e2e")) as env:
        env.seed_target_tenant()
        yield env
