"""The admin pages say AI is missing exactly when the AI feature would not run.

Three features carry an "AI Configuration Required" notice: creative review and product
ranking on the settings page, and the advertising policy check on the Policy page, its one
editor (#2697). They used to test ``tenant.gemini_api_key``, so a seller who configured AI in
the Admin UI (``tenants.ai_config``) was told it needed a Gemini key while every AI step ran
for it. Each notice now asks the feature's own question (``tenant_ai_enabled``): ranking and
review run with the platform's credential as fallback, the policy check only on the seller's
own.

Integration rather than BDD: the settings page is the admin UI, not a buyer wire.
"""

from __future__ import annotations

import pytest

from tests.factories import TenantFactory
from tests.helpers.settings_injection import ADMIN_UI_AI_CONFIG, KEYLESS_AI_CONFIG, PlatformAICredential

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_NOTICE = "AI Configuration Required"


def _notices(client, ai_config, platform_key):
    """How many sections say AI is missing: on the rendered settings page, and on the Policy page."""
    tenant = TenantFactory(ai_config=ai_config)
    counts = []
    with PlatformAICredential(platform_key):
        for page in ("settings", "policy/"):
            response = client.get(f"/tenant/{tenant.tenant_id}/{page}", follow_redirects=True)
            assert response.status_code == 200, (page, response.status_code)
            counts.append(response.get_data(as_text=True).count(_NOTICE))
    return tuple(counts)


@pytest.mark.parametrize(
    ("ai_config", "platform_key", "expected"),
    [
        (ADMIN_UI_AI_CONFIG, None, (0, 0)),
        (None, None, (2, 1)),
        (None, "platform-key", (0, 1)),
        (KEYLESS_AI_CONFIG, "platform-key", (2, 1)),
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
