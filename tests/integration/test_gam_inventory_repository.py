"""Integration tests for GAMInventoryRepository against real PostgreSQL.

The subject is a repository contract -- tenant scoping, the key a sync writes a synced
item by, the counts the admin inventory views show -- which no wire carries, so BDD
cannot reach it. The last class grades the one behaviour the sync builds on that key:
a re-sync updates the items it already holds instead of inserting them again.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.gam_inventory_discovery import Label
from src.core.database.repositories.gam_inventory import GAMInventoryRepository
from tests.factories import GAMInventoryFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestGAMInventoryRepository:
    def test_inventory_ids_by_name_read_one_type(self, factory_session):
        tenant = TenantFactory()
        GAMInventoryFactory(tenant=tenant, inventory_type="custom_targeting_key", inventory_id="11", name="genre")
        GAMInventoryFactory(tenant=tenant, inventory_type="custom_targeting_key", inventory_id="12", name="section")
        GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", inventory_id="13", name="genre")
        GAMInventoryFactory(inventory_type="custom_targeting_key", inventory_id="14", name="mood")  # another tenant's

        assert GAMInventoryRepository(factory_session, tenant.tenant_id).inventory_ids_by_name(
            "custom_targeting_key"
        ) == {"genre": "11", "section": "12"}

    def test_counts_total_each_type_and_status(self, factory_session):
        tenant = TenantFactory()
        for inventory_type, status in [
            ("placement", "ACTIVE"),
            ("placement", "ACTIVE"),
            ("placement", "INACTIVE"),
            ("label", "ACTIVE"),
        ]:
            GAMInventoryFactory(tenant=tenant, inventory_type=inventory_type, status=status)
        GAMInventoryFactory(inventory_type="label", status="ACTIVE")  # another tenant's

        counts = GAMInventoryRepository(factory_session, tenant.tenant_id).counts()

        assert counts.by_type_and_status == {"placement": {"ACTIVE": 2, "INACTIVE": 1}, "label": {"ACTIVE": 1}}
        assert counts.by_type() == {"placement": 3, "label": 1}
        assert counts.total() == 4
        assert counts.total("placement") == 3
        assert counts.total(status="ACTIVE") == 3
        assert counts.total("placement", status="ACTIVE") == 2
        assert counts.total("ad_unit") == 0

    def test_last_synced_at_is_the_latest_sync(self, factory_session):
        tenant = TenantFactory()
        latest = datetime.now(UTC).replace(microsecond=0)
        GAMInventoryFactory(tenant=tenant, last_synced=latest - timedelta(days=1))
        GAMInventoryFactory(tenant=tenant, last_synced=latest)
        GAMInventoryFactory(last_synced=latest + timedelta(days=1))  # another tenant's

        assert GAMInventoryRepository(factory_session, tenant.tenant_id).last_synced_at() == latest
        assert GAMInventoryRepository(factory_session, TenantFactory().tenant_id).last_synced_at() is None

    def test_last_synced_at_narrows_to_one_type(self, factory_session):
        tenant = TenantFactory()
        latest = datetime.now(UTC).replace(microsecond=0)
        GAMInventoryFactory(tenant=tenant, inventory_type="label", last_synced=latest - timedelta(days=1))
        GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", last_synced=latest)
        GAMInventoryFactory(inventory_type="label", last_synced=latest)  # another tenant's
        repo = GAMInventoryRepository(factory_session, tenant.tenant_id)

        assert repo.last_synced_at("label") == latest - timedelta(days=1)
        assert repo.last_synced_at("placement") is None


def _label(label_id: str, name: str, *, is_active: bool = True, description: str | None = None) -> Label:
    """A label as discovery reads it from GAM."""
    return Label(
        id=label_id,
        name=name,
        description=description,
        is_active=is_active,
        ad_category=None,
        label_type="COMPETITIVE_EXCLUSION",
    )


def _label_rows(session, tenant_id: str, *labels: Label, synced_at: datetime | None = None) -> list[dict]:
    """The rows the sync writes for *labels*, built by the sync's own converter."""
    from src.services.gam_inventory_service import GAMInventoryService

    service = GAMInventoryService(session)
    return [
        service._convert_item_to_db_format(tenant_id, "label", label, synced_at or datetime.now(UTC))
        for label in labels
    ]


class TestResyncUpdatesWhatItHolds:
    """``upsert`` writes on ``uq_gam_inventory``, so an item the tenant holds is updated in place."""

    def test_upsert_updates_a_held_item_in_place_and_inserts_a_new_one(self, factory_session):
        tenant = TenantFactory()
        held = GAMInventoryFactory(tenant=tenant, inventory_type="label", inventory_id="7", name="Old name")
        unit = GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", inventory_id="7", name="Unit 7")
        theirs = GAMInventoryFactory(inventory_type="label", inventory_id="7", name="Theirs")  # another tenant's
        held_updated_at = held.updated_at
        synced_at = datetime.now(UTC).replace(microsecond=0)
        rows = _label_rows(
            factory_session,
            tenant.tenant_id,
            _label("7", "New name", is_active=False, description="renamed"),
            _label("8", "Fresh"),
            synced_at=synced_at,
        )
        repo = GAMInventoryRepository(factory_session, tenant.tenant_id)

        repo.upsert(rows)

        assert repo.inventory_ids_by_name("label") == {"New name": "7", "Fresh": "8"}
        factory_session.refresh(held)  # by the row id it was seeded with
        assert (held.name, held.status, held.last_synced) == ("New name", "INACTIVE", synced_at)
        assert held.inventory_metadata == {
            "description": "renamed",
            "ad_category": None,
            "label_type": "COMPETITIVE_EXCLUSION",
        }
        assert held.updated_at > held_updated_at
        factory_session.refresh(unit)
        factory_session.refresh(theirs)
        assert (unit.name, theirs.name) == ("Unit 7", "Theirs")

    def test_upsert_leaves_the_commit_to_its_caller(self, factory_session):
        tenant = TenantFactory()
        GAMInventoryFactory(tenant=tenant, inventory_type="label", inventory_id="7", name="Old name")
        repo = GAMInventoryRepository(factory_session, tenant.tenant_id)

        repo.upsert(_label_rows(factory_session, tenant.tenant_id, _label("7", "New name"), _label("8", "Fresh")))
        factory_session.rollback()

        assert repo.inventory_ids_by_name("label") == {"Old name": "7"}

    def test_upsert_writes_every_row_to_its_own_tenant(self, factory_session):
        tenant, other = TenantFactory(), TenantFactory()

        GAMInventoryRepository(factory_session, tenant.tenant_id).upsert(
            _label_rows(factory_session, other.tenant_id, _label("9", "Nine"))
        )

        assert GAMInventoryRepository(factory_session, tenant.tenant_id).inventory_ids_by_name("label") == {"Nine": "9"}
        assert GAMInventoryRepository(factory_session, other.tenant_id).inventory_ids_by_name("label") == {}
