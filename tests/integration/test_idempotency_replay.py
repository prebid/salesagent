"""Integration tests for replay-after-rejection through _create_media_buy_impl.

Verifies the AdCP idempotency contract item 7: retrying a tool call with the
same idempotency_key returns the cached rejection envelope verbatim, not a
fresh evaluation.

Without these tests the replay path is dead code — Konstantine's review of
PR #1312 explicitly called this out: "If the replay lookup at lines 1477-1487
were deleted, every test still passes green."

Three layers tested:
1. _build_idempotency_rejection_replay — pure re-hydration of cached dict
2. _cache_rejection_envelope — DB write via repository
3. _create_media_buy_impl — full replay through the production entrypoint
"""

import uuid

import pytest

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env — no external patches needed for replay tests."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestBuildIdempotencyRejectionReplay:
    """_build_idempotency_rejection_replay re-hydrates a cached dict envelope."""

    def test_re_hydrates_cached_envelope_to_failed_result(self):
        from src.core.schemas import (
            CreateMediaBuyError,
            CreateMediaBuyResult,
            Error,
        )
        from src.core.tools.media_buy_create import (
            _build_idempotency_rejection_replay,
        )

        original = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message="start_time required", details=None)],
            context=None,
        )
        cached = original.model_dump(mode="json")

        result = _build_idempotency_rejection_replay(cached, context=None)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.status == "failed"
        assert result.response.errors is not None
        assert len(result.response.errors) == 1
        assert result.response.errors[0].code == "VALIDATION_ERROR"
        assert result.response.errors[0].message == "start_time required"

    def test_echoes_current_request_context_into_replay(self):
        from adcp.types.generated_poc.core.context import ContextObject

        from src.core.schemas import CreateMediaBuyError, Error
        from src.core.tools.media_buy_create import (
            _build_idempotency_rejection_replay,
        )

        original = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message="bad", details=None)],
            context=None,
        )
        cached = original.model_dump(mode="json")
        new_context = ContextObject(application_context={"retry_attempt": 2})

        result = _build_idempotency_rejection_replay(cached, context=new_context)

        assert result.response.context is not None
        assert result.response.context.application_context == {"retry_attempt": 2}


class TestCacheRejectionEnvelopeWritesRow:
    """_cache_rejection_envelope writes a retrievable IdempotencyAttempt row."""

    def test_cache_then_find_returns_envelope(self, integration_db):
        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import CreateMediaBuyError, Error
        from src.core.tools.media_buy_create import _cache_rejection_envelope
        from tests.factories import PrincipalFactory, TenantFactory

        idem_key = f"cache-{uuid.uuid4().hex[:8]}"
        tenant_id = f"cache_t_{uuid.uuid4().hex[:6]}"

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            env.get_session()

        rejection = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message="end_time before start_time", details=None)],
            context=None,
        )
        _cache_rejection_envelope(
            tenant_id=tenant_id,
            principal_id=principal_id,
            idempotency_key=idem_key,
            response=rejection,
        )

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=idem_key,
            )
            assert cached is not None
            assert cached.response_envelope == rejection.model_dump(mode="json")
            assert cached.tenant_id == tenant_id
            assert cached.principal_id == principal_id
            assert cached.tool_name == "create_media_buy"

    def test_no_key_is_noop(self, integration_db):
        from src.core.schemas import CreateMediaBuyError, Error
        from src.core.tools.media_buy_create import _cache_rejection_envelope

        rejection = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message="x", details=None)],
            context=None,
        )
        _cache_rejection_envelope(
            tenant_id="any_tenant",
            principal_id="any_principal",
            idempotency_key=None,
            response=rejection,
        )

    def test_duplicate_cache_is_swallowed_via_integrity_error(self, integration_db):
        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import CreateMediaBuyError, Error
        from src.core.tools.media_buy_create import _cache_rejection_envelope
        from tests.factories import PrincipalFactory, TenantFactory

        idem_key = f"dup-{uuid.uuid4().hex[:8]}"
        tenant_id = f"dup_t_{uuid.uuid4().hex[:6]}"

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            principal = PrincipalFactory(tenant=tenant)
            principal_id = principal.principal_id
            env.get_session()

        rejection = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message="x", details=None)],
            context=None,
        )
        _cache_rejection_envelope(
            tenant_id=tenant_id,
            principal_id=principal_id,
            idempotency_key=idem_key,
            response=rejection,
        )
        _cache_rejection_envelope(
            tenant_id=tenant_id,
            principal_id=principal_id,
            idempotency_key=idem_key,
            response=rejection,
        )

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=idem_key,
            )
            assert cached is not None


class TestImplReplaysCachedRejection:
    """_create_media_buy_impl replays cached rejection envelope on key match.

    This is the test Konstantine asked for: a wire-path proof that the
    replay lookup at lines 1721-1731 actually serves the cached envelope.
    """

    async def test_cached_rejection_returned_on_replay(self, integration_db):
        from datetime import UTC, datetime

        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import (
            CreateMediaBuyError,
            CreateMediaBuyRequest,
            CreateMediaBuyResult,
            Error,
        )
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from tests.factories import PrincipalFactory, TenantFactory

        idem_key = f"replay-{uuid.uuid4().hex[:8]}"
        tenant_id = f"replay_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        original_message = "packages[].budget required for non-guaranteed inventory"

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            PrincipalFactory(tenant=tenant, principal_id=principal_id)
            env.get_session()

        original_rejection = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message=original_message, details=None)],
            context=None,
        )
        with MediaBuyUoW(tenant_id) as seed_uow:
            assert seed_uow.idempotency_attempts is not None
            seed_uow.idempotency_attempts.record_rejection(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=idem_key,
                response_envelope=original_rejection.model_dump(mode="json"),
            )

        identity = PrincipalFactory.make_identity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            testing_context=AdCPTestContext(test_session_id="replay_test"),
        )

        req = CreateMediaBuyRequest(
            brand={"domain": "replay-test.example.com"},
            packages=[],
            start_time=datetime(2026, 6, 1, tzinfo=UTC),
            end_time=datetime(2026, 6, 30, tzinfo=UTC),
            po_number="REPLAY-1",
            idempotency_key=idem_key,
        )

        result = await _create_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, CreateMediaBuyResult)
        assert result.status == "failed"
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.response.errors is not None
        assert len(result.response.errors) == 1
        assert result.response.errors[0].code == "VALIDATION_ERROR"
        assert result.response.errors[0].message == original_message

    async def test_unrelated_key_does_not_replay(self, integration_db):
        """Different idempotency_key on the same principal does not pick up
        an unrelated cached rejection — the lookup is key-scoped, not just
        principal-scoped.
        """
        from datetime import UTC, datetime

        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import (
            CreateMediaBuyError,
            CreateMediaBuyRequest,
            CreateMediaBuyResult,
            Error,
        )
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from tests.factories import PrincipalFactory, TenantFactory

        seeded_key = f"seeded-{uuid.uuid4().hex[:8]}"
        other_key = f"other-{uuid.uuid4().hex[:8]}"
        tenant_id = f"miss_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            PrincipalFactory(tenant=tenant, principal_id=principal_id)
            env.get_session()

        seeded = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message="seeded message", details=None)],
            context=None,
        )
        with MediaBuyUoW(tenant_id) as seed_uow:
            assert seed_uow.idempotency_attempts is not None
            seed_uow.idempotency_attempts.record_rejection(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=seeded_key,
                response_envelope=seeded.model_dump(mode="json"),
            )

        identity = PrincipalFactory.make_identity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            testing_context=AdCPTestContext(dry_run=True, test_session_id="miss_test"),
        )

        req = CreateMediaBuyRequest(
            brand={"domain": "miss-test.example.com"},
            packages=[],
            start_time=datetime(2026, 6, 1, tzinfo=UTC),
            end_time=datetime(2026, 6, 30, tzinfo=UTC),
            po_number="MISS-1",
            idempotency_key=other_key,
        )

        result = await _create_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, CreateMediaBuyResult)
        if isinstance(result.response, CreateMediaBuyError) and result.response.errors:
            messages = [e.message for e in result.response.errors]
            assert "seeded message" not in messages, (
                f"Replay incorrectly served seeded envelope for unrelated key: {messages}"
            )
