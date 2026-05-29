"""Integration tests for replay-after-rejection through _create_media_buy_impl.

Verifies the AdCP idempotency contract item 7: retrying a tool call with the
same idempotency_key returns the cached rejection envelope verbatim, not a
fresh evaluation.

Without these tests the replay path is effectively dead code: if the replay
lookup were deleted, the _impl-only tests would still pass green. These tests
pin it so a regression there fails.

Three layers tested:
1. _build_idempotency_rejection_replay — pure re-hydration of cached dict
2. _cache_rejection_envelope — DB write via repository
3. _create_media_buy_impl — full replay through the production entrypoint
"""

import uuid

import pytest

from tests.harness._base import IntegrationEnv
from tests.harness.assertions import assert_replayed_rejection
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport

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

    Pins that the replay lookup actually serves the cached envelope through
    _create_media_buy_impl (not just the lower-level helpers).
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
            assert (
                "seeded message" not in messages
            ), f"Replay incorrectly served seeded envelope for unrelated key: {messages}"


class TestWirePathReplay:
    """Wire-path proof: ``idempotency_key`` survives MCP / A2A / REST wrappers.

    The _impl-only tests stayed green when the wrappers silently dropped
    ``idempotency_key`` via ``TypeAdapter``, because they never crossed the
    transport boundary. These three tests close that gap by dispatching through
    the real transport pipelines via the ``MediaBuyCreateEnv`` harness:

    - MCP: in-memory FastMCP ``Client`` → middleware → TypeAdapter → wrapper
    - A2A: ``AdCPRequestHandler.on_message_send`` → skill router → ``_serialize_for_a2a``
    - REST: FastAPI ``TestClient`` → route → ``create_media_buy_raw``

    Each seeds a cached rejection envelope, sends an ``idempotency_key`` *through
    the transport*, and asserts the cached envelope comes back on the wire. If a
    future change drops ``idempotency_key`` from a wrapper signature (FastMCP's
    TypeAdapter strips undeclared fields; the A2A skill / REST body forward it
    explicitly), the matching transport's replay is bypassed and the test fails.

    A replayed rejection is a *successful* transport response carrying a
    failed-status ``CreateMediaBuyResult`` (REST 200; MCP/A2A no raise), so the
    payload — not the error path — is asserted via ``assert_replayed_rejection``.
    """

    # Valid create_media_buy params shared by all three transports. The replay
    # short-circuits before the adapter, so packages can be empty.
    _CREATE_KWARGS = {
        "brand": {"domain": "wire-replay.example.com"},
        "packages": [],
        "start_time": "2026-06-01T00:00:00Z",
        "end_time": "2026-06-30T00:00:00Z",
        "po_number": "WIRE-REPLAY-1",
    }

    def _run_wire_replay(self, transport: Transport) -> None:
        """Seed a cached rejection, dispatch through *transport*, assert the replay.

        Single body for all three transports — the only variable is the
        ``Transport`` enum, which ``MediaBuyCreateEnv.call_via`` routes to the
        matching real pipeline.
        """
        idem_key = f"wire-{transport.value}-{uuid.uuid4().hex[:8]}"
        cached_message = f"wire-{transport.value} cached rejection — must round-trip"

        with MediaBuyCreateEnv() as env:
            env.setup_default_data()  # tenant + principal (real auth token) in DB
            env.seed_rejection(idem_key, cached_message)

            result = env.call_via(transport, idempotency_key=idem_key, **self._CREATE_KWARGS)

        assert_replayed_rejection(result, code="VALIDATION_ERROR", message_contains=cached_message)

    def test_mcp_wire_replays_cached_rejection(self, integration_db):
        """MCP wrapper forwards idempotency_key → impl replays cached envelope on wire.

        Regression guard: if the ``create_media_buy`` MCP wrapper stops declaring
        ``idempotency_key``, FastMCP's TypeAdapter strips the field before the
        wrapper runs, the impl never sees the key, the rejection replay is
        bypassed, and this test fails.
        """
        self._run_wire_replay(Transport.MCP)

    def test_a2a_wire_replays_cached_rejection(self, integration_db):
        """A2A wrapper forwards idempotency_key → impl replays cached envelope on wire.

        Regression guard: if ``_handle_create_media_buy_skill`` stops forwarding
        ``idempotency_key=params.get("idempotency_key")`` to ``create_media_buy_raw``,
        the impl never sees the key, the rejection replay is bypassed, and this
        test fails. Dispatch drives the real ``on_message_send`` boundary.
        """
        self._run_wire_replay(Transport.A2A)

    def test_rest_wire_replays_cached_rejection(self, integration_db):
        """REST wrapper forwards idempotency_key → impl replays cached envelope on wire.

        Regression guard: if ``CreateMediaBuyBody`` drops ``idempotency_key`` (or
        the ``/api/v1/media-buys`` route stops passing it through), the impl never
        sees the key, the rejection replay is bypassed, and this test fails.
        """
        self._run_wire_replay(Transport.REST)


class TestTransientRejectionNotCached:
    """Transient-skip regression: transient adapter rejections are NOT cached.

    Production fix at ``media_buy_create.py`` (the adapter-rejection branch):

        recovery = response.errors[0].recovery if response.errors else None
        if getattr(recovery, "value", recovery) != "transient":
            _cache_rejection_envelope(...)

    A transient adapter failure (rate-limit, service-unavailable, timeout) is
    one the buyer's retry is *meant* to succeed on — caching it would replay the
    failure forever and defeat the retry. Non-transient rejections (terminal /
    correctable) MUST be cached so a retry with the same key replays the same
    answer. ``recovery`` arrives as a ``Recovery`` enum, so the guard compares
    ``getattr(recovery, "value", recovery)`` — without ``.value`` the comparison
    is always True and transient errors would be cached, defeating the skip.

    These tests drive the FULL non-dry-run create flow (real DB, real
    IdempotencyAttemptRepository) so the adapter returns a ``CreateMediaBuyError``
    and reaches the ``isinstance(response, CreateMediaBuyError)`` branch, then
    assert the cache state directly via the repository.
    """

    @staticmethod
    def _drive_adapter_rejection(env, *, recovery: str, idempotency_key: str, product_id: str):
        """Make the mock adapter RETURN a CreateMediaBuyError with the given recovery.

        The harness installs a happy-path ``side_effect`` on
        ``create_media_buy``; ``side_effect`` takes precedence over
        ``return_value``, so it must be cleared before the error
        ``return_value`` takes effect. Returns the CreateMediaBuyResult.
        """
        from src.core.schemas import CreateMediaBuyError, Error

        adapter = env.mock["adapter"].return_value
        adapter.create_media_buy.side_effect = None
        adapter.create_media_buy.return_value = CreateMediaBuyError(
            errors=[Error(code="ADAPTER_ERROR", message=f"adapter {recovery} failure", recovery=recovery)],
            context=None,
        )
        return env.call_impl(
            brand={"domain": "transient-test.example.com"},
            packages=[{"product_id": product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            start_time="2026-06-01T00:00:00Z",
            end_time="2026-06-30T00:00:00Z",
            po_number="TRANSIENT-1",
            idempotency_key=idempotency_key,
        )

    def test_transient_adapter_rejection_is_not_cached(self, integration_db):
        """recovery='transient' adapter rejection → NO IdempotencyAttempt row written."""
        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import CreateMediaBuyError, CreateMediaBuyResult

        idem_key = f"transient-{uuid.uuid4().hex[:8]}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            result = self._drive_adapter_rejection(
                env, recovery="transient", idempotency_key=idem_key, product_id=product.product_id
            )
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        # The flow reached the adapter-rejection branch (failed CreateMediaBuyError).
        assert isinstance(result, CreateMediaBuyResult)
        assert result.status == "failed"
        assert isinstance(result.response, CreateMediaBuyError)

        # Transient rejection must NOT be cached — a retry should re-evaluate.
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=idem_key,
            )
            assert cached is None, "Transient adapter rejection must not be cached (retry must re-evaluate)"

    def test_non_transient_adapter_rejection_is_cached(self, integration_db):
        """recovery='terminal' adapter rejection → IdempotencyAttempt row IS written.

        Same setup as the transient case; only the recovery hint differs. This
        is the control that proves the skip is recovery-specific, not a blanket
        "adapter rejections are never cached".
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import CreateMediaBuyError, CreateMediaBuyResult

        idem_key = f"terminal-{uuid.uuid4().hex[:8]}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            result = self._drive_adapter_rejection(
                env, recovery="terminal", idempotency_key=idem_key, product_id=product.product_id
            )
            tenant_id = env._tenant_id
            principal_id = env._principal_id

        assert isinstance(result, CreateMediaBuyResult)
        assert result.status == "failed"
        assert isinstance(result.response, CreateMediaBuyError)

        # Non-transient rejection IS cached so a retry replays the same answer.
        # Read the envelope inside the UoW block — the ORM row is detached after exit.
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached = uow.idempotency_attempts.find_by_key(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=idem_key,
            )
            assert cached is not None, "Non-transient adapter rejection must be cached for replay"
            cached_errors = cached.response_envelope.get("errors")
            assert cached_errors, f"Cached envelope must carry errors[]. Got {cached.response_envelope!r}"
            assert cached_errors[0]["code"] == "ADAPTER_ERROR"
