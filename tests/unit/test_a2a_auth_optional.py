#!/usr/bin/env python3
"""
Unit tests for A2A auth-optional discovery endpoints.

Tests that discovery endpoints (list_creative_formats, list_authorized_properties, get_products)
properly handle both authenticated and unauthenticated requests according to AdCP spec.

After the identity-at-transport-boundary refactor (salesagent-anjp), handlers receive
a pre-resolved identity parameter rather than resolving auth internally.
"""

from unittest.mock import ANY, AsyncMock, patch

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.exceptions import AdCPAuthenticationError
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
        assert exc_info.value.error_code == "AUTH_TOKEN_INVALID"
        assert exc_info.value.recovery == "terminal"

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

        assert exc_info.value.error_code == "AUTH_TOKEN_INVALID"
        assert "supported_versions" not in (exc_info.value.details or {})

    def test_missing_token_raises_typed_authentication_error_at_resolver(self):
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            self.handler._resolve_a2a_identity(None, require_valid_token=True)

        assert exc_info.value.error_code == "AUTH_TOKEN_INVALID"
        assert exc_info.value.recovery == "terminal"

    @pytest.mark.asyncio
    async def test_auth_failure_never_sends_or_retains_attacker_push_callback(self):
        """Pre-auth callback data cannot reach the generic task-failure webhook path."""
        from a2a.server.routes.common import ServerCallContext
        from a2a.types import (
            InternalError,
            SendMessageConfiguration,
            SendMessageRequest,
            TaskPushNotificationConfig,
        )

        from tests.helpers import assert_envelope_shape
        from tests.utils.a2a_helpers import create_a2a_message_with_skill

        params = SendMessageRequest(
            message=create_a2a_message_with_skill(
                "update_media_buy",
                {"adcp_version": "4.0", "media_buy_id": "mb_1"},
            ),
            configuration=SendMessageConfiguration(
                task_push_notification_config=TaskPushNotificationConfig(url="https://attacker.example/webhook")
            ),
        )

        with (
            patch("src.a2a_server.adcp_a2a_server.record_boundary_error_for_identity") as record_error,
            patch.object(self.handler, "_send_protocol_webhook", new_callable=AsyncMock) as send_webhook,
            pytest.raises(InternalError) as exc_info,
        ):
            await self.handler.on_message_send(params, ServerCallContext())

        assert_envelope_shape(exc_info.value.data, "AUTH_TOKEN_INVALID", recovery="terminal")
        send_webhook.assert_not_awaited()
        # The auth failure routes through the identity-aware helper with the None
        # identity resolved at the boundary — so tenant_id degrades to None and the
        # activity-feed + audit writes are skipped. A regression that hand-rolled a
        # fabricated "unknown" tenant (truthy) would drive those sinks for an
        # unauthenticated caller; asserting the identity-aware call with None pins that.
        record_error.assert_called_once_with("a2a", "message_processing", ANY, None)
        assert self.handler.tasks == {}
        assert self.handler._task_push_configs == {}

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

        # Routed through the identity-aware helper with the unresolved (None) identity…
        record_error.assert_called_once_with("a2a", "message_processing", ANY, None)
        # …and never through the raw sink with a fabricated "unknown" tenant.
        record_raw.assert_not_called()

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
