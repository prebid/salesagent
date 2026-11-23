"""The buyer Accounts and Signing Keys pages are linked from the tenant Settings navigation.

Issue #2609: no page linked to ``accounts.list_accounts``, and the only link to
``signing_keys.list_signing_keys`` was the **Configure** link of the optional "Message
Signing Key" setup task, which ``templates/setup_checklist.html`` renders only while the
task is incomplete. Once a key could sign, nothing linked to the page where keys are
revoked and rotated.

The test signs in as an ordinary tenant admin (``tenant_user_session``), not a super
admin, because the links are for tenant operators. Every page is fetched over HTTP at the
URL ``url_for`` builds for its endpoint, and every link is read from the ``<a>`` tags of
the rendered page.
"""

from __future__ import annotations

import pytest
from flask import url_for
from werkzeug.test import TestResponse

from tests.factories import SigningKeyFactory, TenantFactory, UserFactory
from tests.helpers.admin_session import tenant_user_session
from tests.helpers.signing import deployment_kek

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


def _url(client, endpoint: str, **values: str) -> str:
    """The URL of ``endpoint``, built by the same ``url_for`` the templates call."""
    with client.application.test_request_context():
        return url_for(endpoint, **values)


def _get_ok(client, url: str) -> TestResponse:
    """GET ``url``, which must render without a redirect.

    No redirect is followed: a route that sent this session to the login page would
    answer 302 here, not the 200 the rendered login page gives.
    """
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response


def _links(client, page_validator, url: str) -> list[str]:
    """The ``<a>`` hrefs of the page at ``url``, which must render without a redirect."""
    return page_validator(_get_ok(client, url)).get_links()


def _signed_in_tenant_admin(client):
    tenant = TenantFactory()
    user = UserFactory(tenant=tenant, role="admin")
    tenant_user_session(client, user.email)
    return tenant


class TestSettingsNavigationLinks:
    @pytest.mark.parametrize("with_signing_key", [False, True], ids=["no_key", "key_signs"])
    def test_settings_links_both_pages_whether_or_not_the_signing_key_task_is_complete(
        self, admin_client, factory_session, page_validator, monkeypatch, with_signing_key
    ):
        with deployment_kek(monkeypatch):
            tenant = _signed_in_tenant_admin(admin_client)
            if with_signing_key:
                SigningKeyFactory(tenant=tenant)
                factory_session.commit()
            tenant_id = tenant.tenant_id
            signing_keys = _url(admin_client, "signing_keys.list_signing_keys", tenant_id=tenant_id)
            accounts = _url(admin_client, "accounts.list_accounts", tenant_id=tenant_id)

            # The bug's precondition, read from the page it was on: the checklist links
            # the signing-keys page only while the task is incomplete. Its action URL has
            # no trailing slash, so the comparison ignores one.
            checklist = _links(
                admin_client, page_validator, _url(admin_client, "tenants.setup_checklist", tenant_id=tenant_id)
            )
            checklist_links_signing_keys = any(
                href and href.rstrip("/") == signing_keys.rstrip("/") for href in checklist
            )
            assert checklist_links_signing_keys is not with_signing_key

            # The pages are reached from the dashboard: it links Settings, and Settings
            # links both pages, whatever the task's state.
            dashboard = _links(
                admin_client, page_validator, _url(admin_client, "tenants.dashboard", tenant_id=tenant_id)
            )
            settings_url = _url(admin_client, "tenants.tenant_settings", tenant_id=tenant_id)
            assert settings_url in dashboard

            settings = _links(admin_client, page_validator, settings_url)
            for target in (signing_keys, accounts):
                assert target in settings
                _get_ok(admin_client, target)
