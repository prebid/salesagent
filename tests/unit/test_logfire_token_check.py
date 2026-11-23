"""No tenant input reaches this process's Logfire configuration.

``logfire.configure()`` replaces the process-wide configuration. The admin UI's "Test Logfire"
button called it with whatever token was typed, pointing every tenant's telemetry at that
token's project, and ``AIServiceFactory.build`` passed a tenant's saved ``logfire_token`` to
``configure_logfire``. Per-tenant Logfire is gone: the factory no longer reads the tenant token,
and the settings page has neither the token field nor the test button and route.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.services.ai.config import TenantAIConfig
from src.services.ai.factory import AIServiceFactory

TENANT_TOKEN = "pylf_v1_us_tenanttoken0123"
PLATFORM_TOKEN = "pylf_v1_us_platformtoken0123"
REPO = Path(__file__).resolve().parents[2]


def test_only_the_platform_token_configures_the_process(monkeypatch, tenant_row):
    from tests.helpers.settings_injection import inject_integrations

    inject_integrations(monkeypatch, logfire_token=PLATFORM_TOKEN)
    stored = {"provider": "anthropic", "model": "claude-x", "api_key": "tenant-key", "logfire_token": TENANT_TOKEN}
    seller_ai = TenantAIConfig.from_tenant(tenant_row(ai_config=stored))
    with patch("src.services.ai.factory.configure_logfire") as configure:
        AIServiceFactory().build(seller_ai)
    configure.assert_called_once_with(PLATFORM_TOKEN)


def test_a_stored_tenant_token_is_not_read():
    assert "logfire_token" not in TenantAIConfig.model_fields


def test_the_logfire_test_route_is_gone():
    from tests.helpers.admin_session import admin_test_app

    app = admin_test_app()
    assert "settings.test_logfire_connection" not in app.view_functions
    assert not [rule.rule for rule in app.url_map.iter_rules() if "logfire" in rule.rule]


def test_the_settings_page_has_no_tenant_logfire_token():
    """Structural: the field, its placeholder and its test button are not on the page."""
    page = (REPO / "templates" / "tenant_settings.html").read_text(encoding="utf-8")
    assert "logfire_token" not in page
    assert "testLogfireConnection" not in page
