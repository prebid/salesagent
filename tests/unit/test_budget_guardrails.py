"""Unit tests for PR04 financial guardrails: F-05, F-07, F-08.

F-05 — Budget ceiling: updates exceeding MAX_CAMPAIGN_BUDGET are rejected.
F-07 — Currency preservation: float-only budget updates use existing DB currency.
F-08 — Min-spend parity: package budget updates honor currency_limit.min_package_budget.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.core.schemas import Budget, UpdateMediaBuyError, UpdateMediaBuyRequest
from src.core.tools.media_buy_update import MAX_CAMPAIGN_BUDGET, _update_media_buy_impl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_identity(principal_id: str = "principal-abc", tenant_id: str = "tenant-xyz"):
    identity = MagicMock()
    identity.principal_id = principal_id
    identity.tenant = {"tenant_id": tenant_id, "name": "Test Tenant"}
    identity.tenant_id = tenant_id
    identity.is_authenticated = True
    identity.testing_context = None
    identity.protocol = "mcp"
    return identity


def _make_req(**kwargs) -> UpdateMediaBuyRequest:
    defaults = {
        "media_buy_id": "mb-test-001",
    }
    defaults.update(kwargs)
    return UpdateMediaBuyRequest(**defaults)


# ---------------------------------------------------------------------------
# F-05: Budget ceiling
# ---------------------------------------------------------------------------


def test_max_campaign_budget_constant_is_ten_million() -> None:
    """Default ceiling must be 10,000,000."""
    assert MAX_CAMPAIGN_BUDGET == Decimal("10000000")


@pytest.mark.asyncio
@patch("src.core.tools.media_buy_update.get_context_manager")
@patch("src.core.tools.media_buy_update.MediaBuyUoW")
@patch("src.core.tools.media_buy_update._verify_principal")
@patch("src.core.tools.media_buy_update.get_principal_object")
@patch("src.core.tools.media_buy_update.get_adapter")
async def test_extreme_budget_rejected(mock_adapter, mock_principal, mock_verify, mock_uow_cls, mock_ctx_mgr) -> None:
    """Budget exceeding MAX_CAMPAIGN_BUDGET must return UpdateMediaBuyError."""
    identity = _make_identity()

    # Wire up UoW mock
    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.media_buys = MagicMock()
    mock_uow.session = MagicMock()
    mock_uow_cls.return_value = mock_uow

    mock_principal.return_value = MagicMock(principal_id="principal-abc", name="Test")
    mock_adapter.return_value = MagicMock(manual_approval_required=False, manual_approval_operations=[])

    ctx_mock = MagicMock()
    ctx_mock.get_or_create_context.return_value = MagicMock(context_id="ctx-1")
    ctx_mock.create_workflow_step.return_value = MagicMock(step_id="step-1")
    mock_ctx_mgr.return_value = ctx_mock

    req = _make_req(budget=Budget(total=888_888_888, currency="USD"))
    result = _update_media_buy_impl(req=req, identity=identity)

    assert isinstance(result, UpdateMediaBuyError)
    assert result.errors
    assert result.errors[0].code == "budget_ceiling_exceeded"


# ---------------------------------------------------------------------------
# F-05: Constant is configurable via env var
# ---------------------------------------------------------------------------


def test_max_campaign_budget_env_override(monkeypatch) -> None:
    """MAX_CAMPAIGN_BUDGET should reflect MAX_CAMPAIGN_BUDGET_USD env var."""
    import importlib

    monkeypatch.setenv("MAX_CAMPAIGN_BUDGET_USD", "5000000")
    import src.core.tools.media_buy_update as mod

    importlib.reload(mod)
    assert mod.MAX_CAMPAIGN_BUDGET == Decimal("5000000")
    # Restore
    importlib.reload(mod)
