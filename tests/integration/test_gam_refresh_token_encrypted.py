"""The GAM OAuth refresh token is encrypted at rest.

It is a credential to the publisher's ad server, the same as the service account JSON
beside it, which was already encrypted. These cases write it through the model, as every
production path does, and read the column back with SQL to see what the database holds.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from src.core.database.models import AdapterConfig
from src.core.database.repositories.adapter_config import AdapterConfigRepository
from src.core.utils.encryption import decrypt_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "gam_token_encrypted"
TOKEN = "1//refresh-token-that-must-not-be-stored-in-plaintext"


@pytest.fixture
def env(integration_db):
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv() as env:
        yield env


def _stored_column(env) -> str | None:
    """The ``adapter_config.gam_refresh_token`` value as the database holds it."""
    return (
        env.get_session()
        .execute(text("SELECT gam_refresh_token FROM adapter_config WHERE tenant_id = :tid"), {"tid": TENANT_ID})
        .scalar_one()
    )


def _reloaded(env) -> AdapterConfig:
    session = env.get_session()
    session.expire_all()
    return session.scalars(select(AdapterConfig).filter_by(tenant_id=TENANT_ID)).one()


def _gam_config(token: str | None) -> AdapterConfig:
    from tests.factories import AdapterConfigFactory, TenantFactory

    tenant = TenantFactory(tenant_id=TENANT_ID, ad_server="google_ad_manager")
    return AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_auth_method="oauth",
        gam_network_code="12345678",
        gam_refresh_token=token,
    )


def test_the_stored_token_is_ciphertext_and_reads_back_as_the_token(env):
    _gam_config(TOKEN)

    stored = _stored_column(env)
    assert stored is not None
    assert TOKEN not in stored
    assert decrypt_api_key(stored) == TOKEN
    assert _reloaded(env).gam_refresh_token == TOKEN


def test_replacing_the_token_stores_the_new_one_encrypted(env):
    _gam_config(TOKEN)
    replacement = "1//a-freshly-authorized-refresh-token"

    config = _reloaded(env)
    config.gam_refresh_token = replacement
    env.get_session().commit()

    stored = _stored_column(env)
    assert replacement not in stored
    assert decrypt_api_key(stored) == replacement
    assert _reloaded(env).gam_refresh_token == replacement


def test_clearing_the_token_stores_null(env):
    _gam_config(TOKEN)

    config = _reloaded(env)
    config.gam_refresh_token = None
    env.get_session().commit()

    assert _stored_column(env) is None
    assert _reloaded(env).gam_refresh_token is None


@pytest.mark.parametrize(("token", "expected"), [(TOKEN, True), (None, False)])
def test_has_gam_credentials_reads_the_stored_token(env, token, expected):
    _gam_config(token)

    assert AdapterConfigRepository.has_gam_credentials(_reloaded(env)) is expected
