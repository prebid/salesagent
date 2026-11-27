"""The GAM line item viewer shows what the orders sync stored for a line item (#2728 review).

The viewer page renders an empty shell and fills it from the JSON route
``/tenant/<id>/gam/api/line-item/<id>``, which answers the stored line item and its stored
order. Only a browser running the page's script shows whether the fetch reaches that route
and whether each tab reads the keys it answers. The JSON body itself is graded by
``T-ADMIN-REFUSAL-059``. The page is opened by an admin of the tenant, a member and not a
super admin.

The seller stores no creatives for a line item, so the page has no Creatives tab, and no
Product JSON tab either.
"""

from datetime import UTC, datetime

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui

ADMIN_EMAIL = "ui-line-item-admin@member.psatest.com"
LINE_ITEM_ID = "7047822666"
ORDER_ID = "3300001"
NAME = "Stored Sponsorship Line Item"
LINE_ITEM_TYPE = "SPONSORSHIP"
ORDER_NAME = "Stored Spring Order"
ADVERTISER_NAME = "Stored Advertiser"
# Below 1000, so the page's thousands separator does not depend on the browser's locale.
GOAL_UNITS = 800
IMPRESSIONS = 600
CLICKS = 12
AD_UNIT_ID = "4400001"
CUSTOM_KEY_ID = 5500001
CUSTOM_VALUE_ID = 6600001
#: GAM's targeting, frequency caps and creative placeholders, as the orders sync serializes them.
TARGETING = {
    "inventoryTargeting": {"targetedAdUnits": [{"adUnitId": AD_UNIT_ID, "includeDescendants": True}]},
    "customTargeting": {
        "logicalOperator": "OR",
        "children": [
            {
                "logicalOperator": "AND",
                "children": [{"keyId": CUSTOM_KEY_ID, "operator": "IS", "valueIds": [CUSTOM_VALUE_ID]}],
            }
        ],
    },
}
FREQUENCY_CAPS = [{"maxImpressions": 3, "numTimeUnits": 1, "timeUnit": "DAY"}]
CREATIVE_PLACEHOLDERS = [{"size": {"width": 300, "height": 250}, "expectedCreativeCount": 2}]


@pytest.fixture
def tenant_with_a_stored_line_item(server_seeding, server_tenant):
    """A tenant of its own (``server_tenant``) with an admin and one stored line item and order; the tenant's id."""
    from tests.factories import AdapterConfigFactory, GAMLineItemFactory, GAMOrderFactory, UserFactory

    with server_seeding():
        tenant = server_tenant("ui_line_item")
        AdapterConfigFactory(tenant=tenant, adapter_type="mock")
        UserFactory(tenant=tenant, email=ADMIN_EMAIL, role="admin")
        GAMOrderFactory(tenant=tenant, order_id=ORDER_ID, name=ORDER_NAME, advertiser_name=ADVERTISER_NAME)
        GAMLineItemFactory(
            tenant=tenant,
            line_item_id=LINE_ITEM_ID,
            order_id=ORDER_ID,
            name=NAME,
            line_item_type=LINE_ITEM_TYPE,
            priority=4,
            cost_type="CPM",
            cost_per_unit=2.5,
            primary_goal_units=GOAL_UNITS,
            stats_impressions=IMPRESSIONS,
            stats_clicks=CLICKS,
            start_date=datetime(2025, 1, 5, tzinfo=UTC),
            delivery_rate_type="FRONTLOADED",
            allow_overbook=True,
            targeting=TARGETING,
            frequency_caps=FREQUENCY_CAPS,
            creative_placeholders=CREATIVE_PLACEHOLDERS,
        )
        return tenant.tenant_id


def _open_tab(page: Page, tab_id: str, pane_id: str):
    """Click the tab *tab_id* and return its pane, once it is shown."""
    page.locator(f"#{tab_id}").click()
    pane = page.locator(f"#{pane_id}")
    expect(pane).to_be_visible()
    return pane


def test_the_viewer_shows_the_stored_line_item(tenant_with_a_stored_line_item, member_page, base_url):
    page: Page = member_page(ADMIN_EMAIL, tenant_with_a_stored_line_item)
    page.goto(f"{base_url}/tenant/{tenant_with_a_stored_line_item}/gam/line-item/{LINE_ITEM_ID}")

    expect(page.locator("#lineItemContent")).to_be_visible()
    basic = page.locator("#basicInfo")
    for shown in (LINE_ITEM_ID, NAME, LINE_ITEM_TYPE):
        expect(basic).to_contain_text(shown)
    order = page.locator("#orderInfo")
    for shown in (ORDER_ID, ORDER_NAME, ADVERTISER_NAME):
        expect(order).to_contain_text(shown)
    pricing = page.locator("#pricingInfo")
    expect(pricing).to_contain_text("$2.50")
    expect(pricing).to_contain_text(f"{GOAL_UNITS}")
    expect(pricing).to_contain_text(f"{IMPRESSIONS} (75.0%)")
    expect(pricing).to_contain_text(f"{CLICKS}")
    # The start date is in 2025; nothing else on the page is.
    expect(page.locator("#scheduleInfo")).to_contain_text("2025")

    targeting = _open_tab(page, "targeting-tab", "targeting")
    expect(targeting).to_contain_text(f"Ad Unit ID: {AD_UNIT_ID} (+ descendants)")
    expect(targeting).to_contain_text(f"Key ID: {CUSTOM_KEY_ID}")
    expect(targeting).to_contain_text(f"Value IDs: {CUSTOM_VALUE_ID}")

    delivery = _open_tab(page, "delivery-tab", "delivery")
    expect(delivery).to_contain_text("Delivery Rate Type: FRONTLOADED")
    expect(delivery).to_contain_text("Allow Overbook: Yes")
    expect(delivery).to_contain_text("3 impressions per 1 DAY")
    expect(delivery).to_contain_text("300x250")

    # No tab is left that nothing the seller stores can fill.
    expect(page.locator("#lineItemTabs button")).to_have_count(4)
    expect(page.locator("#error")).to_be_hidden()
    assert page.js_errors == [], f"JS errors on the line item viewer: {page.js_errors}"
