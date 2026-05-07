"""Regression tests for ``MockSellerPlatform.get_media_buy_delivery``.

Locks the contract that mock and GAM produce identically-shaped
delivery responses by routing through the same delegate.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest


def test_mock_get_media_buy_delivery_is_async() -> None:
    """Method must be async because the delegate it dispatches to is async."""
    from core.platforms.mock import MockSellerPlatform

    assert inspect.iscoroutinefunction(MockSellerPlatform.get_media_buy_delivery)


@pytest.mark.asyncio
async def test_mock_get_media_buy_delivery_actually_invokes_shared_delegate() -> None:
    """Behavioral test: invoking the method awaits
    ``_delegate_get_media_buy_delivery`` with the buyer-supplied req
    and ctx. Catches a regression where the method goes back to a
    stub or wraps the delegate without actually awaiting it.
    """
    from core.platforms.mock import MockSellerPlatform

    platform = MockSellerPlatform()
    fake_req = object()
    fake_ctx = object()

    expected = {"reporting_period": {"start": "x", "end": "y"}, "media_buy_deliveries": []}
    delegate_mock = AsyncMock(return_value=expected)

    with patch("core.platforms.mock._delegate_get_media_buy_delivery", delegate_mock):
        result = await platform.get_media_buy_delivery(fake_req, fake_ctx)

    delegate_mock.assert_awaited_once_with(fake_req, fake_ctx)
    assert result == expected
