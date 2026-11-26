"""The GAM line item viewer shows a stored line item's details (#2728 review).

The viewer page renders an empty shell and fills it from the JSON route
``/tenant/<id>/gam/api/line-item/<id>``, which answers the columns the orders sync stored.
Only a browser running the page's script shows whether the fetch reaches that route and
whether the page reads the keys it answers. The JSON body itself is graded by
``T-ADMIN-REFUSAL-059``. The page is opened by an admin of the tenant, a member and not a
super admin.
"""

from datetime import UTC, datetime

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui

ADMIN_EMAIL = "ui-line-item-admin@member.psatest.com"
LINE_ITEM_ID = "7047822666"
NAME = "Stored Sponsorship Line Item"
LINE_ITEM_TYPE = "SPONSORSHIP"
# Below 1000, so the page's thousands separator does not depend on the browser's locale.
GOAL_UNITS = 800
IMPRESSIONS = 600
CLICKS = 12


@pytest.fixture
def tenant_with_a_stored_line_item(server_seeding, server_tenant):
    """A tenant of its own (``server_tenant``) with an admin and one stored line item; the tenant's id."""
    from tests.factories import AdapterConfigFactory, GAMLineItemFactory, UserFactory

    with server_seeding():
        tenant = server_tenant("ui_line_item")
        AdapterConfigFactory(tenant=tenant, adapter_type="mock")
        UserFactory(tenant=tenant, email=ADMIN_EMAIL, role="admin")
        GAMLineItemFactory(
            tenant=tenant,
            line_item_id=LINE_ITEM_ID,
            name=NAME,
            line_item_type=LINE_ITEM_TYPE,
            priority=4,
            cost_type="CPM",
            cost_per_unit=2.5,
            primary_goal_units=GOAL_UNITS,
            stats_impressions=IMPRESSIONS,
            stats_clicks=CLICKS,
            start_date=datetime(2025, 1, 5, tzinfo=UTC),
        )
        return tenant.tenant_id


def test_the_viewer_shows_the_stored_line_item(tenant_with_a_stored_line_item, member_page, base_url):
    page: Page = member_page(ADMIN_EMAIL, tenant_with_a_stored_line_item)
    page.goto(f"{base_url}/tenant/{tenant_with_a_stored_line_item}/gam/line-item/{LINE_ITEM_ID}")

    expect(page.locator("#lineItemContent")).to_be_visible()
    basic = page.locator("#basicInfo")
    for shown in (LINE_ITEM_ID, NAME, LINE_ITEM_TYPE):
        expect(basic).to_contain_text(shown)
    pricing = page.locator("#pricingInfo")
    expect(pricing).to_contain_text("$2.50")
    expect(pricing).to_contain_text(f"{GOAL_UNITS}")
    expect(pricing).to_contain_text(f"{IMPRESSIONS} (75.0%)")
    expect(pricing).to_contain_text(f"{CLICKS}")
    # The start date is in 2025; nothing else on the page is.
    expect(page.locator("#scheduleInfo")).to_contain_text("2025")
    expect(page.locator("#error")).to_be_hidden()
    assert page.js_errors == [], f"JS errors on the line item viewer: {page.js_errors}"
