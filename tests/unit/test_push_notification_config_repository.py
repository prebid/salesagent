"""PushNotificationConfigRepository.build_detached — the transient auth carrier.

``build_detached`` is the fallback arm of the delivery-webhook push-config
decision: when a principal has no registered config for a ``reporting_webhook``
URL, the scheduler still needs an object to carry the auth policy into the
sender. That object's ``authentication_token`` becomes the outbound
``Authorization`` header (``protocol_webhook_service``), so a dropped or renamed
field silently downgrades a signed webhook to an unsigned one.

These tests are the mechanism that reddens when that happens — the method
previously shipped with no test at all, and its "must not be persisted" contract
lived only in a docstring. The complementary check that the *scheduler* actually
passes the auth policy through from ``reporting_webhook`` lives in
``tests/integration/test_delivery_webhooks_integration.py``; this file pins the
builder itself.
"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository

_URL = "https://example.com/webhook"


def _repo(tenant_id: str = "t1") -> PushNotificationConfigRepository:
    """Repository over a REAL unbound session — no engine, no database needed.

    Deliberately not a MagicMock: a mock's ``.add()`` is a no-op, so the
    transient assertion below would pass even if ``build_detached`` started
    persisting the carrier. Verified by mutation — with a mock session that test
    stayed green when the method was changed to call ``session.add()``. A real
    unbound Session tracks instance state without ever touching a database.
    """
    return PushNotificationConfigRepository(Session(), tenant_id)


class TestBuildDetachedCarriesEveryField:
    """Every field the sender relies on survives the builder."""

    def test_carries_all_seven_fields(self):
        cfg = _repo().build_detached(
            "p1",
            _URL,
            config_id="temp_mb-1",
            authentication_type="Bearer",
            authentication_token="secret-token",
        )

        assert cfg.id == "temp_mb-1"
        assert cfg.tenant_id == "t1"
        assert cfg.principal_id == "p1"
        assert cfg.url == _URL
        assert cfg.authentication_type == "Bearer"
        assert cfg.authentication_token == "secret-token"
        assert cfg.is_active is True

    def test_tenant_id_comes_from_repository_scope_not_an_argument(self):
        """The tenant is the repo's scope — callers cannot pass a foreign one."""
        cfg = _repo("other-tenant").build_detached("p1", _URL, config_id="c")

        assert cfg.tenant_id == "other-tenant"

    def test_auth_fields_default_to_none_for_an_unsigned_webhook(self):
        """An unsigned reporting_webhook yields no credentials — not a stale default."""
        cfg = _repo().build_detached("p1", _URL, config_id="c")

        assert cfg.authentication_type is None
        assert cfg.authentication_token is None


class TestBuildDetachedIsNeverPersisted:
    """The 'transient carrier, not a row' contract, asserted rather than documented."""

    def test_instance_is_transient(self):
        cfg = _repo().build_detached("p1", _URL, config_id="c")

        # transient == never added to a session and has no identity key. If a
        # future change adds this to the session, an is_active=True row would
        # appear for a URL the principal never registered.
        assert inspect(cfg).transient is True
