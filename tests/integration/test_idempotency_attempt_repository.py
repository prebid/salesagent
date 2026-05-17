"""Integration tests for IdempotencyAttemptRepository.

Backs AdCP contract item 7 (issue #1303): retrying a tool call with the same
idempotency_key after a rejection must return the cached envelope, not a
fresh evaluation. The repository encapsulates the per-tenant, per-principal,
per-tool, per-key uniqueness contract and TTL-driven expiry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests — no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


def _setup(env: _RepoEnv, tenant_id: str = "idem_t1", principal_id: str = "idem_p1"):
    """Create a tenant + principal so FK constraints are satisfied."""
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=tenant_id)
    PrincipalFactory(tenant=tenant, principal_id=principal_id)
    return env.get_session()


class TestRecordRejection:
    """record_rejection writes the envelope and stamps expiry from TTL."""

    def test_writes_row_with_default_ttl(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            envelope = {"errors": [{"code": "VALIDATION_ERROR", "message": "bad budget"}], "context": None}

            now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
            attempt = repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-a",
                response_envelope=envelope,
                now=now,
            )

            assert attempt.tenant_id == "idem_t1"
            assert attempt.principal_id == "idem_p1"
            assert attempt.tool_name == "create_media_buy"
            assert attempt.idempotency_key == "key-a"
            assert attempt.response_envelope == envelope
            # Default TTL = 24h
            assert attempt.expires_at == now + timedelta(seconds=86400)

    def test_custom_ttl_honored(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)

            attempt = repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-b",
                response_envelope={"errors": []},
                ttl=timedelta(minutes=30),
                now=now,
            )

            assert attempt.expires_at == now + timedelta(minutes=30)

    def test_duplicate_raises_integrity_error(self, integration_db):
        """Unique index on (tenant, principal, tool, key) prevents double-cache."""
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-dup",
                response_envelope={"errors": []},
            )

            with pytest.raises(IntegrityError):
                repo.record_rejection(
                    principal_id="idem_p1",
                    tool_name="create_media_buy",
                    idempotency_key="key-dup",
                    response_envelope={"errors": []},
                )


class TestFindByKey:
    """find_by_key returns the cached attempt or None when absent/expired."""

    def test_returns_cached_attempt(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            envelope = {"errors": [{"code": "BUDGET_TOO_LOW"}], "context": {"request_id": "r1"}}
            repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-c",
                response_envelope=envelope,
            )

            found = repo.find_by_key(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-c",
            )

        assert found is not None
        assert found.response_envelope == envelope

    def test_returns_none_when_missing(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")

            found = repo.find_by_key(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="never-cached",
            )

        assert found is None

    def test_returns_none_when_expired(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            past = datetime(2020, 1, 1, tzinfo=UTC)
            repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-expired",
                response_envelope={"errors": []},
                ttl=timedelta(minutes=1),
                now=past,
            )

            # Querying with a "now" far past the expiry should return None
            found = repo.find_by_key(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-expired",
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )

        assert found is None

    def test_tenant_isolation(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            _setup(env, tenant_id="idem_iso_t1", principal_id="idem_iso_p")
            _setup(env, tenant_id="idem_iso_t2", principal_id="idem_iso_p")
            session = env.get_session()

            repo_t1 = IdempotencyAttemptRepository(session, "idem_iso_t1")
            repo_t1.record_rejection(
                principal_id="idem_iso_p",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
                response_envelope={"tenant": "t1"},
            )

            repo_t2 = IdempotencyAttemptRepository(session, "idem_iso_t2")
            found = repo_t2.find_by_key(
                principal_id="idem_iso_p",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
            )

        assert found is None, "Tenant t2 must not see tenant t1's cached rejection"

    def test_principal_isolation(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository
        from tests.factories import PrincipalFactory, TenantFactory

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="idem_pi_t1")
            PrincipalFactory(tenant=tenant, principal_id="idem_pi_a")
            PrincipalFactory(tenant=tenant, principal_id="idem_pi_b")
            session = env.get_session()

            repo = IdempotencyAttemptRepository(session, "idem_pi_t1")
            repo.record_rejection(
                principal_id="idem_pi_a",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
                response_envelope={"principal": "a"},
            )

            found = repo.find_by_key(
                principal_id="idem_pi_b",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
            )

        assert found is None, "Principal b must not see principal a's cached rejection"

    def test_tool_name_isolation(self, integration_db):
        """Different tools using the same idempotency_key are independent."""
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
                response_envelope={"tool": "create"},
            )

            found = repo.find_by_key(
                principal_id="idem_p1",
                tool_name="update_media_buy",
                idempotency_key="shared-key",
            )

        assert found is None, "update_media_buy must not see create_media_buy's cached rejection"


class TestExpireOld:
    """expire_old reaps expired rows and returns the deleted count."""

    def test_removes_expired_only(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            past = datetime(2020, 1, 1, tzinfo=UTC)
            future = datetime(2099, 1, 1, tzinfo=UTC)

            repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-old-1",
                response_envelope={"e": 1},
                ttl=timedelta(minutes=1),
                now=past,
            )
            repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-old-2",
                response_envelope={"e": 2},
                ttl=timedelta(minutes=1),
                now=past,
            )
            repo.record_rejection(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-fresh",
                response_envelope={"e": 3},
                now=future,
            )

            deleted = repo.expire_old(now=datetime(2026, 1, 1, tzinfo=UTC))

        assert deleted == 2

    def test_tenant_scoped(self, integration_db):
        """expire_old on tenant A must not delete tenant B's rows."""
        from sqlalchemy import select

        from src.core.database.models import IdempotencyAttempt
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with _RepoEnv() as env:
            _setup(env, tenant_id="idem_exp_t1", principal_id="idem_exp_p")
            _setup(env, tenant_id="idem_exp_t2", principal_id="idem_exp_p")
            session = env.get_session()

            past = datetime(2020, 1, 1, tzinfo=UTC)
            for tenant in ("idem_exp_t1", "idem_exp_t2"):
                IdempotencyAttemptRepository(session, tenant).record_rejection(
                    principal_id="idem_exp_p",
                    tool_name="create_media_buy",
                    idempotency_key="key-old",
                    response_envelope={"tenant": tenant},
                    ttl=timedelta(minutes=1),
                    now=past,
                )

            deleted_t1 = IdempotencyAttemptRepository(session, "idem_exp_t1").expire_old(
                now=datetime(2026, 1, 1, tzinfo=UTC)
            )

            t2_rows = (
                session.execute(select(IdempotencyAttempt).where(IdempotencyAttempt.tenant_id == "idem_exp_t2"))
                .scalars()
                .all()
            )

        assert deleted_t1 == 1
        assert len(t2_rows) == 1, "Tenant t2's row must survive tenant t1's expire_old"
