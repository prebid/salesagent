"""Integration tests for GAMInventoryRepository against real PostgreSQL.

The subject is a repository contract -- tenant scoping, the key a sync matches a synced
item by, the counts the admin inventory views show -- which no wire carries, so BDD
cannot reach it. The last class grades the one behaviour the sync builds on that key:
a re-sync updates the items it already holds instead of inserting them again.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.core.database.repositories.gam_inventory import GAMInventoryRepository
from tests.factories import GAMInventoryFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestGAMInventoryRepository:
    def test_row_ids_key_each_item_by_type_and_inventory_id(self, factory_session):
        tenant = TenantFactory()
        unit = GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", inventory_id="1")
        label = GAMInventoryFactory(tenant=tenant, inventory_type="label", inventory_id="1")
        GAMInventoryFactory(inventory_type="ad_unit", inventory_id="2")  # another tenant's
        repo = GAMInventoryRepository(factory_session, tenant.tenant_id)

        assert repo.row_ids() == {("ad_unit", "1"): unit.id, ("label", "1"): label.id}
        assert repo.row_ids("label") == {("label", "1"): label.id}

    def test_inventory_ids_by_name_read_one_type(self, factory_session):
        tenant = TenantFactory()
        GAMInventoryFactory(tenant=tenant, inventory_type="custom_targeting_key", inventory_id="11", name="genre")
        GAMInventoryFactory(tenant=tenant, inventory_type="custom_targeting_key", inventory_id="12", name="section")
        GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", inventory_id="13", name="genre")
        GAMInventoryFactory(inventory_type="custom_targeting_key", inventory_id="14", name="mood")  # another tenant's

        assert GAMInventoryRepository(factory_session, tenant.tenant_id).inventory_ids_by_name(
            "custom_targeting_key"
        ) == {"genre": "11", "section": "12"}

    def test_counts_by_type_and_status(self, factory_session):
        tenant = TenantFactory()
        for inventory_type, status in [
            ("placement", "ACTIVE"),
            ("placement", "ACTIVE"),
            ("placement", "INACTIVE"),
            ("label", "ACTIVE"),
        ]:
            GAMInventoryFactory(tenant=tenant, inventory_type=inventory_type, status=status)
        GAMInventoryFactory(inventory_type="label", status="ACTIVE")  # another tenant's

        assert GAMInventoryRepository(factory_session, tenant.tenant_id).counts_by_type_and_status() == {
            "placement": {"ACTIVE": 2, "INACTIVE": 1},
            "label": {"ACTIVE": 1},
        }

    def test_last_synced_at_is_the_latest_sync(self, factory_session):
        tenant = TenantFactory()
        latest = datetime.now(UTC).replace(microsecond=0)
        GAMInventoryFactory(tenant=tenant, last_synced=latest - timedelta(days=1))
        GAMInventoryFactory(tenant=tenant, last_synced=latest)
        GAMInventoryFactory(last_synced=latest + timedelta(days=1))  # another tenant's

        assert GAMInventoryRepository(factory_session, tenant.tenant_id).last_synced_at() == latest
        assert GAMInventoryRepository(factory_session, TenantFactory().tenant_id).last_synced_at() is None


class TestResyncUpdatesWhatItHolds:
    """A sync reads ``row_ids`` first, so an item it already holds is updated in place."""

    def test_write_inventory_batch_updates_a_held_item_and_inserts_a_new_one(self, factory_session):
        from src.services.gam_inventory_service import GAMInventoryService

        tenant = TenantFactory()
        held_id = GAMInventoryFactory(tenant=tenant, inventory_type="label", inventory_id="7", name="Old name").id
        label_fields = {"description": None, "ad_category": None, "label_type": None, "is_active": True}
        items = [
            SimpleNamespace(id="7", name="New name", **label_fields),
            SimpleNamespace(id="8", name="Fresh", **label_fields),
        ]

        GAMInventoryService(factory_session)._write_inventory_batch(tenant.tenant_id, "label", items, datetime.now(UTC))

        repo = GAMInventoryRepository(factory_session, tenant.tenant_id)
        assert repo.inventory_ids_by_name("label") == {"New name": "7", "Fresh": "8"}
        assert repo.row_ids("label")[("label", "7")] == held_id
