"""A GAM sync's discovery is saved as one inventory row per item, scoped to its tenant.

``GAMSyncManager`` saves what an inventory or selective sync discovered here. A second
save of the same discovery updates the rows it wrote instead of adding new ones, an
item the save no longer names is marked stale (ad units excepted), and another tenant's
rows are never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.adapters.gam_inventory_discovery import (
    AdUnit,
    AdUnitStatus,
    AudienceSegment,
    CustomTargetingKey,
    CustomTargetingValue,
    GAMInventoryDiscovery,
    Label,
    Placement,
)
from src.core.database.models import GAMInventory
from src.core.database.repositories.uow import GAMInventoryUoW
from tests.factories import GAMInventoryFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT = "test-gam-inventory-save"
OTHER_TENANT = "test-gam-inventory-save-other"


def _discovery(ad_unit_name: str) -> GAMInventoryDiscovery:
    """One item of each type a sync saves, with the ad unit named *ad_unit_name*."""
    discovery = GAMInventoryDiscovery(None, TENANT)
    discovery.ad_units = {
        "au_1": AdUnit(
            id="au_1",
            name=ad_unit_name,
            ad_unit_code="home_top",
            parent_id="root",
            status=AdUnitStatus.ACTIVE,
            description="Top of the home page",
            target_window="BLANK",
            effective_applied_labels=["lb_1"],
            explicitly_targeted=False,
            has_children=False,
            path=["root", ad_unit_name],
            sizes=[{"width": 728, "height": 90}],
        )
    }
    discovery.placements = {
        "pl_1": Placement(
            id="pl_1",
            name="Homepage",
            description=None,
            placement_code="hp",
            status="ACTIVE",
            is_ad_sense_targeting_enabled=False,
            ad_unit_ids=["au_1"],
            targeting_description=None,
        )
    }
    discovery.labels = {
        "lb_1": Label(
            id="lb_1", name="No alcohol", description=None, is_active=False, ad_category=None, label_type="COMPETITIVE"
        )
    }
    discovery.custom_targeting_keys = {
        "k_1": CustomTargetingKey(
            id="k_1", name="genre", display_name="Genre", type="PREDEFINED", status="ACTIVE", reportable_type="ON"
        )
    }
    discovery.custom_targeting_values = {
        "k_1": [
            CustomTargetingValue(
                id="v_1",
                custom_targeting_key_id="k_1",
                name="sports",
                display_name="Sports",
                match_type="EXACT",
                status="ACTIVE",
            )
        ]
    }
    discovery.audience_segments = {
        "seg_1": AudienceSegment(
            id="seg_1",
            name="Sports fans",
            description=None,
            category_ids=["c_1"],
            type="FIRST_PARTY",
            size=1200,
            data_provider_name=None,
            status="ACTIVE",
            segment_type="RULE_BASED",
        )
    }
    return discovery


def _save(discovery: GAMInventoryDiscovery) -> None:
    """Save *discovery* as the sync does (``GAMSyncManager._save_inventory``)."""
    with GAMInventoryUoW(TENANT) as uow:
        assert uow.inventory is not None
        uow.inventory.save_discovery(discovery)


def _rows(env: IntegrationEnv, tenant_id: str) -> dict[tuple[str, str], GAMInventory]:
    env._session.expire_all()
    rows = env._session.scalars(select(GAMInventory).filter_by(tenant_id=tenant_id)).all()
    return {(row.inventory_type, row.inventory_id): row for row in rows}


def test_a_saved_discovery_is_one_row_per_item_of_its_tenant_and_a_resave_updates_them(integration_db):
    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=TENANT, subdomain="gam-inventory-save")
        other = TenantFactory(tenant_id=OTHER_TENANT, subdomain="gam-inventory-save-other")
        long_ago = datetime.now(UTC) - timedelta(days=2)
        # Rows of this tenant the discovery no longer names.
        GAMInventoryFactory(tenant=tenant, inventory_type="placement", inventory_id="pl_gone", last_synced=long_ago)
        GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", inventory_id="au_gone", last_synced=long_ago)
        # Another tenant's row with an id the discovery names.
        GAMInventoryFactory(tenant=other, inventory_type="ad_unit", inventory_id="au_1", name="Theirs")
        env._commit_factory_data()

        _save(_discovery("Leaderboard"))
        first = _rows(env, TENANT)

        assert sorted(first) == [
            ("ad_unit", "au_1"),
            ("ad_unit", "au_gone"),
            ("audience_segment", "seg_1"),
            ("custom_targeting_key", "k_1"),
            ("custom_targeting_value", "v_1"),
            ("label", "lb_1"),
            ("placement", "pl_1"),
            ("placement", "pl_gone"),
        ]
        ad_unit = first[("ad_unit", "au_1")]
        assert (ad_unit.name, ad_unit.path, ad_unit.status) == ("Leaderboard", ["root", "Leaderboard"], "ACTIVE")
        assert ad_unit.inventory_metadata == {
            "ad_unit_code": "home_top",
            "parent_id": "root",
            "description": "Top of the home page",
            "target_window": "BLANK",
            "explicitly_targeted": False,
            "has_children": False,
            "sizes": [{"width": 728, "height": 90}],
            "effective_applied_labels": ["lb_1"],
        }
        placement = first[("placement", "pl_1")]
        assert (placement.name, placement.path, placement.status) == ("Homepage", ["Homepage"], "ACTIVE")
        assert placement.inventory_metadata == {
            "placement_code": "hp",
            "description": None,
            "is_ad_sense_targeting_enabled": False,
            "ad_unit_ids": ["au_1"],
            "targeting_description": None,
        }
        label = first[("label", "lb_1")]
        assert (label.path, label.status) == (["No alcohol"], "INACTIVE")
        assert label.inventory_metadata == {"description": None, "ad_category": None, "label_type": "COMPETITIVE"}
        key = first[("custom_targeting_key", "k_1")]
        assert (key.name, key.path, key.status) == ("genre", ["Genre"], "ACTIVE")
        assert key.inventory_metadata == {"display_name": "Genre", "type": "PREDEFINED", "reportable_type": "ON"}
        value = first[("custom_targeting_value", "v_1")]
        assert (value.name, value.path, value.status) == ("sports", ["Genre", "Sports"], "ACTIVE")
        assert value.inventory_metadata == {
            "custom_targeting_key_id": "k_1",
            "display_name": "Sports",
            "match_type": "EXACT",
            "key_name": "genre",
            "key_display_name": "Genre",
        }
        segment = first[("audience_segment", "seg_1")]
        assert (segment.path, segment.status) == (["FIRST_PARTY", "Sports fans"], "ACTIVE")
        assert segment.inventory_metadata == {
            "description": None,
            "category_ids": ["c_1"],
            "type": "FIRST_PARTY",
            "size": 1200,
            "data_provider_name": None,
            "segment_type": "RULE_BASED",
        }
        # A placement the discovery no longer names is stale; an ad unit stays as it was.
        assert first[("placement", "pl_gone")].status == "STALE"
        assert first[("ad_unit", "au_gone")].status == "ACTIVE"

        _save(_discovery("Leaderboard, renamed"))
        second = _rows(env, TENANT)

        assert sorted(second) == sorted(first)
        assert {k: row.id for k, row in second.items()} == {k: row.id for k, row in first.items()}
        assert second[("ad_unit", "au_1")].name == "Leaderboard, renamed"
        theirs = _rows(env, OTHER_TENANT)
        assert list(theirs) == [("ad_unit", "au_1")]
        assert (theirs[("ad_unit", "au_1")].name, theirs[("ad_unit", "au_1")].status) == ("Theirs", "ACTIVE")
