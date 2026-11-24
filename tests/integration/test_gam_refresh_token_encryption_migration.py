"""Revision 9fdf18a56063 encrypts every stored GAM refresh token with ``ENCRYPTION_KEY``.

A plaintext token is encrypted, a token that already decrypts is left byte for byte, and
NULL stays NULL. The downgrade writes the tokens back as plaintext. With plaintext tokens
and no ``ENCRYPTION_KEY`` the upgrade stops, naming the tenants, and changes nothing.

Runs against a MIGRATED database (``migration_db``), at the revision before this one, with
rows inserted as that schema stores them.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from src.core.http_utils import hostname_of
from src.core.utils.encryption import decrypt_api_key, encrypt_api_key
from tests.integration.migration_helpers import reset_to_revision, run_alembic_downgrade, run_alembic_upgrade
from tests.utils.database_helpers import vhost_for

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "9fdf18a56063"
_PREVIOUS = "e2af0c242a7f"

_PLAINTEXT_TENANT = "mig_gam_token_plain"
_ENCRYPTED_TENANT = "mig_gam_token_encrypted"
_NULL_TENANT = "mig_gam_token_null"

_PLAINTEXT_TOKEN = "1//plaintext-refresh-token"
_ENCRYPTED_TOKEN = "1//already-encrypted-refresh-token"


@pytest.fixture
def encryption_key(monkeypatch):
    """A per-test ``ENCRYPTION_KEY``; the migration helpers rebuild settings from the environment."""
    from src.core import config as config_module

    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config_module, "_settings", None)


def _add_tenant(engine, tenant_id: str) -> None:
    vhost = vhost_for(tenant_id)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, virtual_host, virtual_host_name, ad_server, is_active) "
                "VALUES (:tid, :tid, :sub, :vhost, :vhost_name, 'google_ad_manager', true)"
            ),
            {"tid": tenant_id, "sub": tenant_id.replace("_", "-"), "vhost": vhost, "vhost_name": hostname_of(vhost)},
        )


def _add_adapter_config(engine, tenant_id: str, token: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO adapter_config (tenant_id, adapter_type, gam_refresh_token) "
                "VALUES (:tid, 'google_ad_manager', :token)"
            ),
            {"tid": tenant_id, "token": token},
        )


def _stored_token(engine, tenant_id: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT gam_refresh_token FROM adapter_config WHERE tenant_id = :tid"), {"tid": tenant_id}
        ).scalar_one()


def _seed(migration_db, rows: dict[str, str | None]):
    """Put the database at the previous revision with one GAM tenant per entry of *rows*."""
    first, *rest = rows
    engine, db_url = reset_to_revision(
        migration_db, revision=_PREVIOUS, tenant_id=first, tenant_name=first, subdomain=first.replace("_", "-")
    )
    for tenant_id in rest:
        _add_tenant(engine, tenant_id)
    for tenant_id, token in rows.items():
        _add_adapter_config(engine, tenant_id, token)
    return engine, db_url


def test_upgrade_encrypts_plaintext_and_downgrade_restores_it(migration_db, encryption_key):
    already_encrypted = encrypt_api_key(_ENCRYPTED_TOKEN)
    engine, db_url = _seed(
        migration_db,
        {_PLAINTEXT_TENANT: _PLAINTEXT_TOKEN, _ENCRYPTED_TENANT: already_encrypted, _NULL_TENANT: None},
    )

    run_alembic_upgrade(db_url, _REVISION)

    encrypted = _stored_token(engine, _PLAINTEXT_TENANT)
    assert encrypted is not None
    assert _PLAINTEXT_TOKEN not in encrypted
    assert decrypt_api_key(encrypted) == _PLAINTEXT_TOKEN
    assert _stored_token(engine, _ENCRYPTED_TENANT) == already_encrypted
    assert _stored_token(engine, _NULL_TENANT) is None

    run_alembic_downgrade(db_url, _PREVIOUS)

    assert _stored_token(engine, _PLAINTEXT_TENANT) == _PLAINTEXT_TOKEN
    assert _stored_token(engine, _ENCRYPTED_TENANT) == _ENCRYPTED_TOKEN
    assert _stored_token(engine, _NULL_TENANT) is None


def test_upgrade_without_an_encryption_key_refuses_and_names_the_tenants(migration_db, monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    engine, db_url = _seed(migration_db, {_PLAINTEXT_TENANT: _PLAINTEXT_TOKEN, _NULL_TENANT: None})

    with pytest.raises(RuntimeError, match=_PLAINTEXT_TENANT) as raised:
        run_alembic_upgrade(db_url, _REVISION)

    assert "ENCRYPTION_KEY" in str(raised.value)
    assert _NULL_TENANT not in str(raised.value)
    assert _stored_token(engine, _PLAINTEXT_TENANT) == _PLAINTEXT_TOKEN


def test_upgrade_without_an_encryption_key_passes_when_no_token_is_stored(migration_db, monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    engine, db_url = _seed(migration_db, {_NULL_TENANT: None})

    run_alembic_upgrade(db_url, _REVISION)

    assert _stored_token(engine, _NULL_TENANT) is None


_FOREIGN_TENANT = "mig_gam_token_foreign"


def _encrypted_with_another_key(token: str) -> str:
    return Fernet(Fernet.generate_key()).encrypt(token.encode()).decode()


def test_upgrade_refuses_a_token_encrypted_with_another_key(migration_db, encryption_key):
    foreign = _encrypted_with_another_key("1//token-from-another-deployment")
    engine, db_url = _seed(migration_db, {_PLAINTEXT_TENANT: _PLAINTEXT_TOKEN, _FOREIGN_TENANT: foreign})

    with pytest.raises(RuntimeError, match=_FOREIGN_TENANT) as raised:
        run_alembic_upgrade(db_url, _REVISION)

    assert _PLAINTEXT_TENANT not in str(raised.value)
    # nothing is written: no double encryption, and the plaintext token waits for the re-run
    assert _stored_token(engine, _FOREIGN_TENANT) == foreign
    assert _stored_token(engine, _PLAINTEXT_TENANT) == _PLAINTEXT_TOKEN


def test_downgrade_refuses_a_token_encrypted_with_another_key(migration_db, encryption_key):
    engine, db_url = _seed(migration_db, {_PLAINTEXT_TENANT: _PLAINTEXT_TOKEN, _FOREIGN_TENANT: _ENCRYPTED_TOKEN})
    run_alembic_upgrade(db_url, _REVISION)
    foreign = _encrypted_with_another_key(_ENCRYPTED_TOKEN)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE adapter_config SET gam_refresh_token = :token WHERE tenant_id = :tid"),
            {"token": foreign, "tid": _FOREIGN_TENANT},
        )

    with pytest.raises(RuntimeError, match=_FOREIGN_TENANT):
        run_alembic_downgrade(db_url, _PREVIOUS)

    assert _stored_token(engine, _FOREIGN_TENANT) == foreign
    assert decrypt_api_key(_stored_token(engine, _PLAINTEXT_TENANT)) == _PLAINTEXT_TOKEN
