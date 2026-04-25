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

_TENANT_LOGIN_LABELS = (
    "Log in to Dashboard",
    "Log in as Tenant Admin",
    "Log in with Test Credentials",
)


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
    """Authenticate through the tenant login page using test-mode credentials."""
    page.goto(f"/tenant/{tenant_id}/login", wait_until="networkidle")

    for label in _TENANT_LOGIN_LABELS:
        button = page.get_by_role("button", name=label)
        if button.count():
            button.first.click()
            page.wait_for_url(f"**/tenant/{tenant_id}/**", wait_until="networkidle")
            return

    raise AssertionError(f"No tenant admin login button found for tenant {tenant_id}")


def build_admin_test_session(base_url: str, tenant_id: str) -> requests.Session:
    """Create an authenticated requests session for setup helpers."""
    session = requests.Session()
    response = session.post(
        f"{base_url}/test/auth",
        data={
            "email": "test_tenant_admin@example.com",
            "password": "test123",
            "tenant_id": tenant_id,
        },
        allow_redirects=False,
        timeout=20,
    )
    assert response.status_code in {302, 303}, (
        f"/test/auth failed for tenant {tenant_id}: {response.status_code} {response.text[:200]}"
    )
    return session
