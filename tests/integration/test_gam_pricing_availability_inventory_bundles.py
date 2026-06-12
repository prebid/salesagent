"""Pricing/availability sync coverage for inventory-bundle wholesale products."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.product import ProductRepository
from src.services.gam_pricing_availability_sync import _sync_product_guidance
from tests.factories import (
    AdapterConfigFactory,
    InventoryProfileFactory,
    PricingOptionFactory,
    ProductFactory,
    TenantFactory,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _line_item(*, placement_id: str = "", ad_unit_id: str = "", impressions: int, cpm: float) -> dict:
    return {
        "placement_id": placement_id,
        "placement_name": f"Placement {placement_id}",
        "ad_unit_id": ad_unit_id,
        "ad_unit_name": f"Ad unit {ad_unit_id}" if ad_unit_id else "",
        "country_code": "US",
        "country": "United States",
        "line_item_id": f"li_{placement_id or ad_unit_id}",
        "line_item_name": "Line item",
        "line_item_type": "PRICE_PRIORITY",
        "impressions": impressions,
        "viewable_impressions": 0,
        "measurable_impressions": 0,
        "clicks": 0,
        "completed_views": 0,
        "revenue": round(impressions / 1000 * cpm, 2),
        "cpm": cpm,
        "vcpm": None,
        "cpc": None,
        "cpcv": None,
    }


def test_pricing_availability_sync_updates_inventory_bundle_analytics_without_product_row(factory_session):
    tenant = TenantFactory(tenant_id="bundle-forecast", subdomain="bundle-forecast")
    AdapterConfigFactory(
        tenant=tenant, tenant_id=tenant.tenant_id, adapter_type="google_ad_manager", gam_network_currency="USD"
    )
    InventoryProfileFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        profile_id="homepage_bundle",
        name="Homepage Bundle",
        inventory_config={"ad_units": [], "placements": ["pl_home"], "include_descendants": False},
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        publisher_properties=[
            {
                "publisher_domain": "bundle-forecast.example.com",
                "property_ids": ["homepage"],
                "selection_type": "by_id",
            }
        ],
    )

    reporting = MagicMock()
    reporting.network_timezone = "UTC"
    reporting.get_line_item_capacity_guidance.return_value = {"minimum_package_budget": 10.0}
    reporting.get_placement_country_price_guidance.return_value = {
        "possibly_truncated": False,
        "date_range": "this_month",
        "window_start": "2026-05-01",
        "window_end": "2026-05-30",
        "filters": {"line_item_types": ["PRICE_PRIORITY"]},
        "line_item_rows": [_line_item(placement_id="pl_home", impressions=40_000, cpm=2.0)],
        "raw_rows": 1,
        "eligible_line_item_rows": 1,
    }

    updated_product_ids, counts, errors = _sync_product_guidance(
        tenant_id=tenant.tenant_id,
        reporting=reporting,
        date_range="this_month",
        line_item_types=["PRICE_PRIORITY"],
        min_group_impressions=1,
        min_line_item_impressions=1,
        bookability_safety_factor=1.0,
        max_network_line_items=600_000,
        monthly_line_item_space_fraction=0.01,
        estimated_line_items_per_package=1,
    )

    factory_session.expire_all()
    product = ProductRepository(factory_session, tenant.tenant_id).get_by_id_with_pricing("homepage_bundle")
    profile = InventoryProfileRepository(factory_session, tenant.tenant_id).get_by_id("homepage_bundle")

    assert errors == {}
    assert counts["products_seen"] == 1
    assert counts["products_with_placements"] == 1
    assert updated_product_ids == ["homepage_bundle"]
    assert product is None
    assert profile is not None
    assert profile.forecast["points"][0]["product_id"] == "homepage_bundle"
    assert profile.forecast["points"][0]["metrics"]["impressions"]["mid"] == 40_000.0
    assert profile.pricing_availability["pricing_guidance_by_model"]["cpm"]["p25"] == 2.0


def test_pricing_availability_sync_updates_ad_unit_only_product(factory_session):
    tenant = TenantFactory(tenant_id="ad-unit-forecast", subdomain="ad-unit-forecast")
    AdapterConfigFactory(
        tenant=tenant, tenant_id=tenant.tenant_id, adapter_type="google_ad_manager", gam_network_currency="USD"
    )
    product = ProductFactory(
        tenant=tenant,
        tenant_id=tenant.tenant_id,
        product_id="homepage_ad_unit",
        implementation_config={"targeted_ad_unit_ids": ["987"]},
        delivery_measurement={"provider": "google_ad_manager"},
    )
    PricingOptionFactory(
        product=product,
        tenant_id=tenant.tenant_id,
        product_id=product.product_id,
        pricing_model="cpm",
        rate=None,
        currency="USD",
        is_fixed=False,
        price_guidance={},
    )

    reporting = MagicMock()
    reporting.network_timezone = "UTC"
    reporting.get_line_item_capacity_guidance.return_value = {"minimum_package_budget": 10.0}
    reporting.get_placement_country_price_guidance.return_value = {
        "possibly_truncated": False,
        "date_range": "this_month",
        "window_start": "2026-05-01",
        "window_end": "2026-05-30",
        "filters": {"line_item_types": ["PRICE_PRIORITY"]},
        "line_item_rows": [_line_item(ad_unit_id="987", impressions=50_000, cpm=3.0)],
        "raw_rows": 1,
        "eligible_line_item_rows": 1,
    }

    updated_product_ids, counts, errors = _sync_product_guidance(
        tenant_id=tenant.tenant_id,
        reporting=reporting,
        date_range="this_month",
        line_item_types=["PRICE_PRIORITY"],
        min_group_impressions=1,
        min_line_item_impressions=1,
        bookability_safety_factor=1.0,
        max_network_line_items=600_000,
        monthly_line_item_space_fraction=0.01,
        estimated_line_items_per_package=1,
    )

    factory_session.expire_all()
    refreshed_product = ProductRepository(factory_session, tenant.tenant_id).get_by_id_with_pricing("homepage_ad_unit")

    assert errors == {}
    assert counts["products_seen"] == 1
    assert counts["products_with_placements"] == 0
    assert counts["products_with_inventory_targets"] == 1
    assert counts["placement_ids_queried"] == 0
    assert counts["ad_unit_ids_queried"] == 1
    assert updated_product_ids == ["homepage_ad_unit"]
    reporting.get_placement_country_price_guidance.assert_called_once()
    assert reporting.get_placement_country_price_guidance.call_args.kwargs["placement_ids"] == []
    assert reporting.get_placement_country_price_guidance.call_args.kwargs["ad_unit_ids"] == ["987"]
    assert refreshed_product is not None
    assert refreshed_product.forecast["points"][0]["product_id"] == "homepage_ad_unit"
    assert refreshed_product.forecast["points"][0]["metrics"]["impressions"]["mid"] == 50_000.0
    assert refreshed_product.pricing_options[0].price_guidance == {
        "floor": 3.0,
        "p25": 3.0,
        "p50": 3.0,
        "p75": 3.0,
        "p90": 3.0,
    }
