"""The GAM reporting routes read a tenant's ad server and a principal's advertiser id from the model.

A tenant is on Google Ad Manager when either its legacy ``ad_server`` column or its adapter
config says so (``Tenant.is_gam_tenant``), and the admin UI stores a principal's advertiser
as ``platform_mappings["google_ad_manager"]["advertiser_id"]`` (``Principal.get_adapter_id``
reads that and the legacy root key). Each case here is a configuration the admin UI writes,
answered with the report rather than a 400.
"""

from unittest.mock import patch

import pytest

from tests.factories import AdapterConfigFactory, PrincipalFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_MODULE = "src.admin.blueprints.gam_reporting_api"


@pytest.fixture
def reporting_service():
    """Stand-in for GAM: the client and the reporting service the routes build from it."""
    with (
        patch(f"{_MODULE}.get_ad_manager_client_for_tenant"),
        patch("scripts.ops.gam_helper.ensure_network_timezone", return_value="America/New_York"),
        patch(f"{_MODULE}.GAMReportingService") as service_class,
    ):
        service = service_class.return_value
        service.get_advertiser_summary.return_value = {"impressions": 7}
        yield service


def test_a_tenant_on_gam_through_its_adapter_config_gets_reporting(
    authenticated_admin_session, bound_factory_session, reporting_service
):
    tenant = TenantFactory(ad_server="mock")
    AdapterConfigFactory(tenant=tenant, adapter_type="google_ad_manager")

    response = authenticated_admin_session.get(
        f"/api/tenant/{tenant.tenant_id}/gam/reporting/advertiser/123/summary?date_range=today"
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {"success": True, "data": {"impressions": 7}}
    reporting_service.get_advertiser_summary.assert_called_once_with(
        advertiser_id="123", date_range="today", requested_timezone="America/New_York"
    )


def test_a_principal_mapped_in_the_admin_ui_gets_its_reporting(
    authenticated_admin_session, bound_factory_session, reporting_service
):
    tenant = TenantFactory(ad_server="google_ad_manager")
    principal = PrincipalFactory(tenant=tenant, platform_mappings={"google_ad_manager": {"advertiser_id": "4242"}})

    response = authenticated_admin_session.get(
        f"/api/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/gam/reporting/summary?date_range=today"
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {
        "success": True,
        "data": {"impressions": 7, "principal_id": principal.principal_id},
    }
    reporting_service.get_advertiser_summary.assert_called_once_with(
        advertiser_id="4242", date_range="today", requested_timezone="America/New_York"
    )
