from datetime import datetime

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob, Tenant


@pytest.mark.requires_db
def test_sync_job_id_length(integration_db):
    """Test that SyncJob accepts IDs longer than 50 characters (up to 100)."""
    with get_db_session() as session:
        # Create a tenant first (FK dependency)
        tenant = Tenant(
            tenant_id="tenant_1", name="Test Tenant", subdomain="test-tenant", virtual_host="test.example.com"
        )
        session.add(tenant)
        session.commit()

        # Create SyncJob with long ID
        # sync_id length = 5 + 36 + 1 + 10 = 52 chars is what failed
        # Let's test with 60 chars
        long_sync_id = "sync_" + "a" * 55
        assert len(long_sync_id) == 60

        sync_job = SyncJob(
            sync_id=long_sync_id,
            tenant_id="tenant_1",
            adapter_type="google_ad_manager",
            sync_type="inventory",
            status="running",
            started_at=datetime.now(),
            triggered_by="test",
        )
        session.add(sync_job)
        session.commit()

        # Verify it was saved
        stmt = select(SyncJob).filter_by(sync_id=long_sync_id)
        saved_job = session.scalars(stmt).first()
        assert saved_job is not None
        assert saved_job.sync_id == long_sync_id
