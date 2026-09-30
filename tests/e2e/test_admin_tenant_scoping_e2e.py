"""E2E transport for the tenant-scoped admin route tests of #2203.

The contract classes live in tests/helpers/admin_tenant_scoping_contract.py. Importing them
here collects them against the ``scoping_env`` below, which drives the same harness through
``requests.Session`` against nginx -> FastAPI -> admin blueprints.

Requires: ADCP_SALES_PORT set (Docker stack running). The stack runs with
ADCP_AUTH_TEST_MODE=true; the harness logs in through ``/test/auth`` as the non-admin
``test_tenant_user`` identity against a tenant that is never the target, which keeps the
test-mode bypass out of every membership decision.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import admin_stack_env
from tests.harness.admin_tenant_scoping import AdminTenantScopingEnv

# Collected here, not just imported: pytest picks up every Test* class in the module namespace.
from tests.helpers.admin_tenant_scoping_contract import (  # noqa: F401
    TestActiveMemberUnchanged,
    TestAnonymousDenied,
    TestNonMemberDenied,
)


@pytest.fixture()
def scoping_env(docker_services_e2e):
    """``AdminTenantScopingEnv`` in e2e mode, with the target tenant already seeded."""
    with admin_stack_env(
        docker_services_e2e, lambda base_url: AdminTenantScopingEnv(mode="e2e", base_url=base_url)
    ) as env:
        env.seed_target_tenant()
        yield env
