"""Policy Settings page persists to ``tenant.advertising_policy`` (FORK.md divergence 18).

Graded through the real Flask routes against a real database, because the defect was a
column mismatch that no unit of the page could see: the page wrote ``policy_settings``, whose
validator dropped every list it sent, while enforcement (``get_products``) and the
buyer-facing text (``list_authorized_properties``) read ``advertising_policy``. Three
obligations:

* a Policy-page save lands in ``advertising_policy`` and keeps the keys the form does not
  carry (``description``, the baseline lists);
* the page renders what is stored there, verbatim;
* the Settings page's writer merges instead of replacing, so it no longer wipes
  ``require_manual_review``.

All three fail on upstream ``main`` and pass with the fix.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from src.core.database.models import Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "policy_page_tenant"
DESCRIPTION = "Publisher policy, one line for capability discovery."


@pytest.fixture
def policy_env(integration_db):
    """A committed tenant with a partially populated ``advertising_policy``.

    ``IntegrationEnv`` binds the factories to a session; the factory commits, so the
    Flask handler's own session sees the row.
    """
    from tests.factories import TenantFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv() as env:
        TenantFactory(
            tenant_id=TENANT_ID,
            name="Policy Test Publisher",
            subdomain="policypage",
            policy_settings={"enabled": True, "custom_rules": {}},
            advertising_policy={
                "enabled": False,
                "description": DESCRIPTION,
                "default_prohibited_categories": ["Adult and sexual content"],
                "prohibited_categories": ["Old entry"],
            },
        )
        yield env


def stored_tenant(env) -> Tenant:
    """The tenant row, read fresh after whatever the handler's session committed."""
    session = env.get_session()
    session.rollback()
    return session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).one()


def set_advertising_policy(env, **fields) -> None:
    """Merge *fields* into the stored ``advertising_policy`` and commit."""
    session = env.get_session()
    tenant = session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).one()
    tenant.advertising_policy = {**(tenant.advertising_policy or {}), **fields}
    session.commit()


def test_policy_page_save_lands_in_advertising_policy(authenticated_admin_client, policy_env):
    resp = authenticated_admin_client.post(
        f"/tenant/{TENANT_ID}/policy/update",
        data={
            "enabled": "on",
            "require_manual_review": "on",
            "prohibited_categories": "Weapons, guns, ammunition\n\nCosmetic surgery\n",
            "prohibited_tactics": "Auto-play audio\nFake close buttons",
            "prohibited_advertisers": "spam-site.com",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.data[:500]

    tenant = stored_tenant(policy_env)
    saved = dict(tenant.advertising_policy or {})
    assert saved["enabled"] is True
    assert saved["require_manual_review"] is True
    assert saved["prohibited_categories"] == ["Weapons, guns, ammunition", "Cosmetic surgery"]
    assert saved["prohibited_tactics"] == ["Auto-play audio", "Fake close buttons"]
    assert saved["prohibited_advertisers"] == ["spam-site.com"]
    # Keys the form does not carry survive the save.
    assert saved["description"] == DESCRIPTION
    assert saved["default_prohibited_categories"] == ["Adult and sexual content"]
    # Baseline tactics fall back to the code defaults when the tenant has none stored.
    assert "targeting_children_under_13" in saved["default_prohibited_tactics"]
    # The legacy column is left alone.
    assert "prohibited_categories" not in dict(tenant.policy_settings or {})


def test_policy_page_renders_stored_advertising_policy(authenticated_admin_client, policy_env):
    set_advertising_policy(
        policy_env,
        enabled=True,
        require_manual_review=True,
        prohibited_categories=["Cosmetic surgery and body modification"],
        prohibited_tactics=["Auto-play audio"],
        prohibited_advertisers=["spam-site.com"],
    )

    resp = authenticated_admin_client.get(f"/tenant/{TENANT_ID}/policy/")
    assert resp.status_code == 200, resp.data[:500]
    html = resp.data.decode()
    assert "Cosmetic surgery and body modification" in html
    assert "Auto-play audio" in html
    assert "spam-site.com" in html
    # Publisher-written baseline entries are shown verbatim, not title-cased.
    assert "Adult and sexual content" in html
    manual_review_input = re.search(r'<input[^>]*name="require_manual_review"[^>]*>', html)
    assert manual_review_input is not None
    assert "checked" in manual_review_input.group(0)


def test_settings_page_save_keeps_policy_page_fields(policy_env):
    """The Settings form writes only its own keys; the merge keeps the rest."""
    from src.services.policy_service import PolicyService

    set_advertising_policy(policy_env, require_manual_review=True)

    PolicyService.update_policies(
        TENANT_ID,
        {"advertising_policy": {"enabled": True, "prohibited_categories": ["Gambling"]}},
    )

    saved = dict(stored_tenant(policy_env).advertising_policy or {})
    assert saved["prohibited_categories"] == ["Gambling"]
    assert saved["enabled"] is True
    assert saved["require_manual_review"] is True
    assert saved["description"] == DESCRIPTION
