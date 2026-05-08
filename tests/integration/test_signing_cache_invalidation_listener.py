"""SQLAlchemy session listener evicts the webhook-signing cache on commit.

Closes the race in the prior in-repo invalidation: invalidating before
``session.commit()`` lets a concurrent reader on another worker re-read
the pre-rotation snapshot and re-cache the about-to-be-stale kid before
the rotation becomes durable. The listener captures changes during
``before_flush`` and evicts during ``after_commit`` so the cache is
never empty over a stale-on-disk row.
"""

from __future__ import annotations

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.repositories import TenantSigningCredentialRepository
from src.services.webhook_signing import (
    LoadedSigningCredential,
    _credential_cache,
    _credential_cache_lock,
    invalidate_credential_cache,
)


def _seed_cache(tenant_id: str) -> None:
    with _credential_cache_lock:
        _credential_cache[tenant_id] = LoadedSigningCredential(
            key_id="sentinel-kid",
            alg="ed25519",
            pem_bytes=b"sentinel",
        )


def _has_cached_entry(tenant_id: str) -> bool:
    with _credential_cache_lock:
        return tenant_id in _credential_cache


def _seed_tenant(tenant_id: str) -> None:
    """Persist a Tenant row in its own committed session.

    Kept separate from the operation-under-test session so the test can
    drive commit/rollback timing without the tenant setup interfering.
    Binds all factories so subfactories (CurrencyLimit, etc.) work too.
    """
    from tests.factories import ALL_FACTORIES, TenantFactory

    with get_db_session() as session:
        try:
            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = session
            TenantFactory(tenant_id=tenant_id)
            session.commit()
        finally:
            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = None


def _seed_active_credential(tenant_id: str, key_id: str) -> None:
    from tests.factories import ALL_FACTORIES, TenantSigningCredentialFactory

    with get_db_session() as session:
        try:
            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = session
            TenantSigningCredentialFactory(
                tenant_id=tenant_id,
                key_id=key_id,
                purpose="webhook-signing",
                is_active=True,
            )
            session.commit()
        finally:
            for f in ALL_FACTORIES:
                f._meta.sqlalchemy_session = None


@pytest.fixture(autouse=True)
def _isolate_credential_cache():
    invalidate_credential_cache()
    yield
    invalidate_credential_cache()


@pytest.mark.requires_db
class TestSigningCacheInvalidationListener:
    """Cache eviction is wired to commit, not to the repo call site."""

    def test_create_evicts_cache_only_after_commit(self, integration_db):
        tenant_id = "t_create_evict"
        _seed_tenant(tenant_id)
        _seed_cache(tenant_id)

        with get_db_session() as session:
            repo = TenantSigningCredentialRepository(session, tenant_id=tenant_id)
            repo.create(
                purpose="webhook-signing",
                backend="local_pem",
                backend_ref=f"/tmp/keys/{tenant_id}.pem",
                public_jwk={"kty": "OKP", "crv": "Ed25519", "x": "test"},
                key_id="kid-create",
            )
            assert _has_cached_entry(tenant_id), (
                "Cache evicted before commit — concurrent readers would see "
                "an empty cache and re-cache the pre-create state."
            )
            session.commit()

        assert not _has_cached_entry(tenant_id), "Cache still holds sentinel after commit — listener did not evict."

    def test_rotate_out_evicts_cache_only_after_commit(self, integration_db):
        tenant_id = "t_rotate_evict"
        _seed_tenant(tenant_id)
        _seed_active_credential(tenant_id, key_id="kid-to-rotate")
        _seed_cache(tenant_id)

        with get_db_session() as session:
            repo = TenantSigningCredentialRepository(session, tenant_id=tenant_id)
            assert repo.rotate_out("webhook-signing", "kid-to-rotate") is True
            assert _has_cached_entry(tenant_id), "Cache evicted before commit — same race as create."
            session.commit()

        assert not _has_cached_entry(tenant_id)

    def test_rollback_does_not_evict(self, integration_db):
        """Rolling back means the kid is still active — cache must keep its snapshot."""
        tenant_id = "t_rollback"
        _seed_tenant(tenant_id)
        _seed_cache(tenant_id)

        with get_db_session() as session:
            repo = TenantSigningCredentialRepository(session, tenant_id=tenant_id)
            repo.create(
                purpose="webhook-signing",
                backend="local_pem",
                backend_ref=f"/tmp/keys/{tenant_id}.pem",
                public_jwk={"kty": "OKP", "crv": "Ed25519", "x": "test"},
                key_id="kid-rollback",
            )
            session.rollback()

        assert _has_cached_entry(tenant_id), (
            "Listener evicted on rollback — sentinel still represents the " "durable state and must survive."
        )

    def test_other_purposes_do_not_evict_webhook_cache(self, integration_db):
        tenant_id = "t_other_purpose"
        _seed_tenant(tenant_id)
        _seed_cache(tenant_id)

        with get_db_session() as session:
            repo = TenantSigningCredentialRepository(session, tenant_id=tenant_id)
            repo.create(
                purpose="request-signing-as-buyer",
                backend="local_pem",
                backend_ref=f"/tmp/keys/{tenant_id}-buyer.pem",
                public_jwk={"kty": "OKP", "crv": "Ed25519", "x": "test"},
                key_id="kid-buyer",
            )
            session.commit()

        assert _has_cached_entry(tenant_id), "Listener evicted on a non-webhook purpose write — wastes the cache."

    def test_cross_tenant_isolation(self, integration_db):
        tenant_a, tenant_b = "t_iso_a", "t_iso_b"
        _seed_tenant(tenant_a)
        _seed_tenant(tenant_b)
        _seed_cache(tenant_a)
        _seed_cache(tenant_b)

        with get_db_session() as session:
            repo = TenantSigningCredentialRepository(session, tenant_id=tenant_a)
            repo.create(
                purpose="webhook-signing",
                backend="local_pem",
                backend_ref=f"/tmp/keys/{tenant_a}.pem",
                public_jwk={"kty": "OKP", "crv": "Ed25519", "x": "test"},
                key_id="kid-a",
            )
            session.commit()

        assert not _has_cached_entry(tenant_a)
        assert _has_cached_entry(
            tenant_b
        ), "Listener evicted tenant_b's cache when tenant_a rotated — cross-tenant leak."
