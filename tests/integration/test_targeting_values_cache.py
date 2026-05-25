"""Integration tests for cached custom_targeting_value reads (#479).

Covers:
- ``GAMSyncRepository.list_values_for_key`` returns rows tagged with the
  given ``custom_targeting_key_id``
- ``_cached_targeting_values`` projects rows onto the wire shape
- ``_upsert_targeting_value_row`` inserts a new row and updates existing
- Inserted values are tenant-scoped (one tenant's cache can't read another's)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _TargetingCacheEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


@dataclass
class _FakeGamValue:
    """Minimal stand-in for a discover_custom_targeting_values_for_key row."""

    id: str
    name: str
    display_name: str = ""
    match_type: str = "EXACT"
    status: str = "ACTIVE"


class TestRepositoryListValuesForKey:
    def test_returns_values_filtered_by_key_id(self, integration_db):
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import GAMInventoryFactory, TenantFactory

        with _TargetingCacheEnv() as env:
            tenant = TenantFactory(tenant_id="cache_t1")
            GAMInventoryFactory(
                tenant=tenant,
                inventory_type="custom_targeting_value",
                inventory_id="v1",
                name="sports",
                inventory_metadata={"custom_targeting_key_id": "100", "display_name": "Sports", "match_type": "EXACT"},
            )
            GAMInventoryFactory(
                tenant=tenant,
                inventory_type="custom_targeting_value",
                inventory_id="v2",
                name="news",
                inventory_metadata={"custom_targeting_key_id": "100", "display_name": "News", "match_type": "EXACT"},
            )
            GAMInventoryFactory(
                tenant=tenant,
                inventory_type="custom_targeting_value",
                inventory_id="v3",
                name="other",
                inventory_metadata={"custom_targeting_key_id": "200", "display_name": "Other", "match_type": "EXACT"},
            )
            session = env.get_session()
            rows = GAMSyncRepository(session, tenant.tenant_id).list_values_for_key("100")

        assert sorted(r.inventory_id for r in rows) == ["v1", "v2"]

    def test_empty_when_key_has_no_cached_values(self, integration_db):
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import TenantFactory

        with _TargetingCacheEnv() as env:
            tenant = TenantFactory(tenant_id="cache_t2")
            session = env.get_session()
            rows = GAMSyncRepository(session, tenant.tenant_id).list_values_for_key("999")

        assert rows == []

    def test_isolates_by_tenant(self, integration_db):
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import GAMInventoryFactory, TenantFactory

        with _TargetingCacheEnv() as env:
            t_a = TenantFactory(tenant_id="cache_ta")
            t_b = TenantFactory(tenant_id="cache_tb")
            GAMInventoryFactory(
                tenant=t_a,
                inventory_type="custom_targeting_value",
                inventory_id="va",
                name="tenant_a_only",
                inventory_metadata={"custom_targeting_key_id": "100"},
            )
            session = env.get_session()
            rows_b = GAMSyncRepository(session, t_b.tenant_id).list_values_for_key("100")

        assert rows_b == []

    def test_batches_values_for_multiple_keys(self, integration_db):
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import GAMInventoryFactory, TenantFactory

        with _TargetingCacheEnv() as env:
            tenant = TenantFactory(tenant_id="cache_tmulti")
            other_tenant = TenantFactory(tenant_id="cache_tmulti_other")
            GAMInventoryFactory(
                tenant=tenant,
                inventory_type="custom_targeting_value",
                inventory_id="v1",
                name="sports",
                inventory_metadata={"custom_targeting_key_id": "100"},
            )
            GAMInventoryFactory(
                tenant=tenant,
                inventory_type="custom_targeting_value",
                inventory_id="v2",
                name="news",
                inventory_metadata={"custom_targeting_key_id": "200"},
            )
            GAMInventoryFactory(
                tenant=other_tenant,
                inventory_type="custom_targeting_value",
                inventory_id="other",
                name="other",
                inventory_metadata={"custom_targeting_key_id": "100"},
            )
            session = env.get_session()
            rows_by_key = GAMSyncRepository(session, tenant.tenant_id).list_values_for_keys({"100", "200", "999"})

        assert [r.inventory_id for r in rows_by_key["100"]] == ["v1"]
        assert [r.inventory_id for r in rows_by_key["200"]] == ["v2"]
        assert rows_by_key["999"] == []


class TestCachedTargetingValuesProjection:
    """``_cached_targeting_values`` shapes rows for the JSON response."""

    def test_returns_none_when_no_rows_cached(self, integration_db):
        from src.admin.blueprints.inventory import _cached_targeting_values
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import TenantFactory

        with _TargetingCacheEnv() as env:
            tenant = TenantFactory(tenant_id="proj_t1")
            session = env.get_session()
            repo = GAMSyncRepository(session, tenant.tenant_id)
            assert _cached_targeting_values(repo, "999", "genre") is None

    def test_projects_cached_rows_to_wire_dict(self, integration_db):
        from src.admin.blueprints.inventory import _cached_targeting_values
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import GAMInventoryFactory, TenantFactory

        with _TargetingCacheEnv() as env:
            tenant = TenantFactory(tenant_id="proj_t2")
            GAMInventoryFactory(
                tenant=tenant,
                inventory_type="custom_targeting_value",
                inventory_id="v1",
                name="sports",
                inventory_metadata={
                    "custom_targeting_key_id": "100",
                    "display_name": "Sports Fans",
                    "match_type": "BROAD",
                },
            )
            session = env.get_session()
            repo = GAMSyncRepository(session, tenant.tenant_id)
            values = _cached_targeting_values(repo, "100", "genre")

        assert values == [
            {
                "id": "v1",
                "name": "sports",
                "display_name": "Sports Fans",
                "match_type": "BROAD",
                "status": "ACTIVE",
                "key_id": "100",
                "key_name": "genre",
            }
        ]


class TestUpsertTargetingValueRow:
    """``_upsert_targeting_value_row`` inserts new + updates existing."""

    def test_inserts_new_row(self, integration_db):
        from src.admin.blueprints.inventory import _upsert_targeting_value_row
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import TenantFactory

        with _TargetingCacheEnv() as env:
            tenant = TenantFactory(tenant_id="ins_t1")
            session = env.get_session()
            repo = GAMSyncRepository(session, tenant.tenant_id)
            _upsert_targeting_value_row(
                repo,
                key_id="100",
                key_name="genre",
                key_display_name="Genre",
                value=_FakeGamValue(id="v1", name="sports", display_name="Sports", match_type="EXACT"),
                sync_time=datetime.now(UTC),
            )
            session.flush()
            row = repo.find_inventory_item("custom_targeting_value", "v1")

        assert row is not None
        assert row.inventory_metadata["custom_targeting_key_id"] == "100"
        assert row.inventory_metadata["display_name"] == "Sports"
        assert row.path == ["Genre", "Sports"]

    def test_updates_existing_row(self, integration_db):
        from src.admin.blueprints.inventory import _upsert_targeting_value_row
        from src.core.database.repositories.gam_sync import GAMSyncRepository
        from tests.factories import TenantFactory

        with _TargetingCacheEnv() as env:
            tenant = TenantFactory(tenant_id="ins_t2")
            session = env.get_session()
            repo = GAMSyncRepository(session, tenant.tenant_id)
            sync1 = datetime.now(UTC)
            _upsert_targeting_value_row(
                repo,
                key_id="100",
                key_name="genre",
                key_display_name="Genre",
                value=_FakeGamValue(id="v1", name="sports", display_name="Sports"),
                sync_time=sync1,
            )
            session.flush()
            _upsert_targeting_value_row(
                repo,
                key_id="100",
                key_name="genre",
                key_display_name="Genre",
                value=_FakeGamValue(id="v1", name="sports", display_name="Sports Fans (updated)"),
                sync_time=datetime.now(UTC),
            )
            session.flush()
            rows = repo.list_values_for_key("100")

        assert len(rows) == 1
        assert rows[0].inventory_metadata["display_name"] == "Sports Fans (updated)"
