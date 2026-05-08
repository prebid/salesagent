"""Regression test: ``_delegate_create_media_buy`` must forward
``push_notification_config`` from the request body to the impl as a kwarg.

Issue #64: ``test_completed_status_sends_task_payload`` fails because
no webhook fires when ``create_media_buy`` completes. Root cause: the
delegate accepts ``req`` (a ``CreateMediaBuyRequest`` carrying
``push_notification_config``) but only passes ``req`` and ``identity``
to ``_create_media_buy_impl``. The impl reads its own
``push_notification_config`` kwarg — NOT ``req.push_notification_config``
— so the buyer's webhook config is silently dropped, no
``PushNotificationConfig`` DB row is registered, and
``context_manager._send_push_notifications`` finds zero webhooks to
fire when the workflow step transitions to ``completed``.

The fix extracts ``req.push_notification_config`` (a Pydantic model),
serializes to dict, and passes it as the ``push_notification_config``
kwarg.

Same family as PR #84 (``get_media_buy_delivery`` stub) and PR #107
(``sync_creatives`` stub): wire-shape data the impl expected was
silently dropped at the boundary.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_delegate_forwards_push_notification_config_to_impl() -> None:
    """When the request body carries ``push_notification_config``, the
    delegate must extract it (model_dump) and pass to the impl as a
    kwarg. Without this, the impl gets ``None`` and never registers
    the ``PushNotificationConfig`` DB row."""
    from core.platforms._delegate import _delegate_create_media_buy

    pnc_dict = {
        "url": "https://example.com/webhook",
        "authentication": {
            "schemes": ["Bearer"],
            "credentials": "x" * 40,
        },
    }
    req_body = {
        "brand": {"domain": "testbrand.com"},
        "packages": [
            {
                "product_id": "prod_1",
                "budget": 1000.0,
                "pricing_option_id": "po-cpm-default",
            }
        ],
        "start_time": "2026-06-01T00:00:00Z",
        "end_time": "2026-06-30T00:00:00Z",
        "push_notification_config": pnc_dict,
    }

    fake_ctx = object()

    impl_mock = AsyncMock()
    impl_mock.return_value = type(
        "Stub",
        (),
        {
            "model_dump": lambda self, **kw: {"media_buy_id": "mb_test", "status": "completed"},
        },
    )()

    with (
        patch("core.platforms._delegate._create_media_buy_impl", impl_mock),
        patch(
            "core.platforms._delegate._build_identity",
            return_value=type("FakeIdent", (), {})(),
        ),
    ):
        await _delegate_create_media_buy(req_body, fake_ctx)

    impl_mock.assert_awaited_once()
    _, kwargs = impl_mock.await_args
    forwarded = kwargs.get("push_notification_config")
    assert (
        forwarded is not None
    ), "_delegate_create_media_buy dropped push_notification_config — impl received None. See #64 root cause."
    assert forwarded["url"] == pnc_dict["url"]
    assert forwarded["authentication"]["schemes"] == ["Bearer"]


@pytest.mark.asyncio
async def test_delegate_passes_none_when_request_omits_pnc() -> None:
    """No ``push_notification_config`` on the request → kwarg is None.
    Buyers who don't want webhooks should not get a phantom registration.
    """
    from core.platforms._delegate import _delegate_create_media_buy

    req_body = {
        "brand": {"domain": "testbrand.com"},
        "packages": [
            {
                "product_id": "prod_1",
                "budget": 1000.0,
                "pricing_option_id": "po-cpm-default",
            }
        ],
        "start_time": "2026-06-01T00:00:00Z",
        "end_time": "2026-06-30T00:00:00Z",
    }
    fake_ctx = object()

    impl_mock = AsyncMock()
    impl_mock.return_value = type("Stub", (), {"model_dump": lambda self, **kw: {"media_buy_id": "mb_test"}})()

    with (
        patch("core.platforms._delegate._create_media_buy_impl", impl_mock),
        patch(
            "core.platforms._delegate._build_identity",
            return_value=type("FakeIdent", (), {})(),
        ),
    ):
        await _delegate_create_media_buy(req_body, fake_ctx)

    impl_mock.assert_awaited_once()
    _, kwargs = impl_mock.await_args
    assert kwargs.get("push_notification_config") is None
