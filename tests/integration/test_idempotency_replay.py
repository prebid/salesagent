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
            assert (
                "seeded message" not in messages
            ), f"Replay incorrectly served seeded envelope for unrelated key: {messages}"


class TestWirePathReplay:
    """Wire-path proof: ``idempotency_key`` survives MCP / A2A / REST wrappers.

    Konstantine review on PR #1312 (2026-05-24): the existing 21 idempotency
    tests stayed green when the wrappers silently dropped ``idempotency_key``
    via ``TypeAdapter`` because they exercise only the ``_impl`` layer. These
    three tests close that gap: each seeds a cached rejection envelope,
    sends an ``idempotency_key`` *through the wrapper*, and asserts the
    cached envelope comes back on the wire. If a future change drops
    ``idempotency_key`` from any wrapper signature, the matching test
    breaks immediately.
    """

    def _seed_rejection(
        self,
        tenant_id: str,
        principal_id: str,
        idempotency_key: str,
        message: str,
    ) -> None:
        """Seed a cached rejection envelope for (tenant, principal, key).

        Mirrors the seeding pattern at line 221-228 of
        ``TestImplReplaysCachedRejection.test_cached_rejection_returned_on_replay``.
        """
        from src.core.database.repositories import MediaBuyUoW
        from src.core.schemas import CreateMediaBuyError, Error

        rejection = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message=message, details=None)],
            context=None,
        )
        with MediaBuyUoW(tenant_id) as seed_uow:
            assert seed_uow.idempotency_attempts is not None
            seed_uow.idempotency_attempts.record_rejection(
                principal_id=principal_id,
                tool_name="create_media_buy",
                idempotency_key=idempotency_key,
                response_envelope=rejection.model_dump(mode="json"),
            )

    def _build_identity(self, tenant_id: str, principal_id: str, protocol: str):
        """Build an identity that bypasses setup validation (test_session_id set)."""
        from src.core.testing_hooks import AdCPTestContext
        from tests.factories import PrincipalFactory

        return PrincipalFactory.make_identity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            protocol=protocol,
            testing_context=AdCPTestContext(test_session_id="wire_path_replay"),
        )

    def _bootstrap_tenant(self, tenant_id: str, principal_id: str) -> None:
        from tests.factories import PrincipalFactory, TenantFactory

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            PrincipalFactory(tenant=tenant, principal_id=principal_id)
            env.get_session()

    def test_mcp_wire_replays_cached_rejection(self, integration_db):
        """MCP wrapper forwards idempotency_key → impl replays cached envelope on wire.

        Regression guard: if ``create_media_buy`` MCP wrapper at
        ``src/core/tools/media_buy_create.py:4018`` stops declaring
        ``idempotency_key``, FastMCP's TypeAdapter strips the field before
        the wrapper runs, the impl never sees the key, the rejection
        replay is bypassed, and this test fails.
        """
        import asyncio
        import json
        from unittest.mock import patch

        from fastmcp import Client

        from src.core.main import mcp

        idem_key = f"wire-mcp-{uuid.uuid4().hex[:8]}"
        tenant_id = f"wire_mcp_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        cached_message = "wire-MCP cached rejection — must round-trip"

        self._bootstrap_tenant(tenant_id, principal_id)
        self._seed_rejection(tenant_id, principal_id, idem_key, cached_message)

        identity = self._build_identity(tenant_id, principal_id, "mcp")

        async def _call() -> tuple[bool, dict | None, str | None]:
            with patch(
                "src.core.mcp_auth_middleware.resolve_identity_from_context",
                return_value=identity,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "create_media_buy",
                        {
                            "brand": {"domain": "wire-mcp.example.com"},
                            "packages": [],
                            "start_time": "2026-06-01T00:00:00Z",
                            "end_time": "2026-06-30T00:00:00Z",
                            "po_number": "WIRE-MCP-1",
                            "idempotency_key": idem_key,
                        },
                        raise_on_error=False,
                    )
                    text = None
                    if result.content:
                        for c in result.content:
                            if hasattr(c, "text"):
                                text = c.text
                                break
                    return result.is_error, result.structured_content, text

        is_error, structured, text = asyncio.run(_call())

        # Replay returns a failed CreateMediaBuyResult, which is a *successful*
        # tool call carrying a domain error — NOT an MCP tool error.
        assert not is_error, (
            f"Replay should surface as successful tool call with domain errors, "
            f"not as ToolError. Got is_error=True, text={text!r}"
        )
        assert structured is not None, "MCP wire must include structured_content"

        # Pull the envelope from structured_content (preferred) or content text.
        envelope = structured if structured else json.loads(text or "{}")

        # Cached envelope shape: status=failed, response.errors=[VALIDATION_ERROR]
        assert envelope.get("status") == "failed", (
            f"Cached rejection must surface as status=failed. Got status={envelope.get('status')!r}, "
            f"keys={sorted(envelope.keys())}"
        )
        errors = envelope.get("errors")
        assert errors, f"Wire envelope must carry errors[]. Got envelope={envelope!r}"
        assert (
            errors[0]["code"] == "VALIDATION_ERROR"
        ), f"errors[0].code must match cached envelope. Got {errors[0].get('code')!r}"
        assert errors[0]["message"] == cached_message, (
            f"errors[0].message must be byte-identical to cached envelope. "
            f"Got {errors[0].get('message')!r}, expected {cached_message!r}"
        )

    async def test_a2a_wire_replays_cached_rejection(self, integration_db):
        """A2A wrapper forwards idempotency_key → impl replays cached envelope on wire.

        Regression guard: if ``adcp_a2a_server.py:1543`` (the
        ``idempotency_key=params.get("idempotency_key")`` forwarding line)
        is changed to ``idempotency_key=None``, the impl never sees the
        key, the rejection replay is bypassed, and this test fails.
        """
        from unittest.mock import MagicMock

        from a2a.server.routes.common import ServerCallContext
        from a2a.types import SendMessageRequest, Task

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.config_loader import set_current_tenant
        from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

        idem_key = f"wire-a2a-{uuid.uuid4().hex[:8]}"
        tenant_id = f"wire_a2a_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        cached_message = "wire-A2A cached rejection — must round-trip"
        auth_token = f"tok-{uuid.uuid4().hex[:8]}"

        self._bootstrap_tenant(tenant_id, principal_id)
        self._seed_rejection(tenant_id, principal_id, idem_key, cached_message)

        identity = self._build_identity(tenant_id, principal_id, "a2a")
        # auth_token is needed for the resolved identity in A2A path
        identity = identity.__class__(
            principal_id=identity.principal_id,
            tenant_id=identity.tenant_id,
            tenant=identity.tenant,
            auth_token=auth_token,
            protocol="a2a",
            testing_context=identity.testing_context,
        )

        handler = AdCPRequestHandler()
        handler._get_auth_token = MagicMock(return_value=auth_token)
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        set_current_tenant(identity.tenant)

        skill_params = {
            "brand": {"domain": "wire-a2a.example.com"},
            "packages": [],
            "start_time": "2026-06-01T00:00:00Z",
            "end_time": "2026-06-30T00:00:00Z",
            "po_number": "WIRE-A2A-1",
            "idempotency_key": idem_key,
        }
        message = create_a2a_message_with_skill("create_media_buy", skill_params)
        params = SendMessageRequest(message=message)

        result = await handler.on_message_send(params, ServerCallContext())

        assert isinstance(result, Task), f"on_message_send must return Task, got {type(result).__name__}"
        assert result.artifacts, f"Wire response must include artifacts. Task={result!r}"

        artifact_data = extract_data_from_artifact(result.artifacts[0])

        # Cached rejection: success=False, errors[0] matches seeded envelope
        assert artifact_data.get("success") is False, (
            f"Cached rejection must surface as success=False on A2A wire. "
            f"Got success={artifact_data.get('success')!r}, keys={sorted(artifact_data.keys())}"
        )
        errors = artifact_data.get("errors")
        assert errors, f"A2A wire envelope must carry errors[]. Got artifact={artifact_data!r}"
        assert (
            errors[0]["code"] == "VALIDATION_ERROR"
        ), f"errors[0].code must match cached envelope. Got {errors[0].get('code')!r}"
        assert errors[0]["message"] == cached_message, (
            f"errors[0].message must be byte-identical to cached envelope. "
            f"Got {errors[0].get('message')!r}, expected {cached_message!r}"
        )

    def test_rest_wire_replays_cached_rejection(self, integration_db):
        """REST wrapper forwards idempotency_key → impl replays cached envelope on wire.

        Regression guard: if ``CreateMediaBuyBody`` at ``src/routes/api_v1.py:81``
        drops ``idempotency_key`` (or the route at L237 stops passing it through),
        the impl never sees the key, the rejection replay is bypassed, and
        this test fails.
        """
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.auth_context import _require_auth_dep, _resolve_auth_dep

        idem_key = f"wire-rest-{uuid.uuid4().hex[:8]}"
        tenant_id = f"wire_rest_t_{uuid.uuid4().hex[:6]}"
        principal_id = f"p_{uuid.uuid4().hex[:8]}"
        cached_message = "wire-REST cached rejection — must round-trip"

        self._bootstrap_tenant(tenant_id, principal_id)
        self._seed_rejection(tenant_id, principal_id, idem_key, cached_message)

        identity = self._build_identity(tenant_id, principal_id, "rest")

        # Inject identity via FastAPI dep overrides (bypasses middleware/header parsing).
        app.dependency_overrides[_require_auth_dep] = lambda: identity
        app.dependency_overrides[_resolve_auth_dep] = lambda: identity
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/v1/media-buys",
                json={
                    "brand": {"domain": "wire-rest.example.com"},
                    "packages": [],
                    "start_time": "2026-06-01T00:00:00Z",
                    "end_time": "2026-06-30T00:00:00Z",
                    "po_number": "WIRE-REST-1",
                    "idempotency_key": idem_key,
                },
            )
        finally:
            app.dependency_overrides.pop(_require_auth_dep, None)
            app.dependency_overrides.pop(_resolve_auth_dep, None)

        assert response.status_code == 200, (
            f"Replay must return 200 (successful response with domain errors), "
            f"got {response.status_code}. Body: {response.text!r}"
        )
        body = response.json()
        assert body.get("status") == "failed", (
            f"Cached rejection must surface as status=failed. Got status={body.get('status')!r}, "
            f"keys={sorted(body.keys())}"
        )
        errors = body.get("errors")
        assert errors, f"REST wire envelope must carry errors[]. Got body={body!r}"
        assert (
            errors[0]["code"] == "VALIDATION_ERROR"
        ), f"errors[0].code must match cached envelope. Got {errors[0].get('code')!r}"
        assert errors[0]["message"] == cached_message, (
            f"errors[0].message must be byte-identical to cached envelope. "
            f"Got {errors[0].get('message')!r}, expected {cached_message!r}"
        )
