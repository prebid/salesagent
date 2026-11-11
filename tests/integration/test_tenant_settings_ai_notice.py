"""The tenant settings page says AI is missing exactly when the AI feature would not run.

Three sections carry an "AI Configuration Required" notice: creative review, advertising
policy and product ranking. They used to test ``tenant.gemini_api_key``, so a seller who
configured AI in the Admin UI (``tenants.ai_config``) was told it needed a Gemini key while
every AI step ran for it. Each notice now asks the feature's own question
(``tenant_ai_enabled``): ranking and review run with the platform's credential as fallback,
the policy check only on the seller's own.

Integration rather than BDD: the settings page is the admin UI, not a buyer wire.
"""

from __future__ import annotations

import pytest

from tests.factories import TenantFactory
from tests.helpers.settings_injection import PlatformAICredential

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_NOTICE = "AI Configuration Required"

_SELLER_AI_CONFIG = {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "sk-ant-seller"}


def _notices(client, ai_config, platform_key):
    """How many sections of the rendered settings page say AI is missing."""
    tenant = TenantFactory(ai_config=ai_config)
    with PlatformAICredential(platform_key):
        response = client.get(f"/tenant/{tenant.tenant_id}/settings", follow_redirects=True)
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True).count(_NOTICE)


@pytest.mark.parametrize(
    ("ai_config", "platform_key", "expected"),
    [
        (_SELLER_AI_CONFIG, None, 0),
        (None, None, 3),
        (None, "platform-key", 1),
        ({"provider": "anthropic", "model": "claude-sonnet-4-20250514"}, "platform-key", 3),
    ],
    ids=[
        "seller-ai-config-no-platform-key",
        "nothing-configured-no-platform-key",
        "nothing-configured-platform-key-only-policy-missing",
        "keyless-row-is-unusable-even-with-a-platform-key",
    ],
)
def test_notices_follow_what_each_ai_feature_would_run_on(
    authenticated_admin_session, bound_factory_session, ai_config, platform_key, expected
):
    assert _notices(authenticated_admin_session, ai_config, platform_key) == expected
