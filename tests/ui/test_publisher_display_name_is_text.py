"""A publisher's display name is text in the browser, on every page that lists publishers.

A tenant user names a publisher, and the admin pages that list publishers write that name
into the page they build. A name carrying markup ran script in the session of every admin
who opened one of them. ``static/js/publisher_partners.js`` writes the list for both pages
(the inventory page's Publishers tab and the settings page's Publishers section) and puts
the name in through ``escapeHtml``; the Node test of that function grades its output, and
this grades what a browser does with the page.

The name's ``onerror`` sets a flag on the page instead of opening a dialog, so the test can
read whether it ran. The page is opened by an admin of the tenant, a member and not a super
admin.
"""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui

NAME = '<img src=x onerror="window.publisherNameRan = true">'
ADMIN_EMAIL = "ui-tenant-admin@member.psatest.com"
#: A test publisher domain (docker-compose.e2e.yml routes it through tls-proxy to publisher-origin).
FORM_DOMAIN = "partner-psatest.com"


@pytest.fixture
def tenant_with_an_admin(server_seeding, server_tenant):
    """A tenant of its own (``server_tenant``), with an admin; the tenant's id."""
    from tests.factories import AdapterConfigFactory, UserFactory

    with server_seeding():
        tenant = server_tenant("ui_xss")
        AdapterConfigFactory(tenant=tenant, adapter_type="mock")
        UserFactory(tenant=tenant, email=ADMIN_EMAIL, role="admin")
        return tenant.tenant_id


@pytest.fixture
def tenant_with_a_markup_named_publisher(tenant_with_an_admin, server_seeding):
    """That tenant, holding a publisher named with markup; the tenant's id."""
    from tests.factories import PublisherFactory

    with server_seeding():
        # ``tenant`` only supplies tenant_id, which is given: None keeps it from making a second tenant.
        PublisherFactory(tenant=None, tenant_id=tenant_with_an_admin, display_name=NAME)
    return tenant_with_an_admin


def _assert_the_name_is_text(page: Page, where: str) -> None:
    """The publisher list shows NAME as text: no element made of it, its onerror never ran, no JS error."""
    # A name parsed as markup has no text node equal to it; the wait covers the list's fetch.
    listing = page.locator("#publishersList")
    expect(listing.get_by_text(NAME, exact=True)).to_be_visible()
    assert listing.locator("img").count() == 0, listing.inner_html()
    assert page.evaluate("window.publisherNameRan === true") is False, f"the name's onerror ran on {where}"
    assert page.js_errors == [], f"JS errors on {where}: {page.js_errors}"


@pytest.mark.parametrize(
    "path",
    ["/tenant/{tenant_id}/inventory", "/tenant/{tenant_id}/settings#publishers"],
    ids=["inventory-page", "settings-page"],
)
def test_a_publisher_name_with_markup_shows_as_text(tenant_with_a_markup_named_publisher, member_page, base_url, path):
    page: Page = member_page(ADMIN_EMAIL, tenant_with_a_markup_named_publisher)
    page.goto(base_url + path.format(tenant_id=tenant_with_a_markup_named_publisher))
    page.wait_for_load_state("load")

    _assert_the_name_is_text(page, path)


def test_a_publisher_added_through_the_settings_form_shows_its_name_as_text(
    tenant_with_an_admin, member_page, base_url
):
    """The settings page's add form, with the name typed into its display-name field.

    Adding a publisher fetches its adagents.json once, through the egress gate. FORM_DOMAIN is
    a test publisher the stack routes to its publisher origin, which holds no document for
    this server, so the fetch fails at once and the publisher is stored with the name as
    typed. Verify All is not clicked: it would fetch again.
    """
    page: Page = member_page(ADMIN_EMAIL, tenant_with_an_admin)
    page.goto(f"{base_url}/tenant/{tenant_with_an_admin}/settings#publishers")
    page.get_by_role("button", name="+ Add Publisher").click()
    page.fill("#publisher-domain", FORM_DOMAIN)
    page.fill("#publisher-display-name", NAME)
    with page.expect_event("dialog") as dialog_info:
        page.locator("#add-publisher-form").get_by_role("button", name="Add Publisher").click()
    dialog = dialog_info.value
    shown = dialog.message
    dialog.accept()

    assert shown == "Publisher added successfully", f"the add was refused: {shown!r}"
    expect(page.locator("#add-publisher-modal")).to_be_hidden()
    _assert_the_name_is_text(page, "the settings page after the add")
