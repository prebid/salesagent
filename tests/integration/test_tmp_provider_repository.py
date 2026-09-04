"""Integration tests for TMPProviderRepository.

Pins the four core invariants the class docstring claims:
  1. list_syncable returns active + draining, ordered by priority ASC, name ASC
  2. list_syncable is tenant-scoped (cross-tenant rows are invisible)
  3. auth_credentials round-trips through a real session flush + select
  4. get_all_syncable (static) crosses tenants — used by the health scheduler

Each test uses real PostgreSQL via IntegrationEnv.

beads: salesagent-m44
"""

from __future__ import annotations

import pytest

from src.core.database.repositories.tmp_provider import TMPProviderRepository
from tests.factories import TenantFactory, TMPProviderFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests — no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestListSyncableOrderAndFilter:
    """list_syncable returns active + draining, ordered by priority ASC then name ASC."""

    def test_returns_active_and_draining_ordered_by_priority_then_name(self, integration_db):
        """Active and draining providers are returned; inactive are excluded.

        Order: priority ASC, name ASC within the same priority.
        """
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tmp_repo_order")
            # Add providers in non-sorted order
            TMPProviderFactory(tenant=tenant, name="Zeta", status="active", priority=1)
            TMPProviderFactory(tenant=tenant, name="Alpha", status="active", priority=0)
            TMPProviderFactory(tenant=tenant, name="Beta", status="draining", priority=0)
            TMPProviderFactory(tenant=tenant, name="Gamma", status="inactive", priority=0)
            session = env.get_session()

            repo = TMPProviderRepository(session, "tmp_repo_order")
            results = repo.list_syncable()

        names = [p.name for p in results]
        statuses = {p.status for p in results}

        # Gamma (inactive) must be excluded
        assert "Gamma" not in names
        assert statuses == {"active", "draining"}
        # priority 0 before priority 1; within priority 0: Alpha < Beta alphabetically
        assert names == ["Alpha", "Beta", "Zeta"]


class TestListSyncableTenantScoped:
    """list_syncable only returns providers for the scoped tenant."""

    def test_list_syncable_is_tenant_scoped(self, integration_db):
        """Providers from a different tenant are invisible to the scoped repository."""
        with _RepoEnv() as env:
            t1 = TenantFactory(tenant_id="tmp_repo_t1")
            t2 = TenantFactory(tenant_id="tmp_repo_t2")
            TMPProviderFactory(tenant=t1, name="T1 Provider", status="active")
            TMPProviderFactory(tenant=t2, name="T2 Provider", status="active")
            session = env.get_session()

            repo_t1 = TMPProviderRepository(session, "tmp_repo_t1")
            results = repo_t1.list_syncable()

        names = [p.name for p in results]
        assert names == ["T1 Provider"]
        assert "T2 Provider" not in names


class TestAuthCredentialsRoundTrip:
    """auth_credentials encrypts on write and decrypts on read through a real session."""

    def test_auth_credentials_round_trip_through_session(self, integration_db):
        """Setting auth_credentials, flushing, and re-reading returns the original plaintext."""
        from unittest.mock import patch

        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"ENCRYPTION_KEY": key}):
            with _RepoEnv() as env:
                tenant = TenantFactory(tenant_id="tmp_repo_enc")
                # Create provider without credentials first, then set via property
                provider = TMPProviderFactory(tenant=tenant, name="Encrypted Provider", status="active")
                session = env.get_session()

                # Set credentials through the encrypting property
                provider.auth_credentials = "super-secret-token"
                session.flush()

                # Re-read from the same session (identity map may return same object)
                repo = TMPProviderRepository(session, "tmp_repo_enc")
                fetched = repo.list_syncable()

            assert len(fetched) == 1
            # The raw column must NOT be the plaintext value
            assert fetched[0]._auth_credentials != "super-secret-token"
            # Reading through the property must decrypt to the original value
            assert fetched[0].auth_credentials == "super-secret-token"


class TestGetAllSyncableCrossTenant:
    """get_all_syncable (static) returns active/draining providers across all tenants."""

    def test_get_all_syncable_crosses_tenants(self, integration_db):
        """The static method used by the health scheduler sees providers from all tenants."""
        with _RepoEnv() as env:
            t1 = TenantFactory(tenant_id="tmp_repo_cross_t1")
            t2 = TenantFactory(tenant_id="tmp_repo_cross_t2")
            TMPProviderFactory(tenant=t1, name="Cross T1 Active", status="active")
            TMPProviderFactory(tenant=t2, name="Cross T2 Draining", status="draining")
            TMPProviderFactory(tenant=t1, name="Cross T1 Inactive", status="inactive")
            session = env.get_session()

            results = TMPProviderRepository.get_all_syncable(session)

        names = {p.name for p in results}
        assert "Cross T1 Active" in names
        assert "Cross T2 Draining" in names
        # Inactive must be excluded
        assert "Cross T1 Inactive" not in names


class TestUpdateFieldsSurvivesCorruptCredential:
    """update_fields() must not be blocked by a corrupt/rotated existing credential.

    Regression test: ``update_fields`` used to validate incoming field names with
    ``hasattr(provider, key)`` against the INSTANCE. For ``key="auth_credentials"``,
    that invokes the property getter, which decrypts the *existing* stored value —
    exactly the case hit during credential rotation. A corrupt ciphertext (e.g. the
    encryption key was rotated) raised ``AdCPConfigurationError`` from the mere
    existence check, aborting the very update meant to replace the bad value.
    ``hasattr(type(provider), key)`` checks the descriptor exists without invoking it.
    """

    def test_rotating_a_corrupt_credential_succeeds(self, integration_db):
        """A provider with an undecryptable stored credential can still be updated."""
        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="tmp_repo_rotate")
            provider = TMPProviderFactory(tenant=tenant, name="Rotate Provider", status="active")
            session = env.get_session()

            # Simulate a corrupt/rotated ciphertext: not valid Fernet output, so the
            # auth_credentials getter's decrypt call raises ValueError -> AdCPConfigurationError.
            provider._auth_credentials = "not-a-valid-fernet-token"
            session.flush()
            provider_id = provider.provider_id

            repo = TMPProviderRepository(session, "tmp_repo_rotate")

            # Rotating the credential must succeed even though the OLD value can't
            # be decrypted — update_fields must not read the old value to validate
            # the field name.
            updated = repo.update_fields(provider_id, auth_credentials="new-plaintext-secret")

            assert updated is not None
            assert updated.auth_credentials == "new-plaintext-secret"


class TestUoWYieldsConcreteRepositories:
    """An open UoW hands back the concrete repository types, with no narrowing.

    The real-session half of ``tests/unit/test_uow_repository_accessor.py``:
    that file grades ``RepositoryAccessor``'s raise-when-closed behavior with no
    database, and this grades what an actually-open session yields. Here rather
    than there because it needs Postgres — the tier decides placement, not the
    subject (#1197 review).
    """

    def test_open_uow_yields_the_concrete_repository_types(self, integration_db):
        from src.core.database.repositories.tenant_config import TenantConfigRepository
        from src.core.database.repositories.uow import TMPProviderUoW

        with TMPProviderUoW("tenant_x") as uow:
            # Read with no `assert ... is not None` — that is the accessor's point.
            assert isinstance(uow.tmp_providers, TMPProviderRepository)
            assert isinstance(uow.tenant_config, TenantConfigRepository)
