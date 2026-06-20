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
        assert isinstance(result.response, UpdateMediaBuyError)
        assert result.response.errors[0].code == "BUDGET_TOO_LOW"

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

# UpdateMediaBuyRequest fields that the flat update wrappers (update_media_buy_raw /
# update_media_buy MCP) do not accept as parameters. MediaBuyDualEnv pops these from
# the model_dump before calling a wrapper so the flat-kwargs call doesn't fail on
# unexpected keyword arguments. Kept in sync with update_media_buy_raw's signature.
_WRAPPER_UNSUPPORTED_FIELDS = (
    "account",
    "adcp_major_version",
    "canceled",
    "cancellation_reason",
    "invoice_recipient",
    "new_packages",
    "proposal_id",
    "revision",
    "today",
    "total_budget",
)


class _SimpleClock:
    """Minimal clock for BDD date token resolution.

    Provides future_iso/past_iso/now_iso used by _resolve_date_token in
    given_media_buy.py. No-op for scenarios that don't use date tokens.
    """

    def now_iso(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def future_iso(self, days: int) -> str:
        from datetime import UTC, datetime, timedelta

        return (datetime.now(UTC) + timedelta(days=days)).isoformat().replace("+00:00", "Z")

    def past_iso(self, days: int) -> str:
        from datetime import UTC, datetime, timedelta

        return (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


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
        "principal": "src.core.auth.get_principal_object",
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
        self.clock = _SimpleClock()

    def _configure_mocks(self) -> None:
        mock_session = MagicMock()

        # UoW: session + media_buys + currency_limits repos
        self._uow_instance = MagicMock()
        self._uow_instance.session = mock_session
        self._uow_instance.media_buys = MagicMock()
        # Default media buy with non-terminal status so the state-machine
        # precondition guard in _update_media_buy_impl passes. Tests that
        # need a specific status call ``set_media_buy(status=...)``.
        _default_mb = MagicMock()
        _default_mb.status = "active"
        self._uow_instance.media_buys.get_by_id.return_value = _default_mb

        # The *_or_raise repository helpers delegate to the plain getters and raise
        # the typed not-found when absent. Wiring the mock the same way lets tests
        # configure get_by_id / get_package (return_value OR side_effect lists) and
        # drive both the direct and or-raise call paths with one setup — and the
        # not-found raise is the real typed error, not a bare MagicMock.
        _mb_repo = self._uow_instance.media_buys

        def _get_by_id_or_raise(media_buy_id: str, *, context: Any = None) -> Any:
            media_buy = _mb_repo.get_by_id(media_buy_id)
            if media_buy is None:
                from src.core.exceptions import AdCPMediaBuyNotFoundError

                raise AdCPMediaBuyNotFoundError(f"Media buy '{media_buy_id}' not found", context=context)
            return media_buy

        def _get_package_or_raise(media_buy_id: str, package_id: str, *, context: Any = None) -> Any:
            package = _mb_repo.get_package(media_buy_id, package_id)
            if package is None:
                from src.core.exceptions import AdCPPackageNotFoundError

                raise AdCPPackageNotFoundError(
                    f"Package '{package_id}' not found for media buy '{media_buy_id}'", context=context
                )
            return package

        _mb_repo.get_by_id_or_raise.side_effect = _get_by_id_or_raise
        _mb_repo.get_package_or_raise.side_effect = _get_package_or_raise

        # creatives repo: by default every referenced creative "exists" with an
        # approved status and no format restriction (uow.products.get_by_id
        # returns a product without format_ids). Tests that exercise the
        # rejection paths (missing/error/rejected/incompatible) override
        # admin_get_by_ids or products.get_by_id.
        self._uow_instance.creatives = MagicMock()

        def _default_admin_get_by_ids(creative_ids: list[str]) -> list[Any]:
            creatives = []
            for cid in creative_ids:
                cr = MagicMock()
                cr.creative_id = cid
                cr.status = "approved"
                cr.agent_url = None
                cr.format = "display_300x250"
                creatives.append(cr)
            return creatives

        self._uow_instance.creatives.admin_get_by_ids.side_effect = _default_admin_get_by_ids

        # products repo: default product has no format restriction so the
        # shared creative-format check is a no-op unless a test overrides it.
        self._uow_instance.products = MagicMock()
        _default_product = MagicMock()
        _default_product.format_ids = []
        self._uow_instance.products.get_by_id.return_value = _default_product

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

    def setup_default_data(self) -> tuple[Any, Any]:
        """Return mock tenant + principal for BDD Background steps.

        Unit env has no real DB. Returns lightweight mocks that satisfy
        the ctx["tenant"] / ctx["principal"] expectations from Background steps.
        """
        tenant = MagicMock()
        tenant.tenant_id = self._tenant_id
        tenant.name = "Test Tenant"
        principal = MagicMock()
        principal.principal_id = self._principal_id
        principal.name = "Test Principal"
        return tenant, principal

    # -- Fluent setup helpers -----------------------------------------------

    def set_media_buy(
        self,
        media_buy_id: str = "mb-001",
        currency: str = "USD",
        status: str = "active",
        start_time: Any = None,
        end_time: Any = None,
        **extra: Any,
    ) -> MagicMock:
        """Configure what uow.media_buys.get_by_id returns.

        Default status is "active" so the state-machine precondition guard
        in _update_media_buy_impl lets the request through. Pass
        ``status="paused"`` etc. to test other paths.

        Returns the mock MediaBuy for further customization.
        """
        mb = MagicMock()
        mb.media_buy_id = media_buy_id
        mb.currency = currency
        mb.status = status
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
        """Build an UpdateMediaBuyRequest and call _update_media_buy_impl.

        Accepts either ``req=<UpdateMediaBuyRequest>`` for pre-built requests
        (used by BDD dispatch_request) or flat kwargs to build a new request.
        """
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = kwargs.pop("req", None)
        if req is None:
            identity = kwargs.pop("identity", self.identity)
            req = UpdateMediaBuyRequest(media_buy_id=media_buy_id, **kwargs)
        else:
            identity = kwargs.pop("identity", self.identity)
        return _update_media_buy_impl(req=req, identity=identity)

    def call_via(self, transport: Any, **kwargs: Any) -> Any:
        """Route all transports through call_impl for unit env.

        Unit env has no real transport wrappers. All 4 transports exercise
        the same _update_media_buy_impl code path via call_impl. This is
        correct for testing validation logic that runs before any
        transport-specific code.
        """
        from tests.harness.dispatchers import ImplDispatcher

        kwargs.setdefault("identity", self.identity)
        return ImplDispatcher().dispatch(self, **kwargs)
