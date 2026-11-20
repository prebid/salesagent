"""The buyer Accounts and Signing Keys pages are linked from the tenant Settings navigation.

Issue #2609: no page linked to ``accounts.list_accounts``, and the only link to
``signing_keys.list_signing_keys`` was the action URL of the optional "Message Signing
Key" setup task, which the checklist renders only while the task is incomplete. Once a
key could sign, nothing linked to the page where keys are revoked and rotated.

Each test signs in as an ordinary tenant admin (``tenant_user_session``), not a super
admin, because the links are for tenant operators, and follows the links the way an
operator would: dashboard, then Settings, then the two pages.
"""

from __future__ import annotations

import re

import pytest
from flask import url_for

from src.services.setup_checklist_service import SetupChecklistService
from tests.factories import SigningKeyFactory, TenantFactory, UserFactory
from tests.helpers.admin_session import tenant_user_session
from tests.helpers.signing import deployment_kek

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


def _expected_hrefs(client, tenant_id: str) -> dict[str, str]:
    """The two link targets, built by the same ``url_for`` the template calls."""
    with client.application.test_request_context():
        return {
            "accounts": url_for("accounts.list_accounts", tenant_id=tenant_id),
            "signing_keys": url_for("signing_keys.list_signing_keys", tenant_id=tenant_id),
        }


def _hrefs(html: str) -> set[str]:
    return set(re.findall(r'href="([^"]*)"', html))


def _signed_in_tenant_admin(client):
    tenant = TenantFactory()
    user = UserFactory(tenant=tenant, role="admin")
    tenant_user_session(client, user.email)
    return tenant


def _settings_links(client, tenant_id: str) -> set[str]:
    response = client.get(f"/tenant/{tenant_id}/settings")
    assert response.status_code == 200, response.status_code
    return _hrefs(response.get_data(as_text=True))


def _signing_key_task(tenant_id: str) -> dict:
    status = SetupChecklistService(tenant_id).get_setup_status()
    tasks = status["critical"] + status["recommended"] + status["optional"]
    return next(task for task in tasks if task["key"] == "signing_key")


class TestSettingsNavigationLinks:
    def test_settings_links_to_buyer_accounts_and_signing_keys(self, admin_client, factory_session):
        tenant = _signed_in_tenant_admin(admin_client)
        expected = _expected_hrefs(admin_client, tenant.tenant_id)

        links = _settings_links(admin_client, tenant.tenant_id)

        assert expected["accounts"] in links
        assert expected["signing_keys"] in links

    def test_signing_keys_stays_linked_once_the_setup_task_is_complete(
        self, admin_client, factory_session, monkeypatch
    ):
        with deployment_kek(monkeypatch):
            tenant = _signed_in_tenant_admin(admin_client)
            SigningKeyFactory(tenant=tenant)
            factory_session.commit()
            # The precondition the bug needed: the task is done, so the checklist no
            # longer renders its Configure link.
            assert _signing_key_task(tenant.tenant_id)["is_complete"] is True

            expected = _expected_hrefs(admin_client, tenant.tenant_id)
            links = _settings_links(admin_client, tenant.tenant_id)

        assert expected["signing_keys"] in links
        assert expected["accounts"] in links

    def test_both_pages_are_reachable_from_the_dashboard(self, admin_client, factory_session):
        tenant = _signed_in_tenant_admin(admin_client)
        expected = _expected_hrefs(admin_client, tenant.tenant_id)
        with admin_client.application.test_request_context():
            settings_href = url_for("tenants.tenant_settings", tenant_id=tenant.tenant_id)

        dashboard = admin_client.get(f"/tenant/{tenant.tenant_id}", follow_redirects=True)
        assert dashboard.status_code == 200, dashboard.status_code
        assert settings_href in _hrefs(dashboard.get_data(as_text=True))

        links = _settings_links(admin_client, tenant.tenant_id)
        for href in (expected["accounts"], expected["signing_keys"]):
            assert href in links
            assert admin_client.get(href, follow_redirects=True).status_code == 200, href
