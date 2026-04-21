"""
UI smoke test fixtures using Playwright.

Requires a running Docker stack (docker compose up -d or ./scripts/test-stack.sh up).
Auth uses test mode: ADCP_AUTH_TEST_MODE=true must be set on the server.
"""

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "ui: UI smoke tests (require running Docker stack + Playwright)")


@pytest.fixture(scope="session")
def base_url():
    """Base URL for the running app."""
    port = os.environ.get("ADCP_SALES_PORT", "8000")
    return f"http://localhost:{port}"


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_auth_enabled():
    """Enable auth_setup_mode on the default tenant so /test/auth works.

    The app's internal DB URL uses Docker-internal hostnames (postgres:5432),
    so we construct a localhost URL using the exposed POSTGRES_PORT.
    """
    pg_port = os.environ.get("POSTGRES_PORT")
    if not pg_port:
        pytest.skip("POSTGRES_PORT not set — cannot configure test auth")

    db_url = f"postgresql://adcp_user:secure_password_change_me@localhost:{pg_port}/adcp"

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        conn.execute(text("UPDATE tenants SET auth_setup_mode = true WHERE tenant_id = 'default'"))
        # Configure as GAM tenant so inventory tree UI paths are exercised
        conn.execute(text("UPDATE tenants SET ad_server = 'google_ad_manager' WHERE tenant_id = 'default'"))
        # Seed one ad unit so inventory_synced=True and Browse Ad Units is enabled
        conn.execute(
            text(
                "INSERT INTO gam_inventory"
                " (tenant_id, inventory_type, inventory_id, name, path, status,"
                "  inventory_metadata, last_synced, created_at, updated_at)"
                " VALUES ('default', 'ad_unit', 'smoke-au-001', 'Smoke Test Ad Unit',"
                "  '[\"Smoke Test Ad Unit\"]'::jsonb, 'ACTIVE',"
                '  \'{"parent_id": null, "has_children": false, "sizes":'
                '  [{"width": 300, "height": 250}]}\'::jsonb,'
                "  NOW(), NOW(), NOW())"
                " ON CONFLICT DO NOTHING"
            )
        )
        conn.commit()
    engine.dispose()


@pytest.fixture
def authenticated_page(page, base_url):
    """Log in via the test login page and return the authenticated page."""
    page.goto(f"{base_url}/test/login")
    page.wait_for_load_state("domcontentloaded")

    # Inject tenant_id into the last form (needed for multi-tenant e2e stacks)
    page.evaluate("""() => {
        const forms = document.querySelectorAll('form[action="/test/auth"]');
        const form = forms[forms.length - 1];
        if (form && !form.querySelector('input[name="tenant_id"]')) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'tenant_id';
            input.value = 'default';
            form.appendChild(input);
        }
    }""")

    buttons = page.locator('form[action="/test/auth"] button[type="submit"]')
    buttons.last.click()
    page.wait_for_load_state("networkidle")

    # Collect JS errors for assertions
    js_errors = []
    page.on("pageerror", lambda err: js_errors.append(str(err)))
    page.js_errors = js_errors  # type: ignore[attr-defined]

    return page
