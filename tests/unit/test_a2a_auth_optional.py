#!/usr/bin/env python3
"""
Unit tests for A2A auth-optional discovery endpoints.

Tests that discovery endpoints (list_creative_formats, list_authorized_properties, get_products)
properly handle both authenticated and unauthenticated requests according to AdCP spec.

After the identity-at-transport-boundary refactor (salesagent-anjp), handlers receive
a pre-resolved identity parameter rather than resolving auth internally.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, _unambiguous_invocation_context
from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError, AdCPVersionUnsupportedError
from tests.factories.principal import PrincipalFactory


class TestAuthOptionalSkills:
    """Test auth-optional skill handling in A2A server."""

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()
        self.mock_identity = PrincipalFactory.make_identity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )
        self.anon_identity = PrincipalFactory.make_identity(
            principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

    _STANDARD_READ_HANDLERS = (
        ("get_adcp_capabilities", "_handle_get_adcp_capabilities_skill"),
        ("get_media_buy_delivery", "_handle_get_media_buy_delivery_skill"),
        ("get_media_buys", "_handle_get_media_buys_skill"),
        ("get_products", "_handle_get_products_skill"),
        ("list_accounts", "_handle_list_accounts_skill"),
        ("list_creative_formats", "_handle_list_creative_formats_skill"),
        ("list_creatives", "_handle_list_creatives_skill"),
    )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("skill_name", "handler_name"), _STANDARD_READ_HANDLERS)
    @pytest.mark.parametrize(
        "parameters",
        ({}, {"idempotency_key": "valid-read-key-0001"}),
        ids=("omitted-grace", "valid-supplied"),
    )
    async def test_standard_read_key_is_omitted_or_consumed_before_handler(
        self,
        skill_name,
        handler_name,
        parameters,
    ):
        """All A2A-exposed reads see neither an omitted nor a valid inert key."""
        handler_stub = AsyncMock(return_value={"ok": True})

        with patch.object(self.handler, handler_name, handler_stub):
            result = await self.handler._handle_explicit_skill(
                skill_name,
                parameters,
                self.mock_identity,
            )

        assert result == {"ok": True}
        handler_stub.assert_awaited_once_with({}, self.mock_identity)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("skill_name", "handler_name"), _STANDARD_READ_HANDLERS)
    async def test_explicit_null_read_key_rejects_before_every_handler(self, skill_name, handler_name):
        handler_stub = AsyncMock(return_value={"ok": True})

        with (
            patch.object(self.handler, handler_name, handler_stub),
            pytest.raises(AdCPValidationError, match="idempotency_key must be a string"),
        ):
            await self.handler._handle_explicit_skill(
                skill_name,
                {"idempotency_key": None},
                self.mock_identity,
            )

        handler_stub.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_authentication_precedes_malformed_read_key(self):
        with pytest.raises(AdCPAuthenticationError):
            await self.handler._handle_explicit_skill(
                "get_media_buys",
                {"idempotency_key": None},
                None,
            )

    @pytest.mark.asyncio
    async def test_version_precedes_malformed_read_key(self):
        with pytest.raises(AdCPVersionUnsupportedError):
            await self.handler._handle_explicit_skill(
                "get_products",
                {"adcp_version": "4.0", "idempotency_key": None},
                None,
            )

    @pytest.mark.asyncio
    async def test_list_creative_formats_without_auth(self):
        """list_creative_formats should work with anonymous identity (no principal)."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool") as mock_tool:
            mock_tool.return_value = {"formats": []}

            result = await self.handler._handle_list_creative_formats_skill(parameters={}, identity=self.anon_identity)

            assert result is not None
            assert "formats" in result
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_creative_formats_with_auth(self):
        """list_creative_formats should work with authenticated identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool") as mock_tool:
            mock_tool.return_value = {"formats": []}

            result = await self.handler._handle_list_creative_formats_skill(parameters={}, identity=self.mock_identity)

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_authorized_properties_without_auth(self):
        """list_authorized_properties should work with anonymous identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool") as mock_tool:
            mock_tool.return_value = {"publisher_domains": []}

            result = await self.handler._handle_list_authorized_properties_skill(
                parameters={}, identity=self.anon_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_authorized_properties_with_auth(self):
        """list_authorized_properties should work with authenticated identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool") as mock_tool:
            mock_tool.return_value = {"publisher_domains": []}

            result = await self.handler._handle_list_authorized_properties_skill(
                parameters={}, identity=self.mock_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_products_without_auth(self):
        """get_products should work with anonymous identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool:
            mock_tool.return_value = {"products": []}

            result = await self.handler._handle_get_products_skill(
                parameters={"brief": "test campaign"}, identity=self.anon_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_products_with_auth(self):
        """get_products should work with authenticated identity."""
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool:
            mock_tool.return_value = {"products": []}

            result = await self.handler._handle_get_products_skill(
                parameters={"brief": "test campaign"}, identity=self.mock_identity
            )

            assert result is not None
            mock_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_media_buy_requires_auth(self):
        """create_media_buy should reject None identity (not a discovery endpoint)."""
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy", parameters={"product_ids": ["prod_1"]}, identity=None
            )

        assert "Authentication required" in str(exc_info.value)
        assert exc_info.value.error_code == "AUTH_REQUIRED"
        assert exc_info.value.recovery == "correctable"

    @pytest.mark.asyncio
    async def test_update_media_buy_requires_auth(self):
        """update_media_buy should reject None identity."""
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="update_media_buy", parameters={"media_buy_id": "mb_1"}, identity=None
            )

        assert "Authentication required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_authentication_precedes_version_for_protected_skill(self):
        """The defensive dispatcher guard must not disclose seller versions before auth."""
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="update_media_buy",
                parameters={"adcp_version": "4.0", "media_buy_id": "mb_1"},
                identity=None,
            )

        assert exc_info.value.error_code == "AUTH_REQUIRED"
        assert "supported_versions" not in (exc_info.value.details or {})

    def test_missing_token_raises_typed_authentication_error_at_resolver(self):
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            self.handler._resolve_a2a_identity(None, require_valid_token=True)

        assert exc_info.value.error_code == "AUTH_REQUIRED"
        assert exc_info.value.recovery == "correctable"

    @pytest.mark.asyncio
    async def test_auth_failure_never_sends_or_retains_attacker_push_callback(self):
        """Pre-auth callback data cannot reach the generic task-failure webhook path."""
        from a2a.server.routes.common import ServerCallContext
        from a2a.types import (
            InvalidRequestError,
            SendMessageConfiguration,
            SendMessageRequest,
            TaskPushNotificationConfig,
        )

        from tests.helpers import assert_envelope_shape
        from tests.utils.a2a_helpers import create_a2a_message_with_skill

        application_context = {
            "correlation_id": "a2a-missing-auth-context",
            "nullable": None,
        }
        params = SendMessageRequest(
            message=create_a2a_message_with_skill(
                "update_media_buy",
                {
                    "adcp_version": "4.0",
                    "media_buy_id": "mb_1",
                    "context": application_context,
                },
            ),
            configuration=SendMessageConfiguration(
                task_push_notification_config=TaskPushNotificationConfig(url="https://attacker.example/webhook")
            ),
        )

        with (
            patch("src.a2a_server.adcp_a2a_server.record_boundary_error_for_identity") as record_error,
            patch.object(self.handler, "_send_protocol_webhook", new_callable=AsyncMock) as send_webhook,
            pytest.raises(InvalidRequestError) as exc_info,
        ):
            await self.handler.on_message_send(params, ServerCallContext())

        # The missing-token rejection is a protocol-level JSON-RPC error raised
        # BEFORE identity resolution, task creation, or callback persistence
        # (#1417) — carrying the buyer-facing two-layer envelope in ``data``.
        assert_envelope_shape(exc_info.value.data, "AUTH_REQUIRED", recovery="correctable")
        assert exc_info.value.data["context"] == application_context
        send_webhook.assert_not_awaited()
        # Pre-auth rejection short-circuits before the boundary-telemetry helper:
        # nothing is recorded for an unauthenticated sender, so no tenant-scoped
        # sink can fire for the attacker (stronger than the previous flow, which
        # relied on the helper degrading tenant_id to None).
        record_error.assert_not_called()
        assert self.handler.tasks == {}
        assert self.handler._task_push_configs == {}

    @pytest.mark.asyncio
    async def test_invalid_token_error_echoes_unambiguous_application_context(self):
        """Identity-resolution failures retain context in JSON-RPC error data."""
        from a2a.server.routes.common import ServerCallContext
        from a2a.types import SendMessageRequest
        from a2a.utils.errors import InvalidRequestError

        from tests.helpers import assert_envelope_shape
        from tests.utils.a2a_helpers import create_a2a_message_with_skill

        application_context = {
            "correlation_id": "a2a-invalid-auth-context",
            "nullable": None,
        }
        params = SendMessageRequest(
            message=create_a2a_message_with_skill(
                "update_media_buy",
                {
                    "media_buy_id": "mb_1",
                    "context": application_context,
                },
            )
        )

        with (
            patch.object(self.handler, "_get_auth_token", return_value="invalid-token"),
            patch.object(
                self.handler,
                "_resolve_a2a_identity",
                side_effect=AdCPAuthenticationError("Invalid authentication token"),
            ),
            patch("src.a2a_server.adcp_a2a_server.record_boundary_error_for_identity"),
            # Class symmetry with the missing-token rejection: an invalid bearer
            # is a CLIENT auth error → JSON-RPC InvalidRequestError, never the
            # server-fault InternalError (-32603).
            pytest.raises(InvalidRequestError) as exc_info,
        ):
            await self.handler.on_message_send(params, ServerCallContext())

        assert_envelope_shape(exc_info.value.data, "AUTH_REQUIRED", recovery="correctable")
        assert exc_info.value.data["context"] == application_context

    def test_multi_skill_context_is_echoed_only_when_unambiguous(self):
        shared = {"correlation_id": "shared", "nullable": None}
        assert (
            _unambiguous_invocation_context(
                [
                    {"skill": "one", "parameters": {"context": shared}},
                    {"skill": "two", "parameters": {}},
                    {"skill": "three", "parameters": {"context": dict(shared)}},
                ]
            )
            == shared
        )
        assert (
            _unambiguous_invocation_context(
                [
                    {"skill": "one", "parameters": {"context": shared}},
                    {"skill": "two", "parameters": {"context": {"correlation_id": "other"}}},
                ]
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_generic_processing_error_with_no_identity_skips_tenant_scoped_sinks(self):
        """A non-auth failure with an unresolved identity must not fabricate a tenant.

        The generic ``except Exception`` handler in ``on_message_send`` is reachable
        with ``identity`` still ``None`` — e.g. identity resolution itself raises a
        non-``AdCPAuthenticationError`` (a transient tenant-lookup failure). It must
        route through ``record_boundary_error_for_identity`` so ``tenant_id`` degrades
        to ``None`` and the activity-feed + audit writes are skipped, exactly like the
        auth branch above. A regression that hand-rolled a fabricated ``"unknown"``
        tenant (truthy) would drive those tenant-scoped sinks for a caller with no
        resolved tenant; pinning the identity-aware call with ``None`` catches it.
        """
        from a2a.server.routes.common import ServerCallContext
        from a2a.types import InternalError, SendMessageRequest

        from tests.utils.a2a_helpers import create_a2a_message_with_skill

        # A discovery skill needs no auth, so identity resolution runs for an
        # anonymous caller; we make that resolution raise a non-auth error so the
        # generic handler fires before ``identity`` is ever assigned.
        params = SendMessageRequest(message=create_a2a_message_with_skill("get_products", {"brief": "video"}))

        with (
            patch.object(
                self.handler,
                "_resolve_a2a_identity",
                side_effect=RuntimeError("tenant lookup failed"),
            ),
            patch("src.a2a_server.adcp_a2a_server.record_boundary_error_for_identity") as record_error,
            patch("src.a2a_server.adcp_a2a_server.record_boundary_error") as record_raw,
            patch.object(self.handler, "_send_protocol_webhook", new_callable=AsyncMock),
            pytest.raises(InternalError),
        ):
            await self.handler.on_message_send(params, ServerCallContext())

        # Routed through the identity-aware helper with the unresolved (None) identity.
        # The sink receives the NORMALIZED typed error — pin its type and that it
        # carries the original failure text, not a bare ANY (which would pass a
        # wrong exception object straight through).
        class _NormalizedBoom:
            def __eq__(self, other: object) -> bool:
                from src.core.exceptions import AdCPError

                return isinstance(other, AdCPError) and "tenant lookup failed" in str(other)

            def __repr__(self) -> str:
                return "<AdCPError containing 'tenant lookup failed'>"

        record_error.assert_called_once_with("a2a", "message_processing", _NormalizedBoom(), None)
        # …and never through the raw sink with a fabricated "unknown" tenant.
        record_raw.assert_not_called()

    @pytest.mark.asyncio
    async def test_anonymous_discovery_callback_is_refused_and_never_persisted(self):
        """Anonymous discovery cannot register even an otherwise-safe callback (#1512).

        The prior regression covered a *rejected-auth* request dropping its callback.
        This closes the sibling gap: an auth-optional discovery request that resolves
        an anonymous identity SUCCESSFULLY and carries a callback. URL validation is
        made to pass so this test proves the independent authentication gate refuses
        the callback before storage.

        The rejection surfaces as a buyer-CORRECTABLE FAILED Task (not a JSON-RPC
        InternalError) — the wire-envelope shape is pinned by the raw-wire test in
        tests/integration/test_a2a_error_responses.py (#1512).
        """
        from a2a.server.routes.common import ServerCallContext
        from a2a.types import (
            SendMessageConfiguration,
            SendMessageRequest,
            Task,
            TaskPushNotificationConfig,
            TaskState,
        )

        from tests.helpers import assert_envelope_shape
        from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

        params = SendMessageRequest(
            message=create_a2a_message_with_skill("get_adcp_capabilities", {}),
            configuration=SendMessageConfiguration(
                task_push_notification_config=TaskPushNotificationConfig(url="https://buyer.example/webhook")
            ),
        )

        with (
            patch.object(self.handler, "_resolve_a2a_identity", return_value=self.anon_identity),
            patch(
                "src.core.webhook_validator._validate_callback_url_with_policy",
                return_value=(True, ""),
            ) as validate_url,
        ):
            result = await self.handler.on_message_send(params, ServerCallContext())

        # Buyer-correctable FAILED task, NOT an InternalError; callback never stored so
        # the status/failure webhook has nothing to deliver.
        assert isinstance(result, Task)
        assert result.status.state == TaskState.TASK_STATE_FAILED
        assert result.artifacts
        envelope = extract_data_from_artifact(result.artifacts[0])
        assert_envelope_shape(
            envelope,
            "VALIDATION_ERROR",
            message_substr="requires authentication",
            recovery="correctable",
        )
        validate_url.assert_called_once_with("https://buyer.example/webhook", allow_private=False)
        assert self.handler._task_push_configs == {}

    def test_validate_push_callback_rejects_ssrf_url_for_authenticated_caller(self):
        """Even an authenticated caller cannot register an internal/metadata callback URL (#1512)."""
        from a2a.types import TaskPushNotificationConfig

        config = TaskPushNotificationConfig(url="https://169.254.169.254/latest/meta-data")
        with pytest.raises(AdCPValidationError) as exc:
            self.handler._validate_push_callback(config, self.mock_identity)
        assert "SSRF" in str(exc.value)
        assert exc.value.field == "push_notification_config.url"
        assert exc.value.suggestion == "Supply a publicly routable HTTPS callback URL without embedded credentials."

    def test_validate_push_callback_allows_safe_url_for_authenticated_caller(self):
        """A safe callback URL from an authenticated caller is accepted (no over-rejection)."""
        from a2a.types import TaskPushNotificationConfig

        config = TaskPushNotificationConfig(url="https://buyer.example.com/webhook")
        with patch(
            "src.core.webhook_validator._validate_callback_url_with_policy",
            return_value=(True, ""),
        ):
            self.handler._validate_push_callback(config, self.mock_identity)  # must not raise

    @pytest.mark.asyncio
    async def test_send_protocol_webhook_skips_ssrf_url_at_delivery(self):
        """Delivery re-validates the callback URL and skips an SSRF target (DNS-rebinding/TOCTOU, #1512)."""
        from a2a.types import Task, TaskPushNotificationConfig, TaskState, TaskStatus

        task = Task(id="task_ssrf", context_id="ctx", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
        # Simulate a callback that reached storage (e.g. via a non-on_message_send path,
        # or a hostname that only now resolves to a link-local address).
        self.handler._task_push_configs["task_ssrf"] = TaskPushNotificationConfig(
            url="https://169.254.169.254/latest/meta-data"
        )

        with patch("src.a2a_server.adcp_a2a_server.get_protocol_webhook_service") as mock_service:
            mock_service.return_value.send_notification = AsyncMock()
            await self.handler._send_protocol_webhook(task, status="completed")
            mock_service.return_value.send_notification.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discovery_skills_accept_anonymous_identity(self):
        """Discovery skills should accept anonymous identity (no principal_id)."""
        discovery_skills = {
            "list_creative_formats": "src.a2a_server.adcp_a2a_server.core_list_creative_formats_tool",
            "list_authorized_properties": "src.a2a_server.adcp_a2a_server.core_list_authorized_properties_tool",
            "get_products": "src.a2a_server.adcp_a2a_server.core_get_products_tool",
        }

        for skill_name, mock_path in discovery_skills.items():
            with patch(mock_path) as mock_tool:
                mock_tool.return_value = {}
                await self.handler._handle_explicit_skill(
                    skill_name=skill_name,
                    parameters={"brief": "test"} if skill_name == "get_products" else {},
                    identity=self.anon_identity,
                )

    @pytest.mark.asyncio
    async def test_natural_language_without_auth(self):
        """Natural language requests (empty skill_invocations) should not require auth.

        With the identity-at-transport-boundary refactor, on_message_send resolves
        identity at the transport boundary. NL requests with no auth get
        requires_auth=False, so identity resolution succeeds with anonymous identity.
        """
        # Build a real protobuf SendMessageRequest with NL text
        from a2a.server.routes.common import ServerCallContext
        from a2a.types import Message, Part, Role, SendMessageRequest

        message = Message(
            message_id="test_msg_1",
            context_id="test_ctx_1",
            role=Role.ROLE_USER,
        )
        message.parts.append(Part(text="show me available products"))
        params = SendMessageRequest(message=message)

        # Mock _get_auth_token to return None (no auth)
        with patch.object(self.handler, "_get_auth_token", return_value=None):
            # Mock _resolve_a2a_identity to return anonymous identity
            with patch.object(self.handler, "_resolve_a2a_identity", return_value=self.anon_identity):
                # Mock the _get_products method that would be called for natural language
                with patch.object(self.handler, "_get_products", new_callable=AsyncMock) as mock_products:
                    mock_products.return_value = {"products": []}

                    result = await self.handler.on_message_send(params, context=ServerCallContext())
                    assert result is not None
