"""Integration tests for principals-blueprint webhook registration (salesagent-tayg).

POST /tenant/<tid>/principals/<pid>/webhooks/register has two defects, each
pinned by one test here:

1. The route constructs ``PushNotificationConfig(config_id=..., auth_type=...,
   auth_config=...)`` but the model's columns are ``id`` /
   ``authentication_type`` / ``authentication_token`` — every registration that
   passes URL validation dies with ``TypeError``, which the blanket ``except``
   masks into an error flash. Nothing is ever persisted.

2. The duplicate pre-check (same tenant/principal/url) has no unique index
   behind it — the model's only key is the ``id`` PK — so two racing
   registrations both pass the check and both commit. Duplicate active rows
   mean duplicate webhook deliveries for every notification, silently.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.database.models import PushNotificationConfig
from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory
from tests.helpers import admin_auth_session, concurrent_commit_in_write_window, operator_answer

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

WEBHOOK_URL = "https://example.com/webhook"


def _register(client, tenant_id: str, principal_id: str, url: str = WEBHOOK_URL):
    return client.post(
        f"/tenant/{tenant_id}/principals/{principal_id}/webhooks/register",
        data={"url": url, "auth_type": "none"},
        follow_redirects=False,
    )


def _active_rows(session, tenant_id: str, principal_id: str, url: str = WEBHOOK_URL):
    return session.scalars(
        select(PushNotificationConfig).filter_by(
            tenant_id=tenant_id, principal_id=principal_id, url=url, is_active=True
        )
    ).all()


class TestRegisterWebhookPersists:
    """A valid registration persists exactly one active config row."""

    def test_register_persists_one_active_config(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        answer = operator_answer(admin_client, _register(admin_client, tenant.tenant_id, principal.principal_id))

        assert answer == (
            302,
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks",
            [("success", "Webhook registered successfully")],
        )
        factory_session.expire_all()
        rows = _active_rows(factory_session, tenant.tenant_id, principal.principal_id)
        assert len(rows) == 1


class TestRegisterWebhookHmacMapping:
    """hmac_sha256 registrations land the secret in webhook_secret (the column
    WebhookDeliveryService signs from), not in authentication_token."""

    SECRET = "s" * 32

    def test_hmac_secret_lands_in_webhook_secret(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = admin_client.post(
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks/register",
            data={"url": WEBHOOK_URL, "auth_type": "hmac_sha256", "hmac_secret": self.SECRET},
            follow_redirects=False,
        )

        assert operator_answer(admin_client, resp)[2] == [("success", "Webhook registered successfully")]
        factory_session.expire_all()
        rows = _active_rows(factory_session, tenant.tenant_id, principal.principal_id)
        assert len(rows) == 1
        assert (rows[0].authentication_type, rows[0].webhook_secret, rows[0].authentication_token) == (
            "hmac_sha256",
            self.SECRET,
            None,
        )

    def test_short_hmac_secret_is_rejected(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = admin_client.post(
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks/register",
            data={"url": WEBHOOK_URL, "auth_type": "hmac_sha256", "hmac_secret": "short"},
            follow_redirects=False,
        )

        assert operator_answer(admin_client, resp)[2] == [("error", "HMAC secret must be at least 32 characters")]
        factory_session.expire_all()
        assert _active_rows(factory_session, tenant.tenant_id, principal.principal_id) == []


class TestDeleteWebhook:
    """POST delete removes the row entirely (the management page lists inactive
    rows too, so a soft delete would leave an undeletable ghost)."""

    def test_delete_removes_row(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        cfg = PushNotificationConfigFactory(tenant=tenant, principal=principal, url=WEBHOOK_URL)
        config_id = cfg.id
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = admin_client.post(
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks/{config_id}/delete",
            follow_redirects=False,
        )

        assert operator_answer(admin_client, resp)[2] == [("success", "Webhook deleted successfully")]
        factory_session.expire_all()
        assert factory_session.scalars(select(PushNotificationConfig).filter_by(id=config_id)).first() is None

    def test_delete_missing_row_answers_not_found(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = admin_client.post(
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks/nope/delete",
            follow_redirects=False,
        )

        assert operator_answer(admin_client, resp)[2] == [("error", "Webhook not found")]


class TestToggleWebhook:
    """POST toggle flips is_active and reports the new state."""

    def test_toggle_deactivates_active_row(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        cfg = PushNotificationConfigFactory(tenant=tenant, principal=principal, url=WEBHOOK_URL, is_active=True)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = admin_client.post(
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks/{cfg.id}/toggle",
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert resp.get_json() == {"success": True, "is_active": False}
        factory_session.expire_all()
        refreshed = factory_session.scalars(select(PushNotificationConfig).filter_by(id=cfg.id)).first()
        assert refreshed.is_active is False

    def test_toggle_missing_row_is_404(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = admin_client.post(
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks/nope/toggle",
            follow_redirects=False,
        )

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Webhook not found"}


class TestRegisterWebhookDuplicateRace:
    """Same-URL admin race: winner and loser answer identically, one active row.

    The conflicting ADMIN registration commits from an independent session
    inside the handler's check-then-write window (after its pre-check read,
    before its write lands), so the pre-check cannot have seen it. Both admin
    registrations of one URL compute the same deterministic config id, so the
    primary key — not the pre-check — decides the race. (Scoped invariant:
    protocol-path configs with buyer-chosen ids are deliberately NOT covered —
    AdCP 3.1.1 keys configs by id and is silent on URL uniqueness.)

    The route's session comes from PushNotificationConfigUoW, so the write
    window is patched at the UoW module, not the blueprint.
    """

    def test_winner_and_loser_get_the_same_answer(self, admin_client, factory_session):
        from src.core.database.repositories import uow as uow_module
        from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository

        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        def commit_conflicting_row():
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=WEBHOOK_URL,
                id=PushNotificationConfigRepository.admin_config_id(
                    tenant.tenant_id, principal.principal_id, WEBHOOK_URL
                ),
            )

        with concurrent_commit_in_write_window(uow_module, commit_conflicting_row):
            loser = operator_answer(admin_client, _register(admin_client, tenant.tenant_id, principal.principal_id))

        winner = operator_answer(admin_client, _register(admin_client, tenant.tenant_id, principal.principal_id))

        assert winner == (
            302,
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks",
            [("warning", "Webhook URL already registered for this principal")],
        )
        assert loser == winner
        factory_session.expire_all()
        rows = _active_rows(factory_session, tenant.tenant_id, principal.principal_id)
        assert len(rows) == 1
