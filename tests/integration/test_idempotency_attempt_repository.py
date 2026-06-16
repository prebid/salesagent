"""Integration tests for IdempotencyAttemptRepository.

Backs the AdCP 3.0.1 idempotency contract: retrying a mutating tool call with
the same idempotency_key must replay the original SUCCESS verbatim (errors are
never cached). The repository encapsulates the per-tenant, per-principal,
per-account, per-tool, per-key uniqueness contract and TTL-driven expiry.

The stored envelope is structured ``{"status": <protocol task status>,
"response": <serialized domain model>}`` — the protocol status rides alongside
the domain payload (a pending buy's ``submitted`` status is not a valid domain
status, so it cannot live inside the response).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _Resp(BaseModel):
    """Minimal stand-in success model — the repo serializes whatever model it's given."""

    model_config = {"extra": "allow"}


def _model(**fields) -> _Resp:
    return _Resp(**fields)


def _envelope(status: str, **response_fields) -> dict:
    """The structured shape record_success stores: protocol status + domain response."""
    return {"status": status, "response": response_fields}


def _setup(env: BareIntegrationEnv, tenant_id: str = "idem_t1", principal_id: str = "idem_p1"):
    """Create a tenant + principal so FK constraints are satisfied."""
    from tests.factories import PrincipalFactory, TenantFactory

    tenant = TenantFactory(tenant_id=tenant_id)
    PrincipalFactory(tenant=tenant, principal_id=principal_id)
    return env.get_session()


class TestRecordSuccess:
    """record_success serializes the model, writes the row, and stamps expiry from TTL."""

    def test_writes_row_with_default_ttl(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")

            now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
            attempt = repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-a",
                response_model=_model(media_buy_id="mb_1", packages=[]),
                protocol_status="completed",
                payload_hash="hash-fixed",
                now=now,
            )

            assert attempt.tenant_id == "idem_t1"
            assert attempt.principal_id == "idem_p1"
            assert attempt.account_id is None
            assert attempt.tool_name == "create_media_buy"
            assert attempt.idempotency_key == "key-a"
            # The repo stored protocol status + the serialized model, structured.
            assert attempt.response_envelope == _envelope("completed", media_buy_id="mb_1", packages=[])
            # Default TTL = 24h
            assert attempt.expires_at == now + timedelta(seconds=86400)

    def test_submitted_protocol_status_preserved(self, integration_db):
        """A pending buy's ``submitted`` status survives even though it is not a domain status."""
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            attempt = repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-pending",
                response_model=_model(media_buy_id="mb_p", status="pending_start"),
                protocol_status="submitted",
                payload_hash="hash-fixed",
            )

        assert attempt.response_envelope["status"] == "submitted"
        assert attempt.response_envelope["response"]["status"] == "pending_start"

    def test_custom_ttl_honored(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)

            attempt = repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-b",
                response_model=_model(media_buy_id="mb_b"),
                protocol_status="completed",
                payload_hash="hash-fixed",
                ttl=timedelta(minutes=30),
                now=now,
            )

            assert attempt.expires_at == now + timedelta(minutes=30)

    def test_duplicate_raises_integrity_error(self, integration_db):
        """Unique index on (tenant, principal, account, tool, key) prevents double-cache."""
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-dup",
                response_model=_model(media_buy_id="mb_d"),
                protocol_status="completed",
                payload_hash="hash-fixed",
            )

            with pytest.raises(IntegrityError):
                repo.record_success(
                    principal_id="idem_p1",
                    tool_name="create_media_buy",
                    idempotency_key="key-dup",
                    response_model=_model(media_buy_id="mb_d"),
                    protocol_status="completed",
                    payload_hash="hash-fixed",
                )

    def test_null_account_still_unique(self, integration_db):
        """NULLS NOT DISTINCT: two NULL-account rows for the same key still collide."""
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-null-acct",
                response_model=_model(media_buy_id="mb_n"),
                protocol_status="completed",
                payload_hash="hash-fixed",
                account_id=None,
            )

            with pytest.raises(IntegrityError):
                repo.record_success(
                    principal_id="idem_p1",
                    tool_name="create_media_buy",
                    idempotency_key="key-null-acct",
                    response_model=_model(media_buy_id="mb_n"),
                    protocol_status="completed",
                    payload_hash="hash-fixed",
                    account_id=None,
                )


class TestFindByKey:
    """find_by_key returns the cached attempt or None when absent/expired."""

    def test_returns_cached_attempt(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-c",
                response_model=_model(media_buy_id="mb_c", packages=[]),
                protocol_status="completed",
                payload_hash="hash-fixed",
            )

            found = repo.find_by_key(
                principal_id="idem_p1",
                idempotency_key="key-c",
            )

        assert found is not None
        assert found.response_envelope == _envelope("completed", media_buy_id="mb_c", packages=[])

    def test_returns_none_when_missing(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")

            found = repo.find_by_key(
                principal_id="idem_p1",
                idempotency_key="never-cached",
            )

        assert found is None

    def test_returns_none_when_expired(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            past = datetime(2020, 1, 1, tzinfo=UTC)
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-expired",
                response_model=_model(media_buy_id="mb_e"),
                protocol_status="completed",
                payload_hash="hash-fixed",
                ttl=timedelta(minutes=1),
                now=past,
            )

            # Querying with a "now" far past the expiry should return None
            found = repo.find_by_key(
                principal_id="idem_p1",
                idempotency_key="key-expired",
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )

        assert found is None

    def test_tenant_isolation(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            _setup(env, tenant_id="idem_iso_t1", principal_id="idem_iso_p")
            _setup(env, tenant_id="idem_iso_t2", principal_id="idem_iso_p")
            session = env.get_session()

            repo_t1 = IdempotencyAttemptRepository(session, "idem_iso_t1")
            repo_t1.record_success(
                principal_id="idem_iso_p",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
                response_model=_model(tenant="t1"),
                protocol_status="completed",
                payload_hash="hash-fixed",
            )

            repo_t2 = IdempotencyAttemptRepository(session, "idem_iso_t2")
            found = repo_t2.find_by_key(
                principal_id="idem_iso_p",
                idempotency_key="shared-key",
            )

        assert found is None, "Tenant t2 must not see tenant t1's cached success"

    def test_principal_isolation(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository
        from tests.factories import PrincipalFactory, TenantFactory

        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id="idem_pi_t1")
            PrincipalFactory(tenant=tenant, principal_id="idem_pi_a")
            PrincipalFactory(tenant=tenant, principal_id="idem_pi_b")
            session = env.get_session()

            repo = IdempotencyAttemptRepository(session, "idem_pi_t1")
            repo.record_success(
                principal_id="idem_pi_a",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
                response_model=_model(principal="a"),
                protocol_status="completed",
                payload_hash="hash-fixed",
            )

            found = repo.find_by_key(
                principal_id="idem_pi_b",
                idempotency_key="shared-key",
            )

        assert found is None, "Principal b must not see principal a's cached success"

    def test_account_isolation(self, integration_db):
        """Two accounts under one principal reusing a key are independent (AdCP scope)."""
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
                response_model=_model(account="acct_a"),
                protocol_status="completed",
                payload_hash="hash-fixed",
                account_id="acct_a",
            )

            # Same (tenant, principal, tool, key) but a different account → independent.
            other = repo.find_by_key(
                principal_id="idem_p1",
                idempotency_key="shared-key",
                account_id="acct_b",
            )
            same = repo.find_by_key(
                principal_id="idem_p1",
                idempotency_key="shared-key",
                account_id="acct_a",
            )

        assert other is None, "account acct_b must not see acct_a's cached success"
        assert same is not None, "account acct_a must replay its own cached success"

    def test_key_scope_has_no_tool_dimension(self, integration_db):
        """The spec scope is (agent, account, key) — a key is ONE row across tools.

        A different tool probing the same key MUST hit the row the first tool
        wrote (so its differing payload hash conflicts at the caller layer)
        rather than getting an independent per-tool cache. ``tool_name`` is
        recorded for observability only.
        """
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="shared-key",
                response_model=_model(tool="create"),
                protocol_status="completed",
                payload_hash="hash-fixed",
            )

            found = repo.find_by_key(
                principal_id="idem_p1",
                idempotency_key="shared-key",
            )

            assert found is not None, "a key probe must see the row regardless of which tool wrote it"
            assert found.tool_name == "create_media_buy", "the writing tool stays recorded for observability"
            assert found.payload_hash == "hash-fixed"


class TestExpireOld:
    """expire_old reaps expired rows and returns the deleted count."""

    def test_removes_expired_only(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            past = datetime(2020, 1, 1, tzinfo=UTC)
            future = datetime(2099, 1, 1, tzinfo=UTC)

            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-old-1",
                response_model=_model(media_buy_id="mb_o1"),
                protocol_status="completed",
                payload_hash="hash-fixed",
                ttl=timedelta(minutes=1),
                now=past,
            )
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-old-2",
                response_model=_model(media_buy_id="mb_o2"),
                protocol_status="completed",
                payload_hash="hash-fixed",
                ttl=timedelta(minutes=1),
                now=past,
            )
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-fresh",
                response_model=_model(media_buy_id="mb_f"),
                protocol_status="completed",
                payload_hash="hash-fixed",
                now=future,
            )

            deleted = repo.expire_old(now=datetime(2026, 1, 1, tzinfo=UTC))

        assert deleted == 2

    def test_tenant_scoped(self, integration_db):
        """expire_old on tenant A must not delete tenant B's rows."""
        from sqlalchemy import select

        from src.core.database.models import IdempotencyAttempt
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            _setup(env, tenant_id="idem_exp_t1", principal_id="idem_exp_p")
            _setup(env, tenant_id="idem_exp_t2", principal_id="idem_exp_p")
            session = env.get_session()

            past = datetime(2020, 1, 1, tzinfo=UTC)
            for tenant in ("idem_exp_t1", "idem_exp_t2"):
                IdempotencyAttemptRepository(session, tenant).record_success(
                    principal_id="idem_exp_p",
                    tool_name="create_media_buy",
                    idempotency_key="key-old",
                    response_model=_model(tenant=tenant),
                    protocol_status="completed",
                    payload_hash="hash-fixed",
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


class TestFindIncludingExpired:
    """find_including_expired returns the scope's row regardless of expiry (find_by_key does not)."""

    def test_returns_expired_row_that_find_by_key_filters(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            seeded_at = datetime(2026, 5, 1, tzinfo=UTC)
            repo.record_success(
                principal_id="idem_p1",
                tool_name="create_media_buy",
                idempotency_key="key-incl-exp",
                response_model=_model(media_buy_id="mb_incl"),
                protocol_status="completed",
                payload_hash="hash-incl",
                ttl=timedelta(hours=1),
                now=seeded_at,
            )
            after_expiry = datetime(2026, 6, 1, tzinfo=UTC)

            # find_by_key treats the closed window as absent...
            assert repo.find_by_key(principal_id="idem_p1", idempotency_key="key-incl-exp", now=after_expiry) is None
            # ...but find_including_expired returns the row, carrying its stored expires_at.
            row = repo.find_including_expired(principal_id="idem_p1", idempotency_key="key-incl-exp")
            assert row is not None
            assert row.idempotency_key == "key-incl-exp"
            assert row.expires_at == seeded_at + timedelta(hours=1)

    def test_returns_none_when_no_row_for_scope(self, integration_db):
        from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

        with BareIntegrationEnv() as env:
            session = _setup(env)
            repo = IdempotencyAttemptRepository(session, "idem_t1")
            assert repo.find_including_expired(principal_id="idem_p1", idempotency_key="never-written") is None
