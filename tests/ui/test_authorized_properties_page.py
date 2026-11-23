"""Browser coverage for the authorized-properties pages.

WHY A BROWSER TEST AND NOT ONLY A RENDER ASSERTION. The Flask-client tests in
tests/admin assert that the route returns 200 and that a property's name is in the body.
That is the right level for "the template exists and carries the route's data", and it is
exactly the level that let this page ship broken for as long as it did: the template had
never been written, the route's broad ``except Exception`` turned TemplateNotFound into a
flash and a 302, and nothing in the UI linked to the page, so nobody arrived to notice.

A page is a thing an operator REACHES and READS. These grade that: the link exists where
the feature lives, following it lands on the page rather than a redirect, the page renders
the seeded property, and no JS error fires on the way.
"""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.ui


class TestAuthorizedPropertiesPage:
    """The authorized-properties page is reachable, renders, and carries its data."""

    def test_page_is_reachable_from_publishers_tab(self, authenticated_page: Page, base_url):
        """An operator can GET THERE — the link exists and leads to the page.

        Reachability is the assertion the render-level tests cannot make. The whole
        authorized-properties family had no entry point anywhere in the UI, so every page in
        it was dead to an operator whatever the route did.
        """
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/inventory")
        page.wait_for_load_state("networkidle")

        link = page.get_by_role("link", name="Authorized Properties")
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle")

        assert "/authorized-properties" in page.url, f"link did not lead to the page: {page.url}"
        assert "/login" not in page.url, f"redirected to login: {page.url}"

    def test_page_renders_without_falling_back_to_the_dashboard(self, authenticated_page: Page, base_url):
        """The page itself renders — no TemplateNotFound swallowed into a redirect.

        The tell for the original defect was landing on the tenant dashboard with an error
        flash, which is what the route does when render_template raises.
        """
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/authorized-properties")
        page.wait_for_load_state("networkidle")

        assert "/authorized-properties" in page.url, f"bounced off the page to {page.url}"
        expect(page.get_by_role("heading", name="Authorized Properties")).to_be_visible()

    def test_page_loads_without_js_errors(self, authenticated_page: Page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/authorized-properties")
        page.wait_for_load_state("networkidle")

        assert page.js_errors == [], f"JS errors on the authorized-properties page: {page.js_errors}"
