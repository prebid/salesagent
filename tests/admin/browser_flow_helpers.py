"""Helpers for browser-driven admin E2E tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import requests

playwright_sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Install playwright to run browser-based admin E2E tests.",
)

Page = playwright_sync_api.Page
sync_playwright = playwright_sync_api.sync_playwright


@contextmanager
def browser_page(base_url: str) -> Iterator[Page]:
    """Open a headless Chromium page rooted at the admin base URL."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:
            pytest.fail(f"Playwright Chromium is not available: {exc}")

        context = browser.new_context(base_url=base_url)
        page = context.new_page()
        page.set_default_timeout(15000)
        try:
            yield page
        finally:
            context.close()
            browser.close()


def login_as_tenant_admin(page: Page, tenant_id: str) -> None:
    """Give *page* an authenticated admin session for *tenant_id*.

    Signs the session rather than driving a login form. The form this used to click was
    served by /test/login and backed by a default password — a route composed only under
    ADCP_AUTH_TEST_MODE, so what it exercised was a login path no deployment has. It is
    deleted; a test that needs a session states one (tests/helpers/admin_session).
    """
    from urllib.parse import urlsplit

    from tests.helpers.admin_session import admin_session_cookie

    # Navigate first: the context carries the base URL but exposes no getter for it, and a
    # cookie needs the host it belongs to. The landing request is anonymous and harmless.
    page.goto("/", wait_until="domcontentloaded")
    host = urlsplit(page.url).hostname or "localhost"
    page.context.add_cookies(
        [{"name": "session", "value": admin_session_cookie(tenant_id), "domain": host, "path": "/"}]
    )
    page.goto(f"/tenant/{tenant_id}/", wait_until="networkidle")


def build_admin_test_session(base_url: str, tenant_id: str) -> requests.Session:
    """Create an authenticated requests session for setup helpers.

    Signs the admin session rather than posting a default password to a login route that
    only exists when a flag composed it (see tests/helpers/admin_session).
    """
    from tests.helpers.admin_session import authenticate_http_session

    return authenticate_http_session(requests.Session(), base_url, tenant_id)
