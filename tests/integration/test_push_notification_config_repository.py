"""PushNotificationConfigRepository upsert preserve-if-not-passed semantics.

``upsert`` is shared by the A2A ``set_push_notification_config`` handler
(passes ``validation_token`` explicitly) and the create-media-buy /
admin-registration paths (do not). Omitted fields must keep the existing
row's values — otherwise the create-media-buy path would silently null a
``validation_token`` the buyer set through A2A for the same config id —
while an explicit ``None`` must still clear.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.database.models import PushNotificationConfig
from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

pytestmark = pytest.mark.requires_db

SECRET = "s" * 32


def _refreshed(session, config_id: str) -> PushNotificationConfig:
    session.expire_all()
    return session.scalars(select(PushNotificationConfig).filter_by(id=config_id)).one()


@pytest.fixture
def seeded(integration_db, factory_session):
    """(repo, principal, cfg): a config row with every token field populated."""
    tenant = TenantFactory()
    principal = PrincipalFactory(tenant=tenant)
    cfg = PushNotificationConfigFactory(
        tenant=tenant,
        principal=principal,
        validation_token="vtok",
        session_id="sess-1",
        webhook_secret=SECRET,
    )
    return PushNotificationConfigRepository(factory_session, tenant.tenant_id), principal, cfg


def test_upsert_preserves_unpassed_token_fields(seeded, factory_session):
    repo, principal, cfg = seeded
    _, created = repo.upsert(
        config_id=cfg.id,
        principal_id=principal.principal_id,
        url="https://example.com/updated",
        authentication_type="bearer",
        authentication_token="tok2",
    )

    assert created is False
    row = _refreshed(factory_session, cfg.id)
    assert (row.url, row.authentication_type, row.authentication_token) == (
        "https://example.com/updated",
        "bearer",
        "tok2",
    )
    assert (row.validation_token, row.session_id, row.webhook_secret) == ("vtok", "sess-1", SECRET)


def test_upsert_explicit_none_still_clears(seeded, factory_session):
    repo, principal, cfg = seeded
    repo.upsert(
        config_id=cfg.id,
        principal_id=principal.principal_id,
        url="https://example.com/updated",
        authentication_type=None,
        authentication_token=None,
        validation_token=None,
        session_id=None,
        webhook_secret=None,
    )

    row = _refreshed(factory_session, cfg.id)
    assert (row.validation_token, row.session_id, row.webhook_secret) == (None, None, None)


def test_upsert_insert_defaults_unpassed_fields_to_none(integration_db, factory_session):
    tenant = TenantFactory()
    principal = PrincipalFactory(tenant=tenant)

    repo = PushNotificationConfigRepository(factory_session, tenant.tenant_id)
    config, created = repo.upsert(
        config_id="pnc_fresh",
        principal_id=principal.principal_id,
        url="https://example.com/webhook",
        authentication_type=None,
        authentication_token=None,
    )

    assert created is True
    row = _refreshed(factory_session, config.id)
    assert (row.validation_token, row.session_id, row.webhook_secret) == (None, None, None)
    assert row.is_active is True
