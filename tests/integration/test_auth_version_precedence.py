"""Compound-error precedence: AUTH before VERSION on every transport (#1546 review).

Canonical decision: on a request that is BOTH unauthenticated (bad/missing
token) AND pins an unsupported AdCP version, the seller rejects AUTH first — an
unauthenticated caller is not told what versions the agent supports (a
VERSION_UNSUPPORTED body carries ``supported_versions``). Before this change the
order flipped per transport: REST rejected VERSION before AUTH; MCP flipped on
missing-vs-invalid token. It is now uniform:

- REST: version validation is a per-route dependency chained AFTER ``require_auth``
  (src/routes/api_v1.py ``_version_after_require``).
- MCP: MCPAuthMiddleware runs before RequestCompatMiddleware and now rejects a
  principal-less identity (missing token) as well as an invalid one, so the
  version check (call_next) is never reached for an unauthenticated caller.
- A2A: on_message_send resolves+enforces auth before ``_handle_explicit_skill``
  (where the version pin is validated) — already AUTH-first.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers import assert_envelope_shape
from tests.integration.test_bearer_across_transports import _build_jsonrpc_skill

# An auth-required op that also carries an unsupported version pin — the compound error.
_UNSUPPORTED_PIN = "4.0"


def _assert_no_version_disclosure(target: Any) -> None:
    serialized = json.dumps(target, sort_keys=True)
    assert "VERSION_UNSUPPORTED" not in serialized
    assert "supported_versions" not in serialized


def _assert_auth_without_version_disclosure(target: Any) -> None:
    """Assert canonical AUTH rejection without leaking negotiation metadata."""
    assert_envelope_shape(target, "AUTH_REQUIRED", recovery="correctable")
    envelope = target.envelope if hasattr(target, "envelope") else target
    _assert_no_version_disclosure(envelope)


@pytest.mark.requires_db
class TestAuthBeforeVersionOnRestWire:
    """REST rejects AUTH before VERSION on a compound-error request."""

    @pytest.mark.parametrize("authorization", [None, "Bearer definitely-not-a-real-token"], ids=["missing", "invalid"])
    def test_compound_error_returns_auth_not_version(self, integration_db, authorization):
        from starlette.testclient import TestClient

        from src.app import app
        from tests.harness.capabilities import CapabilitiesEnv

        with CapabilitiesEnv() as env:
            tenant, _principal = env.setup_default_data()

            headers = {"x-adcp-tenant": tenant.tenant_id}
            if authorization:
                headers["Authorization"] = authorization

            client = TestClient(app, raise_server_exceptions=False)
            # media-buys is auth-required; the body pins an unsupported version.
            response = client.post(
                "/api/v1/media-buys",
                json={"adcp_version": _UNSUPPORTED_PIN, "packages": []},
                headers=headers,
            )

            assert response.status_code == 401, f"Expected AUTH (401), got {response.status_code}: {response.text}"
            _assert_auth_without_version_disclosure(response.json())


@pytest.mark.requires_db
class TestAuthBeforeVersionOnA2AWire:
    """A2A rejects AUTH before VERSION on a compound-error request."""

    def test_compound_error_returns_auth_not_version(self, integration_db):
        from starlette.testclient import TestClient

        from src.app import app
        from tests.harness.capabilities import CapabilitiesEnv

        with CapabilitiesEnv() as env:
            tenant, _principal = env.setup_default_data()

            client = TestClient(app, raise_server_exceptions=False)
            # get_media_buys is auth-required; parameters pin an unsupported version.
            response = client.post(
                "/a2a",
                json=_build_jsonrpc_skill("get_media_buys", {"adcp_version": _UNSUPPORTED_PIN}),
                headers={
                    "Authorization": "Bearer definitely-not-a-real-token",
                    "x-adcp-tenant": tenant.tenant_id,
                    "Content-Type": "application/json",
                    "A2A-Version": "1.0",
                },
            )

            body = response.json()
            assert "error" in body, f"AUTH must win over VERSION on the compound error, got {body}"
            assert "data" in body["error"], f"A2A auth error omitted its AdCP envelope: {body}"
            _assert_auth_without_version_disclosure(body["error"]["data"])
            _assert_no_version_disclosure(body)


class TestAuthBeforeVersionOnMcpMiddleware:
    """MCP rejects AUTH before the version check (RequestCompatMiddleware) runs.

    MCPAuthMiddleware is the OUTER middleware; when it rejects auth it never
    calls ``call_next`` — so RequestCompatMiddleware (the version gate) is never
    reached even though the arguments carry an unsupported pin.
    """

    @pytest.mark.asyncio
    async def test_auth_rejection_short_circuits_version_check(self):
        from src.core.exceptions import AdCPAuthenticationError
        from src.core.mcp_auth_middleware import MCPAuthMiddleware
        from src.core.tool_error_logging import AdCPToolError

        fastmcp_ctx = MagicMock()

        async def _set_state(key, value, *, serializable=True):
            return None

        fastmcp_ctx.set_state = _set_state

        ctx = MagicMock()
        ctx.fastmcp_context = fastmcp_ctx
        ctx.message = MagicMock()
        ctx.message.name = "create_media_buy"  # auth-required
        ctx.message.arguments = {"adcp_version": _UNSUPPORTED_PIN}

        # call_next stands in for the downstream RequestCompatMiddleware version gate.
        call_next = AsyncMock()

        # An invalid token makes resolve_identity raise AUTH inside the middleware,
        # which the middleware translates to the two-layer AUTH envelope.
        with patch(
            "src.core.mcp_auth_middleware.resolve_identity_from_context",
            side_effect=AdCPAuthenticationError("Invalid token"),
        ):
            with pytest.raises(AdCPToolError) as exc:
                await MCPAuthMiddleware().on_call_tool(ctx, call_next)

        # AUTH won (not VERSION) and the version gate downstream was never reached.
        _assert_auth_without_version_disclosure(exc.value)
        call_next.assert_not_awaited()
