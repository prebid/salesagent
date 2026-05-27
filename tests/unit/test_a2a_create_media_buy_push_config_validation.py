"""Regression: A2A create_media_buy must not validate the transport-layer
push_notification_config as part of CreateMediaBuyRequest.

Core invariant: ``push_notification_config`` is an A2A transport-layer parameter
forwarded to ``core_create_media_buy_tool`` SEPARATELY (adcp_a2a_server.py:1529).
It must NOT be folded into ``CreateMediaBuyRequest.model_validate()`` — doing so
applies the adcp ``Authentication.credentials`` MinLen(32) constraint to the
request body, so a Bearer-auth webhook config with a short credential makes the
WHOLE create_media_buy fail validation. That diverts the request away from the
manual-approval gate in media_buy_create.py (no submitted TaskStatusUpdateEvent
webhook is ever emitted).

The MCP wrapper and ``create_media_buy_raw`` both construct CreateMediaBuyRequest
WITHOUT push_notification_config and forward it as a separate argument. The A2A
skill handler must behave identically.

beads: salesagent-18h.3
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.core.schemas import CreateMediaBuyResult


def _valid_packages_params() -> dict:
    """Minimal spec-valid create_media_buy parameters (no push config)."""
    return {
        "brand": {"domain": "testbrand.com"},
        "packages": [
            {
                "product_id": "prod_1",
                "budget": 50000.0,
                "pricing_option_id": "po_default",
            }
        ],
        "start_time": "2099-01-01T00:00:00Z",
        "end_time": "2099-01-31T23:59:59Z",
        "context": {"e2e": "push_config_validation"},
    }


@pytest.mark.asyncio
async def test_short_webhook_credentials_do_not_block_create_media_buy():
    """Bearer-auth push_notification_config with <32-char credentials must NOT
    cause _handle_create_media_buy_skill to short-circuit into VALIDATION_ERROR.

    With the bug present, CreateMediaBuyRequest.model_validate(params) rejects
    the short ``authentication.credentials`` and the handler returns an
    adcp_error dict — ``core_create_media_buy_tool`` is never called, so the
    manual-approval (submitted) path is never reached.
    """
    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
    from src.core.resolved_identity import ResolvedIdentity

    handler = AdCPRequestHandler()
    identity = ResolvedIdentity(
        principal_id="test-principal",
        tenant_id="test-tenant",
        tenant={"tenant_id": "test-tenant"},
        protocol="a2a",
    )

    params = _valid_packages_params()
    # Transport-layer push notification config (as injected by
    # _handle_explicit_skill from the A2A SendMessageConfiguration). The
    # 18-char credential is shorter than the adcp MinLen(32) on the
    # CreateMediaBuyRequest.push_notification_config.authentication field.
    params["push_notification_config"] = {
        "url": "http://localhost:9999/webhook",
        "authentication": {
            "schemes": ["Bearer"],
            "credentials": "test-webhook-token",  # 18 chars (< 32)
        },
    }

    submitted_result = CreateMediaBuyResult(
        response={"media_buy_id": "mb_test", "packages": []},
        status="submitted",
    )

    captured: dict = {}

    async def fake_tool(**kwargs):
        captured.update(kwargs)
        return submitted_result

    with patch(
        "src.a2a_server.adcp_a2a_server.core_create_media_buy_tool",
        new=AsyncMock(side_effect=fake_tool),
    ):
        result = await handler._handle_create_media_buy_skill(params, identity)

    # The tool MUST have been called — the request must not be rejected by
    # request-model validation just because the webhook credential is short.
    assert captured, (
        "core_create_media_buy_tool was never called — _handle_create_media_buy_skill "
        "short-circuited (likely a VALIDATION_ERROR from folding push_notification_config "
        "into CreateMediaBuyRequest.model_validate)."
    )

    # push_notification_config must be forwarded to the tool, not validated away.
    assert captured.get("push_notification_config") == {
        "url": "http://localhost:9999/webhook",
        "authentication": {"schemes": ["Bearer"], "credentials": "test-webhook-token"},
    }, f"push_notification_config not forwarded to tool: {captured.get('push_notification_config')!r}"

    # The handler must return the tool's result (manual-approval submitted),
    # not a VALIDATION_ERROR dict.
    if isinstance(result, dict):
        assert result.get("status") == "submitted", (
            f"Expected submitted status to reach the webhook path, got dict {result!r}"
        )
    else:
        assert result.status == "submitted", f"Expected submitted status, got {result!r}"


@pytest.mark.asyncio
async def test_no_auth_push_config_still_works():
    """Control: a no-auth push_notification_config must keep working (the bug
    only manifests when the authentication block forces the MinLen(32) check)."""
    from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
    from src.core.resolved_identity import ResolvedIdentity

    handler = AdCPRequestHandler()
    identity = ResolvedIdentity(
        principal_id="test-principal",
        tenant_id="test-tenant",
        tenant={"tenant_id": "test-tenant"},
        protocol="a2a",
    )

    params = _valid_packages_params()
    params["push_notification_config"] = {"url": "http://localhost:9999/webhook"}

    submitted_result = CreateMediaBuyResult(
        response={"media_buy_id": "mb_test", "packages": []},
        status="submitted",
    )

    captured: dict = {}

    async def fake_tool(**kwargs):
        captured.update(kwargs)
        return submitted_result

    with patch(
        "src.a2a_server.adcp_a2a_server.core_create_media_buy_tool",
        new=AsyncMock(side_effect=fake_tool),
    ):
        result = await handler._handle_create_media_buy_skill(params, identity)

    assert captured, "core_create_media_buy_tool was never called for no-auth config"
    assert captured.get("push_notification_config") == {"url": "http://localhost:9999/webhook"}
    status = result.get("status") if isinstance(result, dict) else result.status
    assert status == "submitted"
