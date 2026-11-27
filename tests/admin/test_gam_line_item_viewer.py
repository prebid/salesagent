"""Admin: the GAM line item viewer renders a line item the orders sync stored.

The routes once fell back to a live GAM lookup that could never run
(salesagent-fp6gt.11.3). A line item that is not stored is refused with
``LineItemNotFound`` (T-ADMIN-REFUSAL-040, -055); the JSON route's stored answer
is graded by T-ADMIN-REFUSAL-059.
"""

from __future__ import annotations

import pytest

from tests.factories import GAMLineItemFactory, TenantFactory
from tests.helpers.admin_session import admin_auth_session

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "gam_li_viewer_t1"


@pytest.fixture
def stored_line_item(factory_session):
    tenant = TenantFactory(tenant_id=_TENANT_ID, ad_server="google_ad_manager")
    return GAMLineItemFactory(tenant=tenant, line_item_id="987654", name="Stored Line Item")


def test_viewer_renders_a_stored_line_item(admin_client, stored_line_item):
    admin_auth_session(admin_client, _TENANT_ID)

    resp = admin_client.get(f"/tenant/{_TENANT_ID}/gam/line-item/987654")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200, f"{resp.status_code}\n{body[:500]}"
    # The viewer's header names the line item; error.html carries no such line.
    assert "Line Item ID: 987654" in body
