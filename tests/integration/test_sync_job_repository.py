"""Integration tests for SyncJobRepository against real PostgreSQL.

The subject is a repository contract -- which job is a tenant's latest of one type -- which
no wire carries, so BDD cannot reach it. Each test seeds a newer job the read must not
return, so dropping the tenant, type or status filter, or the ordering, fails one of them.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.core.database.repositories.sync_job import SyncJobRepository
from tests.factories import SyncJobFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestLatest:
    def test_the_tenants_newest_job_of_the_type_comes_back(self, factory_session):
        tenant = TenantFactory()
        now = datetime.now(UTC)
        SyncJobFactory(tenant=tenant, started_at=now - timedelta(hours=2))
        newest = SyncJobFactory(tenant=tenant, started_at=now - timedelta(hours=1))
        SyncJobFactory(tenant=tenant, sync_type="order_approval", started_at=now)  # another type
        SyncJobFactory(started_at=now)  # another tenant's newer job

        assert SyncJobRepository(factory_session, tenant.tenant_id).latest("inventory").sync_id == newest.sync_id

    def test_status_narrows_it_to_one_status(self, factory_session):
        tenant = TenantFactory()
        now = datetime.now(UTC)
        completed = SyncJobFactory(tenant=tenant, status="completed", started_at=now - timedelta(hours=1))
        SyncJobFactory(tenant=tenant, status="running", started_at=now)
        repo = SyncJobRepository(factory_session, tenant.tenant_id)

        assert repo.latest("inventory", status="completed").sync_id == completed.sync_id
        assert repo.latest("inventory", status="failed") is None

    def test_a_tenant_with_no_job_has_none(self, factory_session):
        SyncJobFactory()  # another tenant's

        assert SyncJobRepository(factory_session, TenantFactory().tenant_id).latest("inventory") is None
