"""Transport-boundary regression tests for update_media_buy financial guardrails."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastmcp.server.context import Context

from src.core.schemas import UpdateMediaBuyError
from src.core.tools.media_buy_update import update_media_buy, update_media_buy_raw

MODULE = "src.core.tools.media_buy_update"


def _make_identity(tenant_id: str = "tenant_test"):
    identity = MagicMock()
    identity.principal_id = "principal_test"
    identity.tenant_id = tenant_id
    identity.tenant = {"tenant_id": tenant_id, "name": "Test Tenant"}
    identity.testing_context = None
    identity.protocol = "mcp"
    return identity


def _mock_media_buy(currency: str = "USD"):
    media_buy = MagicMock()
    media_buy.currency = currency
    media_buy.start_time = datetime(2025, 1, 1, tzinfo=UTC)
    media_buy.end_time = datetime(2025, 1, 31, tzinfo=UTC)
    return media_buy


def _mock_currency_limit(*, min_package_budget: str | None = None, max_daily_package_spend: str | None = None):
    currency_limit = MagicMock()
    currency_limit.min_package_budget = Decimal(min_package_budget) if min_package_budget is not None else None
    currency_limit.max_daily_package_spend = (
        Decimal(max_daily_package_spend) if max_daily_package_spend is not None else None
    )
    return currency_limit


def _mock_uow(media_buy, currency_limit):
    uow = MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    uow.session = MagicMock()
    uow.media_buys = MagicMock()
    uow.currency_limits = MagicMock()
    uow.media_buys.get_by_id.return_value = media_buy
    uow.media_buys.get_packages.return_value = []
    uow.media_buys.update_fields.return_value = media_buy
    uow.currency_limits.get_for_currency.return_value = currency_limit
    return uow


def _common_patches(mock_uow, protocol: str = "mcp"):
    principal = MagicMock(principal_id="principal_test", name="Test Principal", platform_mappings={})
    adapter = MagicMock(manual_approval_required=False, manual_approval_operations=[])
    ctx_manager = MagicMock()
    ctx_manager.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
    ctx_manager.create_workflow_step.return_value = MagicMock(step_id="step_001")
    identity = _make_identity()
    identity.protocol = protocol
    return (
        patch(f"{MODULE}.MediaBuyUoW", return_value=mock_uow),
        patch(f"{MODULE}.get_principal_object", return_value=principal),
        patch(f"{MODULE}.get_adapter", return_value=adapter),
        patch(f"{MODULE}.get_context_manager", return_value=ctx_manager),
        patch(f"{MODULE}.get_audit_logger", return_value=MagicMock()),
        patch(f"{MODULE}._verify_principal"),
        identity,
        ctx_manager,
    )


def test_a2a_wrapper_rejects_oversized_campaign_budget():
    """A2A boundary should reject the original oversized-budget attack path."""
    mock_uow = _mock_uow(
        media_buy=_mock_media_buy(currency="USD"),
        currency_limit=_mock_currency_limit(),
    )
    uow_patch, principal_patch, adapter_patch, ctx_patch, audit_patch, verify_patch, identity, _ = _common_patches(
        mock_uow, protocol="a2a"
    )

    with uow_patch, principal_patch, adapter_patch, ctx_patch, audit_patch, verify_patch:
        result = update_media_buy_raw(
            media_buy_id="mb_transport",
            budget=888_888_888.0,
            currency="USD",
            identity=identity,
        )

    assert isinstance(result, UpdateMediaBuyError)
    assert result.errors
    assert result.errors[0].code == "budget_ceiling_exceeded"


def test_a2a_wrapper_rejects_package_budget_below_minimum():
    """A2A boundary should reject under-minimum package budget updates."""
    mock_uow = _mock_uow(
        media_buy=_mock_media_buy(currency="EUR"),
        currency_limit=_mock_currency_limit(min_package_budget="100"),
    )
    uow_patch, principal_patch, adapter_patch, ctx_patch, audit_patch, verify_patch, identity, _ = _common_patches(
        mock_uow, protocol="a2a"
    )

    with uow_patch, principal_patch, adapter_patch, ctx_patch, audit_patch, verify_patch:
        result = update_media_buy_raw(
            media_buy_id="mb_transport",
            packages=[{"package_id": "pkg-1", "budget": 50.0}],
            identity=identity,
        )

    assert isinstance(result, UpdateMediaBuyError)
    assert result.errors
    assert result.errors[0].code == "budget_below_minimum"


def test_mcp_wrapper_preserves_existing_currency_for_float_budget():
    """MCP boundary should preserve the existing media buy currency on float-only budget updates."""
    mock_uow = _mock_uow(
        media_buy=_mock_media_buy(currency="EUR"),
        currency_limit=_mock_currency_limit(),
    )
    uow_patch, principal_patch, adapter_patch, ctx_patch, audit_patch, verify_patch, identity, _ = _common_patches(
        mock_uow
    )

    mock_ctx = MagicMock(spec=Context)
    mock_ctx.get_state = AsyncMock(side_effect=[identity, "ctx_transport"])

    with uow_patch, principal_patch, adapter_patch, ctx_patch, audit_patch, verify_patch:
        result = asyncio.run(
            update_media_buy(
                media_buy_id="mb_transport",
                budget=5000.0,
                ctx=mock_ctx,
            )
        )

    assert result.structured_content["media_buy_id"] == "mb_transport"
    mock_uow.media_buys.update_fields.assert_called_once_with("mb_transport", budget=5000.0, currency="EUR")
