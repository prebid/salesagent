"""Integration tests for salesagent-ul6n (review finding MED-02).

The harness must expose PUBLIC query methods so step functions stop reaching into
``env._session`` (a private attribute). These exercise the new public API on a real
IntegrationEnv against PostgreSQL.

Before the methods exist these fail with AttributeError; after, they pass.
"""

from __future__ import annotations

import pytest

from src.core.database.models import Tenant
from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv


@pytest.mark.requires_db
class TestIntegrationEnvPublicQuery:
    def test_get_session_returns_bound_session(self, integration_db):
        with IntegrationEnv(tenant_id="ul6n_t1", principal_id="ul6n_p1") as env:
            session = env.get_session()
            assert session is not None
            # It is the same session the factories are bound to.
            assert session is env._session

    def test_query_returns_matching_rows(self, integration_db):
        with IntegrationEnv(tenant_id="ul6n_t2", principal_id="ul6n_p2") as env:
            TenantFactory(tenant_id="ul6n_t2")
            rows = env.query(Tenant, tenant_id="ul6n_t2")
            assert [t.tenant_id for t in rows] == ["ul6n_t2"]

    def test_get_one_returns_single_or_none(self, integration_db):
        with IntegrationEnv(tenant_id="ul6n_t3", principal_id="ul6n_p3") as env:
            TenantFactory(tenant_id="ul6n_t3")
            found = env.get_one(Tenant, tenant_id="ul6n_t3")
            assert found is not None and found.tenant_id == "ul6n_t3"
            assert env.get_one(Tenant, tenant_id="ul6n_absent") is None

    def test_get_workflow_steps_returns_list_scoped_to_tenant(self, integration_db):
        with IntegrationEnv(tenant_id="ul6n_t4", principal_id="ul6n_p4") as env:
            steps = env.get_workflow_steps()
            assert steps == []  # fresh tenant has no workflow steps
