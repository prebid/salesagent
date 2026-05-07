"""Regression test: ``MockSellerPlatform.update_media_buy`` must NOT
preempt the SDK's ``inject_context``.

Issue #95: the e2e ``test_complete_campaign_lifecycle_with_webhooks``
fails asserting ``response.context == {'e2e': 'update_media_buy'}``
but receives ``{'e2e': 'create_media_buy'}``. Root cause: the mock's
update_media_buy returns ``_project_media_buy(record)``; the record
stores the create-time ``context`` and the projection includes it.
The SDK's :func:`adcp.server.helpers.inject_context` only fills
``response.context`` when it is **not already present**, so the
buyer's update-call context is silently dropped.

Fix: update_media_buy strips the record's stored context from its
projected response, letting :func:`inject_context` fill from the
request body.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_update_media_buy_response_omits_stored_context_so_sdk_can_inject():
    """The mock's update response MUST NOT include ``context`` —
    otherwise the SDK's ``inject_context`` (which only fills when
    absent) cannot echo the buyer's per-call correlation key.
    """
    from unittest.mock import MagicMock

    from core.platforms.mock import _MEDIA_BUYS, MockSellerPlatform

    # Seed a media buy with a CREATE-time context cached on the record.
    media_buy_id = "mb_test_ctx_echo"
    _MEDIA_BUYS[media_buy_id] = {
        "media_buy_id": media_buy_id,
        "status": "pending_start",
        "packages": [
            {
                "package_id": "pkg_0",
                "product_id": "prod_test",
                "pricing_option_id": "po_default",
                "budget": 1000.0,
                "creative_assignments": [{"creative_id": "c1"}],
                "targeting_overlay": None,
                "status": "pending",
            }
        ],
        "tenant_id": "test_tenant",
        "currency": "USD",
        "context": {"e2e": "create_media_buy"},  # original create-time context
        "valid_actions": ["pause", "cancel"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    try:
        platform = MockSellerPlatform()
        ctx = MagicMock()
        ctx.account.metadata.get.return_value = "test_tenant"

        # Patch is the buyer's update body. The buyer's context here
        # should be the one echoed in the response — not the create's.
        patch = {"paused": True, "context": {"e2e": "update_media_buy"}}

        # Note: we exercise the wrapped method via the underlying impl
        # to bypass the @_IDEMPOTENCY.wrap decorator. The decorator's
        # cache lookup needs a fresh idempotency_key at the wire layer;
        # for this regression we exercise the pure response-shape contract.
        underlying = MockSellerPlatform.update_media_buy.__wrapped__  # type: ignore[attr-defined]
        result: Any = await underlying(platform, media_buy_id, patch, ctx)

        # The response MUST NOT carry the create-time context. The SDK's
        # ``inject_context`` (called downstream by ``create_tool_caller``)
        # will fill the update's context from the request. If the mock
        # pre-populates context, the SDK's injector skips the request and
        # the buyer sees the wrong correlation key.
        assert "context" not in result, (
            f"Mock update_media_buy response must NOT include `context` (so the "
            f"SDK's inject_context can fill from the request). Got: "
            f"context={result.get('context')!r}. See #95."
        )
        # Defend against silent regressions where update_media_buy becomes
        # a no-op (would still pass the context check above).
        assert (
            result["status"] == "paused"
        ), "Patch was not applied: paused=True should transition the buy to paused state"
    finally:
        _MEDIA_BUYS.pop(media_buy_id, None)
