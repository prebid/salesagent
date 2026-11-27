"""The Policy page keeps what an operator saves: reloaded, it shows the saved policy (#2692, #2697).

The page edits ``tenants.advertising_policy``, the column ``get_products`` enforces. It used
to save into a column of its own and render its defaults over whatever was stored, so an
operator's save looked lost. This drives the page as a browser does: open it on a tenant
with no saved policy, enter edit mode, fill in the form, save, reload, and read the values
the page shows. The page is opened by an admin of the tenant, a member and not a super admin.
"""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui

ADMIN_EMAIL = "ui-policy-admin@member.psatest.com"

#: What the operator types into each list, one entry per line, by the textarea's id.
LISTS = {
    "prohibited_advertisers": ["blocked.example", "competitor.example"],
    "prohibited_categories": ["gambling"],
    "prohibited_tactics": ["false urgency"],
    "default_prohibited_categories": ["illegal_content", "hate_speech"],
    "default_prohibited_tactics": ["impersonation"],
}


@pytest.fixture
def tenant_without_a_policy(server_seeding, server_tenant):
    """A tenant of its own (``server_tenant``) that has saved no policy, with an admin; the tenant's id."""
    from tests.factories import AdapterConfigFactory, UserFactory

    with server_seeding():
        tenant = server_tenant("ui_policy")
        AdapterConfigFactory(tenant=tenant, adapter_type="mock")
        UserFactory(tenant=tenant, email=ADMIN_EMAIL, role="admin")
        return tenant.tenant_id


def test_the_policy_page_shows_the_saved_policy_after_a_reload(tenant_without_a_policy, member_page, base_url):
    page: Page = member_page(ADMIN_EMAIL, tenant_without_a_policy)
    policy_url = f"{base_url}/tenant/{tenant_without_a_policy}/policy/"
    page.goto(policy_url)
    page.wait_for_load_state("load")
    expect(page.locator("#no-policy-saved")).to_be_visible()

    page.get_by_role("button", name="Edit Settings").click()
    page.locator("#enabled").check()
    page.locator("#require_manual_review").check()
    for field_id, entries in LISTS.items():
        page.locator(f"#{field_id}").fill("\n".join(entries))
    with page.expect_navigation():
        page.get_by_role("button", name="Save All Policy Settings").click()

    page.goto(policy_url)
    page.wait_for_load_state("load")
    expect(page.locator("#no-policy-saved")).to_have_count(0)
    expect(page.locator("#enabled")).to_be_checked()
    expect(page.locator("#require_manual_review")).to_be_checked()
    shown = {field_id: page.locator(f"#{field_id}").input_value().splitlines() for field_id in LISTS}
    assert shown == LISTS, f"Expected the saved lists {LISTS!r}, the page shows {shown!r}"
    # What the operator reads in view mode: each saved advertiser is listed.
    for advertiser in LISTS["prohibited_advertisers"]:
        expect(page.locator(".policy-list-item", has_text=advertiser)).to_be_visible()
    assert page.js_errors == [], f"JS errors on the Policy page: {page.js_errors}"
