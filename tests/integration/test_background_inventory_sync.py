"""An incremental inventory sync rewrites the custom targeting keys a tenant holds in place.

``_run_sync_thread`` is the background sync the admin UI starts. The only stand-in is
Google's SOAP client (``stub_gam_client``), the one patch target ``tests/CLAUDE.md``
allows here: the discovery, the writers and the database are production's. BDD cannot
reach this, because the sync runs on a thread the admin route starts and answers with
nothing but a sync id, and no wire carries what it writes.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from src.core.database.repositories.gam_inventory import GAMInventoryRepository
from src.services.background_sync_service import _run_sync_thread
from tests.factories import AdapterConfigFactory, GAMInventoryFactory, SyncJobFactory, TenantFactory
from tests.harness.gam import GAM_CLIENT_PATCH
from tests.helpers.gam_client import SoapObject, stub_gam_client

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _gam_keys(**names: str) -> list[SoapObject]:
    """The custom targeting keys GAM holds, by id: ``_gam_keys(**{"11": "genre"})``."""
    return [
        SoapObject(id=int(key_id), name=name, displayName=name.title(), type="PREDEFINED", status="ACTIVE")
        for key_id, name in names.items()
    ]


def _first_page(results: list[SoapObject]):
    """A ``get*ByStatement`` stand-in answering *results* on the first page and none after it."""
    return lambda statement: SoapObject(results=results if statement["query"].endswith("OFFSET 0") else [])


def test_incremental_sync_updates_held_keys_in_place_and_maps_every_synced_key(factory_session):
    tenant = TenantFactory()
    config = AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456",
        gam_refresh_token="test_refresh_token",
        custom_targeting_keys={"genre": "11"},
    )
    # A completed sync keeps the run incremental: a full one deletes the held rows first.
    SyncJobFactory(tenant=tenant, status="completed", started_at=datetime.now(UTC) - timedelta(days=1))
    job = SyncJobFactory(tenant=tenant)
    genre = GAMInventoryFactory(tenant=tenant, inventory_type="custom_targeting_key", inventory_id="11", name="genre")
    section = GAMInventoryFactory(
        tenant=tenant, inventory_type="custom_targeting_key", inventory_id="12", name="section"
    )
    client = stub_gam_client()
    client.GetService("CustomTargetingService").getCustomTargetingKeysByStatement.side_effect = _first_page(
        _gam_keys(**{"11": "genre_v2", "12": "section", "13": "mood"})
    )

    with patch(GAM_CLIENT_PATCH["gam_client"], return_value=client):
        _run_sync_thread(tenant.tenant_id, job.sync_id, "incremental", None, None, None)

    factory_session.refresh(job)
    assert (job.status, job.error_message) == ("completed", None)
    factory_session.refresh(genre)
    factory_session.refresh(section)
    assert (genre.name, section.name) == ("genre_v2", "section")  # the rows they were, renamed where GAM renamed
    synced = {"genre_v2": "11", "section": "12", "mood": "13"}
    assert GAMInventoryRepository(factory_session, tenant.tenant_id).inventory_ids_by_name("custom_targeting_key") == (
        synced
    )
    factory_session.refresh(config)
    assert config.custom_targeting_keys == synced
