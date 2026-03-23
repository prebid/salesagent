"""MediaBuyUpdateEnv — unit test environment for _update_media_buy_impl.

Patches: MediaBuyUoW, get_principal_object, _verify_principal,
         get_context_manager, get_adapter, get_audit_logger,
         ensure_tenant_context, get_db_session.

Usage::

    def test_something() -> None:
        with MediaBuyUpdateEnv() as env:
            env.set_media_buy(currency="EUR")
            env.set_currency_limit(min_package_budget=Decimal("100"))
            result = env.call_impl(packages=[{"package_id": "pkg-1", "budget": 50.0}])
            env.mock["uow"].return_value.currency_limits.get_for_currency.assert_called_with("EUR")
        assert isinstance(result, UpdateMediaBuyError)
        assert result.errors[0].code == "budget_below_minimum"

Available mocks via env.mock:
    "uow"       -- MediaBuyUoW class mock (env.mock["uow"].return_value is the UoW instance)
    "principal" -- get_principal_object mock
    "verify"    -- _verify_principal mock
    "ctx_mgr"   -- get_context_manager mock
    "adapter"   -- get_adapter mock
    "audit"     -- get_audit_logger mock
    "tenant"    -- ensure_tenant_context mock
    "db"        -- get_db_session mock

Fluent API:
    set_media_buy(...)       -- configure uow.media_buys.get_by_id return value
    set_currency_limit(...)  -- configure uow.currency_limits.get_for_currency return value
    call_impl(...)           -- build UpdateMediaBuyRequest and call _update_media_buy_impl
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from tests.harness._base import BaseTestEnv

_MODULE = "src.core.tools.media_buy_update"
_DB_MODULE = "src.core.database.database_session"


class MediaBuyUpdateEnv(BaseTestEnv):
    """Unit test environment for _update_media_buy_impl.

    All external dependencies are mocked. Fast, isolated.

    Fluent API:
        set_media_buy(...)       -- configure the media buy returned by uow.media_buys.get_by_id
        set_currency_limit(...)  -- configure the currency limit returned by uow.currency_limits
        call_impl(...)           -- call _update_media_buy_impl with an UpdateMediaBuyRequest

    Inspect interactions via:
        env.mock["uow"].return_value  -- the mock UoW instance (media_buys, currency_limits repos)
        env.mock["adapter"]           -- get_adapter mock
        env.mock["ctx_mgr"]           -- get_context_manager mock
    """

    MODULE = _MODULE
    EXTERNAL_PATCHES = {
        "uow": f"{_MODULE}.MediaBuyUoW",
        "principal": f"{_MODULE}.get_principal_object",
        "verify": f"{_MODULE}._verify_principal",
        "ctx_mgr": f"{_MODULE}.get_context_manager",
        "adapter": f"{_MODULE}.get_adapter",
        "audit": f"{_MODULE}.get_audit_logger",
        "tenant": "src.core.helpers.context_helpers.ensure_tenant_context",
        "db": f"{_DB_MODULE}.get_db_session",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._uow_instance: MagicMock | None = None

    def _configure_mocks(self) -> None:
        mock_session = MagicMock()

        # UoW: session + media_buys + currency_limits repos
        self._uow_instance = MagicMock()
        self._uow_instance.session = mock_session
        self._uow_instance.media_buys = MagicMock()
        self._uow_instance.currency_limits = MagicMock()
        self._uow_instance.__enter__ = MagicMock(return_value=self._uow_instance)
        self._uow_instance.__exit__ = MagicMock(return_value=False)
        self.mock["uow"].return_value = self._uow_instance

        # Default currency limit: no restrictions
        default_cl = MagicMock()
        default_cl.max_daily_package_spend = None
        default_cl.min_package_budget = Decimal("0")
        self._uow_instance.currency_limits.get_for_currency.return_value = default_cl

        # Principal
        self.mock["principal"].return_value = MagicMock(
            principal_id=self._principal_id,
            name="Test Principal",
            platform_mappings={},
        )

        # Context manager: workflow step
        mock_step = MagicMock()
        mock_step.step_id = "step_001"
        mock_ctx_mgr_instance = MagicMock()
        mock_ctx_mgr_instance.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
        mock_ctx_mgr_instance.create_workflow_step.return_value = mock_step
        self.mock["ctx_mgr"].return_value = mock_ctx_mgr_instance

        # Adapter: no manual approval by default
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        self.mock["adapter"].return_value = mock_adapter

        # Tenant context
        self.mock["tenant"].return_value = {"tenant_id": self._tenant_id, "name": "Test"}

        # Audit logger
        self.mock["audit"].return_value = MagicMock()

        # DB session (legacy path uses raw session)
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_session)
        mock_cm.__exit__ = MagicMock(return_value=False)
        self.mock["db"].return_value = mock_cm

    # -- Fluent setup helpers -----------------------------------------------

    def set_media_buy(
        self,
        media_buy_id: str = "mb-001",
        currency: str = "USD",
        start_time: Any = None,
        end_time: Any = None,
        **extra: Any,
    ) -> MagicMock:
        """Configure what uow.media_buys.get_by_id returns.

        Returns the mock MediaBuy for further customization.
        """
        mb = MagicMock()
        mb.media_buy_id = media_buy_id
        mb.currency = currency
        mb.start_time = start_time
        mb.end_time = end_time
        for k, v in extra.items():
            setattr(mb, k, v)
        self._uow_instance.media_buys.get_by_id.return_value = mb
        return mb

    def set_currency_limit(
        self,
        min_package_budget: Decimal | None = None,
        max_daily_package_spend: Decimal | None = None,
    ) -> MagicMock:
        """Configure what uow.currency_limits.get_for_currency returns.

        Returns the mock CurrencyLimit for further customization.
        """
        cl = MagicMock()
        cl.min_package_budget = min_package_budget
        cl.max_daily_package_spend = max_daily_package_spend
        self._uow_instance.currency_limits.get_for_currency.return_value = cl
        return cl

    # -- Impl call ----------------------------------------------------------

    def call_impl(self, media_buy_id: str = "mb-001", **kwargs: Any) -> Any:
        """Build an UpdateMediaBuyRequest and call _update_media_buy_impl."""
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(media_buy_id=media_buy_id, **kwargs)
        return _update_media_buy_impl(req=req, identity=self.identity)
