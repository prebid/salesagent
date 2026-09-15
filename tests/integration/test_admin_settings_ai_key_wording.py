"""The Business Rules banners must agree with what the AI features actually run on.

Settings → Business Rules carries three banners (creative review, advertising policy,
product ranking) telling the seller whether AI is available, and the dashboard checklist
carries a matching optional task. All four read the legacy ``tenant.gemini_api_key``
column. The Admin UI's own AI Services form writes ``tenant.ai_config`` and nothing writes
the legacy column any more, so a seller who pasted a key under Integrations → AI Services
kept seeing "Gemini API Key Required" on every Business Rules card.

The flag now comes from ``TenantAIConfig.from_tenant`` (``ai_config`` first, the legacy
column second), the owner the policy check, AI review and ranking resolve through, and the
wording names the requirement (an AI provider key) rather than one vendor.

These tests assert on the rendered HTML because the banner text IS the behaviour a seller
sees; the Integrations section legitimately names "Google Gemini" among the providers, so
the assertions target the banner strings, not the word "Gemini".
"""

import pytest

from tests.factories import TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

REQUIRED = b"AI Provider Key Required"
CONFIGURED = b"AI Provider Configured"
LEGACY_REQUIRED = b"Gemini API Key Required"

# A tenant configured the way the Admin UI writes it: provider, model and key on
# `ai_config`, nothing in the legacy `gemini_api_key` column.
_ADMIN_UI_AI_CONFIG = {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "sk-ant-tenant"}

# What `src/admin/blueprints/settings.py` stores when the seller leaves the key field
# blank, and tells them "AI features will be disabled" as it does.
_KEYLESS_AI_CONFIG = {"provider": "google", "model": "gemini-2.0-flash"}


def _settings_page(client, tenant_id: str) -> bytes:
    response = client.get(f"/tenant/{tenant_id}/settings", follow_redirects=True)
    assert response.status_code == 200
    return response.data


class TestBusinessRulesBannersFollowTheTenantAIConfiguration:
    def test_no_key_shows_a_provider_neutral_banner(self, authenticated_admin_session):
        with IntegrationEnv(tenant_id="aikw_none"):
            TenantFactory(tenant_id="aikw_none")
            page = _settings_page(authenticated_admin_session, "aikw_none")
        assert REQUIRED in page
        assert CONFIGURED not in page
        assert LEGACY_REQUIRED not in page

    def test_ai_config_key_from_the_admin_ui_counts_as_configured(self, authenticated_admin_session):
        """The regression: configured through Integrations → AI Services, told a key was still required."""
        with IntegrationEnv(tenant_id="aikw_uicfg"):
            TenantFactory(tenant_id="aikw_uicfg", ai_config=_ADMIN_UI_AI_CONFIG)
            page = _settings_page(authenticated_admin_session, "aikw_uicfg")
        assert CONFIGURED in page
        assert REQUIRED not in page
        assert LEGACY_REQUIRED not in page

    def test_keyless_ai_config_row_is_not_a_configuration(self, authenticated_admin_session):
        with IntegrationEnv(tenant_id="aikw_keyless"):
            TenantFactory(tenant_id="aikw_keyless", ai_config=_KEYLESS_AI_CONFIG)
            page = _settings_page(authenticated_admin_session, "aikw_keyless")
        assert REQUIRED in page
        assert CONFIGURED not in page

    def test_legacy_gemini_column_still_counts(self, authenticated_admin_session, monkeypatch):
        """`Tenant.gemini_api_key` encrypts on write, so the legacy case needs an ENCRYPTION_KEY."""
        from cryptography.fernet import Fernet

        monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
        with IntegrationEnv(tenant_id="aikw_legacy"):
            TenantFactory(tenant_id="aikw_legacy", gemini_api_key="legacy-tenant-key")
            page = _settings_page(authenticated_admin_session, "aikw_legacy")
        assert CONFIGURED in page
        assert REQUIRED not in page
