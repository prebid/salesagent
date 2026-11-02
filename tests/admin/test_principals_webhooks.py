"""Integration tests for principals-blueprint webhook registration (salesagent-tayg).

POST /tenant/<tid>/principals/<pid>/webhooks/register carried three defects,
each pinned by one class here:

1. The route constructs ``PushNotificationConfig(config_id=..., auth_type=...,
   auth_config=...)`` but the model's columns are ``id`` /
   ``authentication_type`` / ``authentication_token`` — every registration that
   passes URL validation dies with ``TypeError``, which the blanket ``except``
   masks into an error flash. Nothing is ever persisted.

2. The duplicate pre-check (same tenant/principal/url) has no unique index
   behind it — the model's only key is the ``id`` PK — so two racing
   registrations both pass the check and both commit. Duplicate active rows
   mean duplicate webhook deliveries for every notification, silently.

3. GH #1894: an HMAC registration wrote the operator's shared secret to
   ``push_notification_configs.webhook_secret`` under the non-canonical scheme
   spelling ``"hmac_sha256"``. Neither is what a sender reads —
   ``webhook_delivery_service`` hands ``deliver_webhook`` the pair
   ``(authentication_type, authentication_token)``
   (``src/services/webhook_delivery_service.py:611-615``), and the seam matches
   the scheme against the pinned ``AuthenticationScheme`` without folding
   casing. So the row was accepted, shown to the operator as signed, and
   delivered UNSIGNED. ``TestRegisterWebhookHmacMapping`` grades the corrected
   mapping and that the correction still holds end to end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from adcp.types import AuthenticationScheme
from sqlalchemy import select

from src.admin.blueprints.principals import NO_AUTHENTICATION
from src.core.database.models import PushNotificationConfig
from src.core.security.egress.response import OutboundResult
from src.core.security.webhook_egress import deliver_webhook
from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory
from tests.helpers import (
    admin_auth_session,
    assert_signature_verifies_over_wire_body,
    concurrent_commit_in_write_window,
    operator_answer,
)
from tests.helpers.webhook_credential_refusal import SHORT_CREDENTIAL

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

WEBHOOK_URL = "https://example.com/webhook"


def _register(
    client,
    tenant_id: str,
    principal_id: str,
    url: str = WEBHOOK_URL,
    *,
    auth_type: str = NO_AUTHENTICATION,
    secret: str | None = None,
):
    """POST the registration form exactly as the rendered page posts it.

    ``auth_type`` defaults to the blueprint's own ``NO_AUTHENTICATION`` constant
    and the HMAC callers pass an ``AuthenticationScheme`` member, because the
    template renders both from those same two sources
    (``templates/webhook_management.html``). A test that spelled its own
    ``"hmac_sha256"`` literal here would be posting a value no browser can
    produce — and would be pinning GH #1894's spelling from the outside.
    """
    data = {"url": url, "auth_type": auth_type}
    if secret is not None:
        data["hmac_secret"] = secret
    return client.post(
        f"/tenant/{tenant_id}/principals/{principal_id}/webhooks/register",
        data=data,
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


class _HeadersAsReceived(dict):
    """A header map that answers ``None`` for a header that is not there.

    ``tests.helpers.hmac_assertions`` is written against an origin's
    ``http.client.HTTPMessage``, where an absent header reads as ``None`` — and
    it depends on that: an absent signature must be reported as "this delivery
    went out UNSIGNED", not as a ``KeyError`` that reads like a bug in the test.
    Captured headers arrive here as a plain dict, so this restores the one
    behaviour the shared grader needs.
    """

    def __missing__(self, key: str) -> None:
        return None


@dataclass(frozen=True)
class _DialledRequest:
    """The bytes and headers the egress seam actually handed to ``send``.

    Shaped like the ``OriginRequest`` the shared HMAC grader takes, so the
    signature is verified by the SAME code that grades every other webhook
    signature in this repo rather than by a seventh hand-rolled recompute.
    """

    headers: _HeadersAsReceived
    body: bytes


class TestRegisterWebhookHmacMapping:
    """An HMAC registration lands in the columns the SENDER reads, spelled the
    way the PINNED enum spells it.

    Both halves are GH #1894, and neither is cosmetic:

    * the credential goes to ``authentication_token``. The delivery seam signs
      from ``(authentication_type, authentication_token)`` and nothing else —
      ``webhook_delivery_service`` passes exactly that pair to
      ``deliver_webhook`` (``src/services/webhook_delivery_service.py:611-615``),
      and ``push_notification_configs.webhook_secret`` has no reader left in
      ``src/`` at all (its only occurrences are the column, its redaction in
      ``__repr__``, and the repository parameter this route passes ``None``).
      A secret written there is a registration the operator is told is signed
      and that goes out unsigned.
    * the scheme is ``AuthenticationScheme.HMAC_SHA256``. The seam matches the
      pinned enum without folding casing (``_authentication_or_refusal``,
      ``src/core/security/webhook_egress.py``), so a row spelled
      ``"hmac_sha256"`` refuses at delivery. That spelling is what
      ``alembic/versions/a1f4c7d92b30_normalize_webhook_auth_scheme_spellings.py``
      exists to clean up; this route must not create more of it.
    """

    SECRET = "s" * 32
    SCHEME = AuthenticationScheme.HMAC_SHA256

    def _register_hmac(self, client, tenant, principal, secret: str | None = None, scheme: str | None = None):
        return _register(
            client,
            tenant.tenant_id,
            principal.principal_id,
            auth_type=self.SCHEME if scheme is None else scheme,
            secret=self.SECRET if secret is None else secret,
        )

    def test_hmac_secret_lands_in_authentication_token(self, admin_client, factory_session):
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = self._register_hmac(admin_client, tenant, principal)

        assert operator_answer(admin_client, resp)[2] == [("success", "Webhook registered successfully")]
        factory_session.expire_all()
        rows = _active_rows(factory_session, tenant.tenant_id, principal.principal_id)
        assert len(rows) == 1
        assert (rows[0].authentication_type, rows[0].authentication_token) == (self.SCHEME, self.SECRET)

        # The column with no reader must stay empty, and this assertion says why
        # rather than only what: a future change that routes the credential back
        # to webhook_secret leaves a row that looks signed to whoever registered
        # it and delivers unsigned to whoever receives it (GH #1894).
        assert rows[0].webhook_secret is None, (
            "the shared secret was written to push_notification_configs.webhook_secret, "
            "a column no sender in src/ reads — the registration would be shown to the "
            "operator as signed and delivered UNSIGNED (GH #1894)"
        )

    def test_the_registered_row_delivers_signed(self, admin_client, factory_session, monkeypatch):
        """The stored row, read the way the sender reads it, produces signed bytes.

        Not a second spelling of the column assertion above. This takes the row
        the admin form just wrote, hands the seam the SAME pair
        ``webhook_delivery_service`` hands it, and grades the bytes that would
        cross the socket. Move the credential back to ``webhook_secret`` and
        ``credentials`` arrives ``None``: the seam returns a ``refused_auth``
        outcome instead of dialling, and this fails naming the unsigned delivery
        — which is the harm in GH #1894, one layer below the column names.

        ``send`` is replaced rather than the URL made unreachable, so nothing is
        dialled and the assertion is on what the seam PREPARED, not on a network.
        """
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)
        self._register_hmac(admin_client, tenant, principal)
        factory_session.expire_all()
        (row,) = _active_rows(factory_session, tenant.tenant_id, principal.principal_id)

        dialled: dict[str, Any] = {}

        def _capture(url: str, *, content: bytes, headers: dict[str, str], **kwargs: Any) -> OutboundResult:
            dialled["headers"] = _HeadersAsReceived(headers)
            dialled["body"] = content
            return OutboundResult(http_status=200, headers={}, content=b"", attempts=1, duration_seconds=0.0)

        monkeypatch.setattr("src.core.security.webhook_egress.send", _capture)

        outcome = deliver_webhook(
            row.url,
            {"notification_type": "test"},
            scheme=row.authentication_type,
            credentials=row.authentication_token,
        )

        assert outcome.kind == "delivered", (
            f"the seam refused the row this admin form just registered ({outcome.kind}: {outcome.detail}) — "
            f"the operator was told the webhook was registered and nothing will ever be delivered to it"
        )
        assert_signature_verifies_over_wire_body(_DialledRequest(dialled["headers"], dialled["body"]), self.SECRET)

    def test_non_canonical_scheme_spelling_is_refused(self, admin_client, factory_session):
        """``hmac_sha256`` is refused at registration, not folded onto the pin.

        The route deliberately translates nothing (``principals.py``): a
        route-side lookup table is how a fifth spelling reached the database
        once already. The migration cleans up the rows that exist; this pins
        that no new one is written — including through this form, which is one
        of the two surfaces that wrote them.
        """
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = self._register_hmac(admin_client, tenant, principal, scheme="hmac_sha256")

        status, location, flashed = operator_answer(admin_client, resp)
        assert (status, location) == (
            302,
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks",
        )
        assert [category for category, _ in flashed] == ["error"]
        factory_session.expire_all()
        assert _active_rows(factory_session, tenant.tenant_id, principal.principal_id) == []

    def test_short_hmac_secret_is_rejected(self, admin_client, factory_session):
        """A secret one character under the pinned minimum is refused, and stored nowhere.

        ``SHORT_CREDENTIAL`` is 31 characters — the boundary — so what refuses is
        the pinned ``credentials`` ``minLength: 32``
        (``core/push-notification-config.json`` @3.1.1) and not some hand-written
        "looks too short" rule. The scheme is the canonical one, so the refusal
        cannot be the scheme's doing either.

        The flash SENTENCE is graded once, on the integration surface, by
        ``tests.helpers.webhook_credential_refusal.assert_admin_flash_refuses_the_credential``
        — that helper is the cross-surface owner of what "refused the credential"
        means, and restating its wording here would be a second copy to drift.
        What this grades is what belongs to this module: the operator is answered
        with an error rather than a success, the secret is not echoed back into a
        page, and NOTHING was persisted.
        """
        tenant = TenantFactory()
        principal = PrincipalFactory(tenant=tenant)
        admin_auth_session(admin_client, tenant.tenant_id)

        resp = self._register_hmac(admin_client, tenant, principal, secret=SHORT_CREDENTIAL)

        status, location, flashed = operator_answer(admin_client, resp)
        assert (status, location) == (
            302,
            f"/tenant/{tenant.tenant_id}/principals/{principal.principal_id}/webhooks",
        )
        assert [category for category, _ in flashed] == ["error"]
        assert all(SHORT_CREDENTIAL not in message for _, message in flashed), (
            f"the refusal echoed the operator's shared secret back into the page: {flashed}"
        )
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
