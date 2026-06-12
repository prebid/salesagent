"""Integration tests for embedded wholesale-product authoring APIs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from adcp.types import Error as AdCPResponseError

from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL
from src.core.database.models import AuthorizedProperty
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.services.aao_lookup_service import PublisherPartnerStatus
from tests.factories import (
    AdapterConfigFactory,
    AuthorizedPropertyFactory,
    GAMInventoryFactory,
    InventoryProfileFactory,
    PricingOptionFactory,
    ProductFactory,
    PublisherPartnerFactory,
    SpringServeInventoryFactory,
    TenantFactory,
)
from tests.helpers.managed_tenant_api import (
    bind_factories_to_session,
    configure_google_ad_manager_adapter,
    make_management_api_test_client,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-managed-tenant-wholesale-test-key"


@pytest.fixture
def management_api_client(integration_db):
    return make_management_api_test_client(API_KEY)


@pytest.fixture
def bound_factories(integration_db):
    with bind_factories_to_session() as session:
        session.info["management_api_caller"] = True
        yield session


@pytest.fixture(autouse=True)
def creative_format_catalog_unavailable():
    with patch("src.admin.blueprints.products.get_creative_formats", return_value=[]):
        yield


@pytest.fixture
def gam_tenant(bound_factories):
    tenant = TenantFactory(
        tenant_id="tenant_wholesale_gam",
        name="Wonderstruck",
        subdomain="wonderstruck",
        ad_server="google_ad_manager",
        is_embedded=True,
        public_agent_url="https://interchange.io",
    )
    configure_google_ad_manager_adapter(tenant)
    PublisherPartnerFactory(
        tenant=tenant,
        publisher_domain="wonderstruck.com",
        display_name="Wonderstruck",
        is_verified=True,
        sync_status="success",
    )
    AuthorizedPropertyFactory(
        tenant=tenant,
        property_id="wonderstruck_site",
        publisher_domain="wonderstruck.com",
        name="Wonderstruck Site",
        tags=["premium", "all_inventory"],
        verification_status="verified",
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="ad_unit",
        inventory_id="au_home",
        name="Homepage Ad Unit",
        path=["Wonderstruck", "Homepage"],
        inventory_metadata={"parent_id": None, "has_children": True, "sizes": [{"width": 970, "height": 250}]},
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="placement",
        inventory_id="pl_homepage_takeover",
        name="Homepage Takeover Placement",
        path=["Wonderstruck", "Homepage Takeover"],
        inventory_metadata={"parent_id": None, "targeted_ad_unit_ids": ["au_home"]},
    )
    return tenant


def _wholesale_payload(**overrides):
    payload = {
        "wholesale_product_id": "homepage_takeover",
        "name": "Homepage Takeover",
        "description": "High-impact homepage package.",
        "status": "active",
        "delivery_type": "non_guaranteed",
        "channels": ["display"],
        "inventory": {
            "publisher_properties": [
                {
                    "publisher_domain": "wonderstruck.com",
                    "selection_type": "by_id",
                    "property_ids": ["wonderstruck_site"],
                }
            ],
            "creative_formats": [
                {
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org",
                        "id": "homepage_takeover",
                    },
                    "slot_requirements": [
                        {
                            "slot_id": "leaderboard",
                            "name": "Leaderboard",
                            "asset_type": "image",
                            "width": 970,
                            "height": 250,
                            "required": True,
                        }
                    ],
                }
            ],
            "execution": {
                "adapter": "google_ad_manager",
                "selectors": [
                    {
                        "selector_type": "placement",
                        "external_id": "pl_homepage_takeover",
                    },
                    {
                        "selector_type": "ad_unit",
                        "external_id": "au_home",
                        "options": {"include_descendants": True},
                    },
                ],
                "format_bindings": [
                    {
                        "format_id": {
                            "agent_url": "https://creative.adcontextprotocol.org",
                            "id": "homepage_takeover",
                        },
                        "adapter_config": {
                            "creative_placeholders": [{"slot_id": "leaderboard", "size": "970x250"}],
                            "roadblocking": "as_many_as_possible",
                        },
                    }
                ],
            },
        },
        "targeting_capabilities": {"allowed_dimensions": ["geo", "device"]},
        "optimization_capabilities": {"allowed_goals": ["impressions"]},
    }
    payload.update(overrides)
    return payload


def test_inventory_discovery_surfaces_adapter_selectors_and_publisher_properties(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    capabilities = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/inventory/adapter-capabilities",
        headers=auth_headers,
    )
    assert capabilities.status_code == 200, capabilities.get_data(as_text=True)
    selector_types = {selector["selector_type"] for selector in capabilities.get_json()["selector_types"]}
    assert {"ad_unit", "placement"} <= selector_types

    selectors = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/inventory/selectors"
        "?selector_type=ad_unit&q=Homepage",
        headers=auth_headers,
    )
    assert selectors.status_code == 200, selectors.get_data(as_text=True)
    assert selectors.get_json()["selectors"][0]["external_id"] == "au_home"

    properties = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/inventory/publisher-properties",
        headers=auth_headers,
    )
    assert properties.status_code == 200, properties.get_data(as_text=True)
    body = properties.get_json()
    assert body["domains"][0]["publisher_domain"] == "wonderstruck.com"
    assert body["properties"][0]["property_id"] == "wonderstruck_site"
    assert {selector["selection_type"] for selector in body["allowed_selectors"]} == {"all", "by_id", "by_tag"}


def test_inventory_discovery_surfaces_springserve_selectors(management_api_client, bound_factories):
    def assert_single_selector_response(response, expected_selector):
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert set(body) == {"selectors", "count", "next_cursor"}
        assert body["count"] == 1
        assert body["selectors"] == [expected_selector]

    client, auth_headers = management_api_client
    tenant = TenantFactory(
        tenant_id="tenant_wholesale_springserve_selectors",
        name="Wonderstruck SpringServe",
        subdomain="wonderstruck-springserve",
        ad_server="springserve",
        is_embedded=True,
    )
    AdapterConfigFactory(tenant=tenant, adapter_type="springserve")
    SpringServeInventoryFactory(
        tenant=tenant,
        entity_type="supply_partner",
        entity_id="63440",
        name="Talpa Media",
        supply_partner_id=None,
        supply_router_id=None,
        key_id=None,
        raw_json={"id": 63440, "name": "Talpa Media", "account_id": 1730},
    )
    SpringServeInventoryFactory(
        tenant=tenant,
        entity_type="supply_tag",
        entity_id="945295",
        name="KIJK CTV",
        supply_partner_id="63440",
        supply_router_id="148010",
        key_id=None,
        raw_json={
            "id": 945295,
            "name": "KIJK CTV",
            "supply_partner_id": 63440,
            "format": "video",
        },
    )
    bound_factories.commit()

    partner_response = client.get(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/inventory/selectors?selectorType=supply_partner&q=Talpa",
        headers=auth_headers,
    )
    tag_response = client.get(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/inventory/selectors"
        "?selectorType=supply_tag&parentId=148010&q=KIJK",
        headers=auth_headers,
    )

    assert_single_selector_response(
        partner_response,
        {
            "selector_type": "supply_partner",
            "external_id": "63440",
            "name": "Talpa Media",
            "path": None,
            "parent_id": None,
            "status": None,
            "metadata": {"id": 63440, "name": "Talpa Media", "account_id": 1730},
        },
    )
    assert_single_selector_response(
        tag_response,
        {
            "selector_type": "supply_tag",
            "external_id": "945295",
            "name": "KIJK CTV",
            "path": None,
            "parent_id": "148010",
            "status": None,
            "metadata": {
                "id": 945295,
                "name": "KIJK CTV",
                "supply_partner_id": 63440,
                "format": "video",
            },
        },
    )


def test_creative_formats_authoring_endpoint_surfaces_discovery_errors(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    discovery_error = AdCPResponseError(
        code="REGISTRY_ERROR",
        message="Creative agent registry initialization failed: registry unavailable",
        recovery="transient",
        retry_after=5,
    )

    with patch(
        "src.core.format_resolver.list_available_formats_with_errors",
        return_value=SimpleNamespace(formats=[], errors=[discovery_error]),
    ):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/creative-formats",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["creative_formats"] == []
    assert body["count"] == 0
    assert len(body["errors"]) == 1
    error = body["errors"][0]
    assert error["code"] == discovery_error.code
    assert error["message"] == discovery_error.message
    assert error["recovery"] == discovery_error.recovery.value
    assert error["retry_after"] == 5


def test_creative_formats_authoring_endpoint_filters_standard_catalog_locally(management_api_client, gam_tenant):
    client, auth_headers = management_api_client

    response = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/creative-formats?asset_type=display&q=Display",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["count"] > 0
    assert body["creative_formats"]
    assert body["errors"] == []
    assert all("display" in format_summary["name"].lower() for format_summary in body["creative_formats"])
    assert all(format_summary["raw"].get("type") == "display" for format_summary in body["creative_formats"])
    assert "video_standard" not in {format_summary["format_id"]["id"] for format_summary in body["creative_formats"]}


def test_creative_formats_authoring_endpoint_canonicalizes_format_refs(management_api_client, gam_tenant):
    client, auth_headers = management_api_client

    with patch(
        "src.admin.blueprints.products.get_creative_format_catalog",
        return_value=SimpleNamespace(
            formats=[
                {
                    "format_id": {
                        "agent_url": "https://adcontextprotocol.org/agents/formats/mcp/",
                        "id": "display_image",
                    },
                    "name": "Display Image",
                    "asset_types": ["image"],
                    "requirements": {},
                }
            ],
            errors=[],
        ),
    ):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/creative-formats",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["creative_formats"][0]["format_id"]["agent_url"] == "https://creative.adcontextprotocol.org"
    assert body["creative_formats"][0]["format_id"]["id"] == "display_image"


def test_creative_formats_authoring_endpoint_preserves_format_parameters(management_api_client, gam_tenant):
    client, auth_headers = management_api_client

    with patch(
        "src.admin.blueprints.products.get_creative_format_catalog",
        return_value=SimpleNamespace(
            formats=[
                {
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org/",
                        "id": "display_image",
                        "width": 970,
                        "height": 250,
                    },
                    "name": "Display 970x250 Image",
                    "asset_types": ["image"],
                    "requirements": {},
                }
            ],
            errors=[],
        ),
    ):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/creative-formats",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["creative_formats"][0]["format_id"] == {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_image",
        "width": 970,
        "height": 250,
    }


def test_publisher_properties_lookup_enables_api_only_product_authoring(management_api_client, bound_factories):
    client, auth_headers = management_api_client
    tenant = TenantFactory(
        tenant_id="tenant_wholesale_api_only_lookup",
        name="Wonderstruck API Only",
        subdomain="wonderstruck-api-only",
        ad_server="google_ad_manager",
        is_embedded=True,
        public_agent_url="https://interchange.io",
    )
    configure_google_ad_manager_adapter(tenant)
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="ad_unit",
        inventory_id="au_api_only_home",
        name="API Only Homepage Ad Unit",
        path=["Wonderstruck", "API Only Homepage"],
        inventory_metadata={"parent_id": None, "sizes": [{"width": 970, "height": 250}]},
    )
    GAMInventoryFactory(
        tenant=tenant,
        inventory_type="placement",
        inventory_id="pl_api_only_homepage_takeover",
        name="API Only Homepage Takeover Placement",
        path=["Wonderstruck", "API Only Homepage Takeover"],
        inventory_metadata={"parent_id": None, "targeted_ad_unit_ids": ["au_api_only_home"]},
    )
    bound_factories.commit()

    status = PublisherPartnerStatus(
        publisher_domain="wonderstruck.org",
        total_properties=1,
        authorized_properties=1,
        status="unbound",
        aao_onboarding_url="https://agenticadvertising.org/publisher/wonderstruck.org",
        error="Publisher's entry has no authorization_type.",
    )
    adagents = {
        "properties": [
            {
                "property_id": "wonderstruck_home",
                "property_type": "website",
                "name": "Wonderstruck Home",
                "identifiers": [{"type": "domain", "value": "wonderstruck.org"}],
                "tags": ["all_inventory", "premium"],
            }
        ],
        "authorized_agents": [{"url": "https://interchange.io"}],
    }

    with (
        patch("src.admin.tenant_management_api.check_url_ssrf", return_value=(True, "")),
        patch("src.admin.tenant_management_api.get_publisher_partner_status", AsyncMock(return_value=status)),
        patch("src.services.property_discovery_service.fetch_adagents", AsyncMock(return_value=adagents)),
        patch("src.services.property_discovery_service.get_all_tags", return_value=["all_inventory", "premium"]),
    ):
        lookup = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/inventory/publisher-properties:lookup",
            headers=auth_headers,
            json={"publisher_domain": "https://Wonderstruck.org/", "force_refresh": True},
        )

    assert lookup.status_code == 200, lookup.get_data(as_text=True)
    lookup_body = lookup.get_json()
    assert lookup_body["publisher_domain"] == "wonderstruck.org"
    assert lookup_body["aao_status"] == "unbound"
    assert lookup_body["property_ids"] == ["wonderstruck_home"]
    assert lookup_body["property_tags"] == ["all_inventory", "premium"]
    assert lookup_body["domains"][0]["publisher_domain"] == "wonderstruck.org"
    assert {selector["selection_type"] for selector in lookup_body["allowed_selectors"]} == {"all", "by_id", "by_tag"}

    payload = _wholesale_payload(
        wholesale_product_id="api_only_homepage_takeover",
        name="API Only Homepage Takeover",
    )
    payload["inventory"]["publisher_properties"] = [
        {
            "publisher_domain": "wonderstruck.org",
            "selection_type": "by_id",
            "property_ids": ["wonderstruck_home"],
        }
    ]
    payload["inventory"]["execution"]["selectors"] = [
        {
            "selector_type": "placement",
            "external_id": "pl_api_only_homepage_takeover",
        },
        {
            "selector_type": "ad_unit",
            "external_id": "au_api_only_home",
            "options": {"include_descendants": True},
        },
    ]

    created = client.post(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=payload,
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    created_body = created.get_json()
    assert created_body["product_id"] == "api_only_homepage_takeover"
    assert created_body["inventory"]["publisher_properties"][0]["publisher_domain"] == "wonderstruck.org"


def test_wholesale_product_crud_persists_inventory_profile_and_derived_pricing(
    management_api_client, gam_tenant, bound_factories
):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()

    validation = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
        headers=auth_headers,
        json=payload,
    )
    assert validation.status_code == 200, validation.get_data(as_text=True)
    validation_body = validation.get_json()
    assert validation_body["valid"] is True
    assert all(not issue["code"].endswith("_ignored") for issue in validation_body["issues"])

    preview = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:preview",
        headers=auth_headers,
        json=payload,
    )
    assert preview.status_code == 200, preview.get_data(as_text=True)
    assert preview.get_json()["adapter_projection"]["inventory_config"]["ad_units"] == ["au_home"]
    assert preview.get_json()["buyer_projection"]["forecast"] is None

    created = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=payload,
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    created_body = created.get_json()
    assert created_body["product_id"] == "homepage_takeover"
    assert created_body["inventory_profile_id"] == "homepage_takeover"
    assert created_body["delivery_type"] == "non_guaranteed"
    assert created_body["forecast"] is None
    assert created_body["pricing_options"][0]["pricing_option_id"] == "cpm_usd_auction"
    assert created_body["pricing_options"][0]["is_fixed"] is False
    assert created_body["pricing_options"][0]["price_guidance"] == {"floor": 0.0}
    assert created_body["inventory"]["execution"]["selectors"][0]["selector_type"] == "placement"
    assert ProductRepository(bound_factories, gam_tenant.tenant_id).get_by_id("homepage_takeover") is None
    profile = InventoryProfileRepository(bound_factories, gam_tenant.tenant_id).get_by_id("homepage_takeover")
    assert profile is not None
    assert profile.forecast is None

    duplicate = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=payload,
    )
    assert duplicate.status_code == 409, duplicate.get_data(as_text=True)
    assert duplicate.get_json()["error"] == "wholesale_product_exists"

    listing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
    )
    assert listing.status_code == 200, listing.get_data(as_text=True)
    assert listing.get_json()["count"] == 1
    assert listing.get_json()["wholesale_products"][0]["pricing_options"][0]["pricing_option_id"] == "cpm_usd_auction"

    updated_payload = _wholesale_payload(
        name="Homepage Takeover Updated",
        status="draft",
    )
    updated = client.put(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
        json=updated_payload,
    )
    assert updated.status_code == 200, updated.get_data(as_text=True)
    updated_body = updated.get_json()
    assert updated_body["name"] == "Homepage Takeover Updated"
    assert updated_body["status"] == "draft"
    assert updated_body["pricing_options"][0]["pricing_option_id"] == "cpm_usd_auction"
    assert updated_body["forecast"] is None

    detail = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.get_data(as_text=True)
    assert detail.get_json()["inventory"]["creative_formats"][0]["slot_requirements"][0]["slot_id"] == "leaderboard"
    detail_selectors = detail.get_json()["inventory"]["execution"]["selectors"]
    assert detail_selectors[1]["options"] == {"include_descendants": True}

    list_selectors = listing.get_json()["wholesale_products"][0]["inventory"]["execution"]["selectors"]
    assert list_selectors[1]["options"] == {"include_descendants": True}

    deleted = client.delete(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert deleted.status_code == 200, deleted.get_data(as_text=True)

    missing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert missing.status_code == 404


def test_wholesale_create_invalidates_cached_status(management_api_client, gam_tenant):
    client, auth_headers = management_api_client

    before = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/status",
        headers=auth_headers,
    )
    assert before.status_code == 200, before.get_data(as_text=True)
    assert before.get_json()["products"]["active_count"] == 0

    created = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=_wholesale_payload(),
    )
    assert created.status_code == 201, created.get_data(as_text=True)

    after = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/status",
        headers=auth_headers,
    )
    assert after.status_code == 200, after.get_data(as_text=True)
    assert after.get_json()["products"]["active_count"] == 1
    assert after.get_json()["inventory_profiles"]["total_count"] == 1


def test_wholesale_product_authoring_rejects_system_metadata_inputs(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    payload = _wholesale_payload(
        forecast={"impressions": 1000000},
        pricing_options=[{"pricing_model": "cpm", "currency": "USD", "is_fixed": True, "rate": "40.00"}],
    )

    created = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=payload,
    )

    assert created.status_code == 422, created.get_data(as_text=True)
    body = created.get_json()
    assert {detail["loc"][-1] for detail in body} >= {"forecast", "pricing_options"}
    assert {detail["type"] for detail in body} == {"extra_forbidden"}


def test_wholesale_product_api_canonicalizes_legacy_reference_format_refs(
    management_api_client, gam_tenant, bound_factories
):
    client, auth_headers = management_api_client
    payload = _wholesale_payload(wholesale_product_id="legacy_reference_homepage")
    legacy_url = "https://adcontextprotocol.org/agents/formats"
    payload["inventory"]["creative_formats"][0]["format_id"]["agent_url"] = legacy_url
    payload["inventory"]["execution"]["format_bindings"][0]["format_id"]["agent_url"] = legacy_url

    created = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=payload,
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    created_body = created.get_json()
    created_format = created_body["inventory"]["creative_formats"][0]
    assert created_format["format_id"]["agent_url"].rstrip("/") == DEFAULT_CREATIVE_AGENT_URL
    assert created_format["slot_requirements"][0]["slot_id"] == "leaderboard"
    assert (
        created_body["inventory"]["execution"]["format_bindings"][0]["format_id"]["agent_url"].rstrip("/")
        == DEFAULT_CREATIVE_AGENT_URL
    )

    listing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
    )
    assert listing.status_code == 200, listing.get_data(as_text=True)
    listed_format = listing.get_json()["wholesale_products"][0]["inventory"]["creative_formats"][0]
    assert listed_format["format_id"]["agent_url"].rstrip("/") == DEFAULT_CREATIVE_AGENT_URL
    assert listed_format["slot_requirements"][0]["slot_id"] == "leaderboard"

    detail = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/legacy_reference_homepage",
        headers=auth_headers,
    )
    assert detail.status_code == 200, detail.get_data(as_text=True)
    detail_body = detail.get_json()
    detail_format = detail_body["inventory"]["creative_formats"][0]
    assert detail_format["format_id"]["agent_url"].rstrip("/") == DEFAULT_CREATIVE_AGENT_URL
    assert detail_format["slot_requirements"][0]["slot_id"] == "leaderboard"
    assert (
        detail_body["inventory"]["execution"]["format_bindings"][0]["format_id"]["agent_url"].rstrip("/")
        == DEFAULT_CREATIVE_AGENT_URL
    )


def test_local_example_domain_self_heals_existing_fixture_tenant(
    management_api_client,
    bound_factories,
    monkeypatch,
):
    client, auth_headers = management_api_client
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ADCP_TESTING", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)

    tenant = TenantFactory(
        tenant_id="tenant_wholesale_local_example",
        subdomain="wholesale-local-example",
        ad_server="mock",
        is_embedded=True,
    )
    AdapterConfigFactory(tenant=tenant, adapter_type="mock")
    InventoryProfileFactory(
        tenant=tenant,
        profile_id="existing_example_profile",
        inventory_config={"adapter": "mock", "selectors": []},
        format_ids=[],
        publisher_properties=[{"publisher_domain": "example.com", "selection_type": "all"}],
    )
    bound_factories.commit()

    assert (
        bound_factories.get(AuthorizedProperty, {"tenant_id": tenant.tenant_id, "property_id": "example_com"}) is None
    )

    payload = {
        "wholesale_product_id": "new_example_profile",
        "name": "New Example Profile",
        "description": "Local fixture product using the sample publisher domain.",
        "status": "active",
        "delivery_type": "non_guaranteed",
        "inventory": {
            "publisher_properties": [{"publisher_domain": "example.com", "selection_type": "all"}],
            "creative_formats": [
                {
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org",
                        "id": "display_300x250",
                    }
                }
            ],
            "execution": {
                "adapter": "mock",
                "selectors": [],
                "format_bindings": [
                    {
                        "format_id": {
                            "agent_url": "https://creative.adcontextprotocol.org",
                            "id": "display_300x250",
                        },
                        "adapter_config": {},
                    }
                ],
            },
        },
    }

    validation = client.post(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/wholesale-products:validate",
        headers=auth_headers,
        json=payload,
    )
    assert validation.status_code == 200, validation.get_data(as_text=True)
    assert validation.get_json()["valid"] is True

    preview = client.post(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/wholesale-products:preview",
        headers=auth_headers,
        json=payload,
    )
    assert preview.status_code == 200, preview.get_data(as_text=True)
    assert preview.get_json()["validation"]["valid"] is True
    bound_factories.expire_all()
    assert (
        bound_factories.get(AuthorizedProperty, {"tenant_id": tenant.tenant_id, "property_id": "example_com"}) is None
    )

    created = client.post(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=payload,
    )

    assert created.status_code == 201, created.get_data(as_text=True)
    bound_factories.expire_all()
    authorized_property = bound_factories.get(
        AuthorizedProperty,
        {"tenant_id": tenant.tenant_id, "property_id": "example_com"},
    )
    assert authorized_property is not None
    assert authorized_property.publisher_domain == "example.com"
    assert authorized_property.verification_status == "verified"
    partner = TenantConfigRepository(bound_factories, tenant.tenant_id).get_publisher_partner_by_domain("example.com")
    assert partner is not None
    assert partner.is_verified is True


def test_wholesale_replace_self_heals_local_example_authorization(
    management_api_client,
    gam_tenant,
    bound_factories,
    monkeypatch,
):
    client, auth_headers = management_api_client
    product_id = "replace_example_profile"
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("ADCP_TESTING", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)

    profile = InventoryProfileFactory(
        tenant=gam_tenant,
        profile_id=product_id,
        inventory_config={"adapter": "google_ad_manager", "selectors": []},
        format_ids=[],
        publisher_properties=[],
        constraints={
            "formats": [],
            "channels": [],
            "targeting_dimensions": [],
            "managed_by": "wholesale_products_api",
            "owner_product_id": product_id,
        },
    )
    product = ProductFactory(
        tenant=gam_tenant,
        product_id=product_id,
        name="Replace Example Profile",
        implementation_config={"adapter": "google_ad_manager", "status": "active"},
        inventory_profile=profile,
        properties=[],
        property_tags=None,
    )
    PricingOptionFactory(product=product)
    bound_factories.commit()
    config_repo = TenantConfigRepository(bound_factories, gam_tenant.tenant_id)
    assert config_repo.get_authorized_property_by_id("example_com") is None

    payload = _wholesale_payload(wholesale_product_id=product_id, name="Replace Example Profile")
    payload["inventory"]["publisher_properties"] = [{"publisher_domain": "example.com", "selection_type": "all"}]
    replaced = client.put(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/{product_id}",
        headers=auth_headers,
        json=payload,
    )

    assert replaced.status_code == 200, replaced.get_data(as_text=True)
    bound_factories.expire_all()
    authorized_property = config_repo.get_authorized_property_by_id("example_com")
    assert authorized_property is not None
    assert authorized_property.publisher_domain == "example.com"


def test_profile_backed_generic_selectors_round_trip_without_legacy_gam_keys(
    management_api_client,
    bound_factories,
):
    client, auth_headers = management_api_client
    tenant = TenantFactory(
        tenant_id="tenant_wholesale_springserve",
        subdomain="wholesale-springserve",
        ad_server="springserve",
        is_embedded=True,
    )
    AdapterConfigFactory(tenant=tenant, adapter_type="springserve")
    publisher_properties = [
        {"publisher_domain": "wonderstruck.com", "selection_type": "by_id", "property_ids": ["wonderstruck_site"]}
    ]
    format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]
    profile = InventoryProfileFactory(
        tenant=tenant,
        profile_id="springserve_homepage",
        inventory_config={
            "adapter": "springserve",
            "selectors": [
                {
                    "selector_type": "zone",
                    "external_id": "zone_123",
                    "name": "Homepage Zone",
                    "options": {"priority": "high"},
                }
            ],
            "format_bindings": [
                {
                    "format_id": format_ids[0],
                    "adapter_config": {"creative_template": "standard_display"},
                }
            ],
        },
        format_ids=format_ids,
        publisher_properties=publisher_properties,
        constraints={
            "managed_by": "wholesale_products_api",
            "owner_product_id": "springserve_homepage",
            "status": "active",
        },
    )
    product = ProductFactory(
        tenant=tenant,
        product_id="springserve_homepage",
        name="SpringServe Homepage",
        implementation_config={"adapter": "springserve", "status": "active"},
        inventory_profile=profile,
        properties=publisher_properties,
        property_tags=None,
    )
    PricingOptionFactory(product=product)
    bound_factories.commit()

    detail = client.get(
        f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/wholesale-products/springserve_homepage",
        headers=auth_headers,
    )

    assert detail.status_code == 200, detail.get_data(as_text=True)
    execution = detail.get_json()["inventory"]["execution"]
    assert execution["adapter"] == "springserve"
    assert execution["selectors"] == [
        {
            "selector_type": "zone",
            "external_id": "zone_123",
            "name": "Homepage Zone",
            "options": {"priority": "high"},
        }
    ]
    assert execution["format_bindings"][0]["adapter_config"] == {"creative_template": "standard_display"}


def test_wholesale_products_tolerate_extra_stored_publisher_property_fields(
    management_api_client,
    gam_tenant,
    bound_factories,
):
    client, auth_headers = management_api_client
    publisher_properties = [
        {
            "publisher_domain": "wonderstruck.com",
            "property_id": "wonderstruck_site",
            "tags": ["sports"],
            "name": "wonderstruck.com",
            "property_type": "website",
        }
    ]
    format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]
    profile = InventoryProfileFactory(
        tenant=gam_tenant,
        profile_id="extra_fields_profile",
        inventory_config={"adapter": "google_ad_manager", "selectors": []},
        format_ids=format_ids,
        publisher_properties=publisher_properties,
        constraints={
            "managed_by": "wholesale_products_api",
            "owner_product_id": "extra_fields_profile",
            "status": "active",
        },
    )
    product = ProductFactory(
        tenant=gam_tenant,
        product_id="extra_fields_product",
        name="Extra Fields Product",
        implementation_config={"adapter": "google_ad_manager", "status": "active"},
        inventory_profile=profile,
        properties=publisher_properties,
        property_tags=None,
    )
    PricingOptionFactory(product=product)
    bound_factories.commit()

    listing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
    )

    assert listing.status_code == 200, listing.get_data(as_text=True)
    properties = listing.get_json()["wholesale_products"][0]["inventory"]["publisher_properties"]
    assert properties[0]["publisher_domain"] == "wonderstruck.com"
    assert properties[0]["selection_type"] == "by_id"
    assert properties[0]["property_ids"] == ["wonderstruck_site"]
    assert "name" not in properties[0]
    assert "property_type" not in properties[0]


def test_wholesale_products_do_not_fall_back_to_legacy_product_rows(
    management_api_client,
    gam_tenant,
    bound_factories,
):
    client, auth_headers = management_api_client
    product = ProductFactory(
        tenant=gam_tenant,
        product_id="legacy_product_row",
        name="Legacy Product Row",
        forecast={"impressions": 100000},
    )
    PricingOptionFactory(product=product, pricing_model="cpm", is_fixed=True, rate=40)
    bound_factories.commit()

    listing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
    )
    detail = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/legacy_product_row",
        headers=auth_headers,
    )

    assert listing.status_code == 200, listing.get_data(as_text=True)
    assert "legacy_product_row" not in {
        wholesale_product["product_id"] for wholesale_product in listing.get_json()["wholesale_products"]
    }
    assert detail.status_code == 404, detail.get_data(as_text=True)


def test_wholesale_validation_checks_authorized_publisher_properties(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()
    payload["inventory"]["publisher_properties"][0]["property_ids"] = ["raptive_site"]

    validation = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
        headers=auth_headers,
        json=payload,
    )

    assert validation.status_code == 200, validation.get_data(as_text=True)
    body = validation.get_json()
    assert body["valid"] is False
    assert {issue["code"] for issue in body["issues"]} >= {"publisher_property_not_authorized"}


def test_wholesale_validation_checks_discovered_creative_formats(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()

    with patch(
        "src.admin.blueprints.products.get_creative_formats",
        return_value=[
            {
                "format_id": {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x250",
                },
                "name": "Display 300x250",
            }
        ],
    ):
        validation = client.post(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
            headers=auth_headers,
            json=payload,
        )

    assert validation.status_code == 200, validation.get_data(as_text=True)
    body = validation.get_json()
    assert body["valid"] is False
    assert {issue["code"] for issue in body["issues"]} >= {"creative_format_not_found"}


def test_wholesale_validation_canonicalizes_catalog_creative_format_agent_url(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()
    payload["inventory"]["creative_formats"][0]["format_id"] = {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_970x250_image",
    }
    payload["inventory"]["execution"]["format_bindings"][0]["format_id"] = {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_970x250_image",
    }

    with patch(
        "src.admin.blueprints.products.get_creative_formats",
        return_value=[
            {
                "format_id": {
                    "agent_url": "https://creative.adcontextprotocol.org/",
                    "id": "display_970x250_image",
                },
                "name": "Display 970x250 Image",
            }
        ],
    ):
        validation = client.post(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
            headers=auth_headers,
            json=payload,
        )

    assert validation.status_code == 200, validation.get_data(as_text=True)
    body = validation.get_json()
    assert body["valid"] is True
    assert body["issues"] == []


def test_wholesale_validation_preserves_catalog_creative_format_parameters(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()
    payload["inventory"]["creative_formats"][0]["format_id"] = {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_image",
        "width": 970,
        "height": 250,
    }
    payload["inventory"]["execution"]["format_bindings"][0]["format_id"] = {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_image",
        "width": 970,
        "height": 250,
    }

    with patch(
        "src.admin.blueprints.products.get_creative_formats",
        return_value=[
            {
                "format_id": {
                    "agent_url": "https://creative.adcontextprotocol.org/",
                    "id": "display_image",
                    "width": 300,
                    "height": 250,
                },
                "name": "Display 300x250 Image",
            }
        ],
    ):
        validation = client.post(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
            headers=auth_headers,
            json=payload,
        )

    assert validation.status_code == 200, validation.get_data(as_text=True)
    body = validation.get_json()
    assert body["valid"] is False
    assert {issue["code"] for issue in body["issues"]} >= {"creative_format_not_found"}


def test_wholesale_validation_accepts_parameterized_creative_format_refs(management_api_client, gam_tenant):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()
    payload["inventory"]["creative_formats"][0]["format_id"] = {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_image",
        "width": 970,
        "height": 250,
    }
    payload["inventory"]["execution"]["format_bindings"][0]["format_id"] = {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "display_image",
        "width": 970,
        "height": 250,
    }

    with patch(
        "src.admin.blueprints.products.get_creative_formats",
        return_value=[
            {
                "format_id": {
                    "agent_url": "https://creative.adcontextprotocol.org/",
                    "id": "display_image",
                    "width": 970,
                    "height": 250,
                },
                "name": "Display 970x250 Image",
            }
        ],
    ):
        validation = client.post(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
            headers=auth_headers,
            json=payload,
        )

    assert validation.status_code == 200, validation.get_data(as_text=True)
    body = validation.get_json()
    assert body["valid"] is True
    assert body["issues"] == []


@pytest.mark.parametrize(
    "catalog_agent_url",
    [
        "https://creative.adcontextprotocol.org/mcp",  # /mcp transport suffix
        "https://adcontextprotocol.org/agents/formats",  # legacy reference alias
    ],
)
def test_wholesale_validation_canonicalizes_non_canonical_catalog_format_refs(
    management_api_client, gam_tenant, catalog_agent_url
):
    client, auth_headers = management_api_client
    payload = _wholesale_payload()
    payload["inventory"]["creative_formats"][0]["format_id"] = {
        "agent_url": "https://creative.adcontextprotocol.org",
        "id": "homepage_takeover",
    }

    with patch(
        "src.admin.blueprints.products.get_creative_formats",
        return_value=[
            {
                "format_id": {
                    "agent_url": catalog_agent_url,
                    "id": "homepage_takeover",
                },
                "name": "Homepage Takeover",
            }
        ],
    ):
        validation = client.post(
            f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products:validate",
            headers=auth_headers,
            json=payload,
        )

    assert validation.status_code == 200, validation.get_data(as_text=True)
    body = validation.get_json()
    assert "creative_format_not_found" not in {issue["code"] for issue in body["issues"]}


def test_wholesale_create_rejects_existing_unowned_inventory_profile(
    management_api_client, gam_tenant, bound_factories
):
    client, auth_headers = management_api_client
    profile = InventoryProfileFactory(
        tenant=gam_tenant,
        profile_id="homepage_takeover",
        constraints={"formats": ["display_300x250"]},
    )

    listing = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
    )
    assert listing.status_code == 200, listing.get_data(as_text=True)
    assert listing.get_json()["count"] == 0

    detail = client.get(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert detail.status_code == 404, detail.get_data(as_text=True)

    deleted = client.delete(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products/homepage_takeover",
        headers=auth_headers,
    )
    assert deleted.status_code == 404, deleted.get_data(as_text=True)
    assert InventoryProfileRepository(bound_factories, gam_tenant.tenant_id).get_by_id(profile.profile_id) is not None

    created = client.post(
        f"/api/v1/tenant-management/tenants/{gam_tenant.tenant_id}/wholesale-products",
        headers=auth_headers,
        json=_wholesale_payload(),
    )

    assert created.status_code == 409, created.get_data(as_text=True)
    assert created.get_json()["error"] == "inventory_profile_conflict"
