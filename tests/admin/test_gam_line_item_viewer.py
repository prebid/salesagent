"""Admin repro: the GAM-fallback branch of view_gam_line_item must render.

When a line item is not in the local DB, ``view_gam_line_item`` fetches it
from GAM and builds a "temporary line item object for display". That object
was constructed as ``GAMLineItem(currency_code=..., goal_units=...,
units_delivered=..., impressions_delivered=..., clicks_delivered=..., ctr=...,
raw_data=...)`` — seven kwargs the mapper does not have — so SQLAlchemy's
declarative constructor raised TypeError, the route's blanket ``except``
turned it into a 500 error page, and the whole fetch-from-GAM branch never
worked against the current schema (salesagent-3pj8).

This drives the real route with the reporting service mocked at the module
seam (the only external dependency) and asserts the page renders.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.factories import AdapterConfigFactory, TenantFactory
from tests.helpers.admin_session import admin_auth_session

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "gam_li_viewer_t1"

_GAM_LINE_ITEM = {
    "id": 987654,
    "name": "GAM-only Line Item",
    "orderId": 123456,
    "status": "DELIVERING",
    "startDateTime": None,
    "endDateTime": None,
    "lineItemType": "STANDARD",
    "priority": 8,
    "costType": "CPM",
    "costPerUnit": {"microAmount": 5_000_000, "currencyCode": "USD"},
    "primaryGoal": {"goalType": "LIFETIME", "units": 100_000},
}


@pytest.fixture
def gam_tenant(factory_session):
    tenant = TenantFactory(tenant_id=_TENANT_ID, ad_server="google_ad_manager")
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="12345678",
        gam_refresh_token="refresh-token",
    )
    return tenant


class TestGamFallbackRendersFetchedLineItem:
    def test_line_item_absent_from_db_renders_from_gam(self, admin_client, gam_tenant):
        """A line item that exists in GAM but not the local DB must render, not 500."""
        admin_auth_session(admin_client, _TENANT_ID)

        with patch("src.admin.blueprints.gam.GAMReportingService") as service_cls:
            service_cls.return_value.get_line_item_details.return_value = dict(_GAM_LINE_ITEM)
            resp = admin_client.get(f"/tenant/{_TENANT_ID}/gam/line-item/987654")

        body = resp.get_data(as_text=True)
        assert resp.status_code == 200, f"fallback branch failed: {resp.status_code}\n{body[:500]}"
        # The viewer template's header renders "Line Item ID: <id> | Tenant: ..."
        # (error.html carries no such line) — proves the viewer rendered with
        # the requested id prefilled, not the blanket-except error page.
        assert "Line Item ID: 987654" in body, "viewer did not render with the requested line item id"
