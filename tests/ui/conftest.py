"""
UI smoke test fixtures using Playwright.

Requires a running Docker stack (docker compose up -d or ./scripts/test-stack.sh up).
Auth uses test mode: ADCP_AUTH_TEST_MODE=true must be set on the server.
"""

import os

import pytest

from tests.e2e.conftest import e2e_host


def pytest_configure(config):
    config.addinivalue_line("markers", "ui: UI smoke tests (require running Docker stack + Playwright)")


@pytest.fixture(scope="session")
def base_url():
    """Base URL for the running app.

    Host path: localhost:<published-port>. In-network the server is reached by
    service name (ADCP_TEST_HOST=proxy) with no published host port.
    """
    host = e2e_host()
    port = os.environ.get("ADCP_SALES_PORT", "8000")
    return f"http://{host}:{port}"


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_auth_enabled():
    """Enable auth_setup_mode on the default tenant so /test/auth works.

    Seeds the SERVER's database (/adcp). In-network there is no published
    Postgres port, so honor the service-name URL the runner exports
    (E2E_DATABASE_URL=postgres:5432/adcp); on the host path fall back to
    localhost:<published POSTGRES_PORT>.
    """
    db_url = os.environ.get("E2E_DATABASE_URL")
    if not db_url:
        pg_port = os.environ.get("POSTGRES_PORT")
        if not pg_port:
            pytest.skip("neither E2E_DATABASE_URL nor POSTGRES_PORT set — cannot configure test auth")
        db_host = os.environ.get("ADCP_TEST_DB_HOST", "localhost")
        db_url = f"postgresql://adcp_user:secure_password_change_me@{db_host}:{pg_port}/adcp"

    from sqlalchemy import create_engine, text

    # Own the precondition instead of assuming it: the 'default' tenant is
    # boot-seeded by the server, but the bdd suite's e2e scenarios TRUNCATE the
    # server DB, so by the time ui runs it may be gone (suite-order dependent).
    # Re-run the idempotent production seed (single source of truth, race-safe)
    # and fail loud if the tenant still doesn't exist. (#1418)
    from src.core.database.database import init_db

    init_db()

    engine = create_engine(db_url)
    with engine.connect() as conn:
        missing = conn.execute(text("SELECT 1 FROM tenants WHERE tenant_id = 'default'")).first() is None
        if missing:
            raise RuntimeError(
                "ui precondition failed: tenant 'default' absent after init_db() — "
                f"seed targets DATABASE_URL while this fixture configures {db_url}; check the env split"
            )
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
