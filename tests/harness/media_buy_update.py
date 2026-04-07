"""MediaBuyUpdateEnv — test environments for _update_media_buy_impl.

Two variants:
- **MediaBuyUpdateEnv** (unit): All external deps mocked. Fast, isolated.
- **MediaBuyUpdateIntegrationEnv** (integration/BDD): Real DB, mocks external services only.

Unit env usage::

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

from tests.harness._base import BaseTestEnv, IntegrationEnv

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


# ═══════════════════════════════════════════════════════════════════════════
# Integration variant — real DB, mocks only external services
# ═══════════════════════════════════════════════════════════════════════════

_UPDATE_MODULE = "src.core.tools.media_buy_update"


class MediaBuyUpdateIntegrationEnv(IntegrationEnv):
    """Integration test environment for _update_media_buy_impl.

    Real: DB, MediaBuyUoW, repositories, validation logic.
    Mocked: adapter, audit logger, context manager.

    Requires: integration_db pytest fixture.
    """

    EXTERNAL_PATCHES = {
        "adapter": f"{_UPDATE_MODULE}.get_adapter",
        "audit": f"{_UPDATE_MODULE}.get_audit_logger",
        "context_mgr": f"{_UPDATE_MODULE}.get_context_manager",
    }

    _seeded_media_buy_id: str = "NOT_SEEDED"  # Set by setup_update_data()

    def setup_update_data(self) -> tuple:
        """Create the full dependency chain needed for update_media_buy.

        Creates: tenant (with CurrencyLimit USD), principal, PropertyTag,
        Product with PricingOption (with placements), a pre-existing media buy
        with one package.

        Media buy is created through seed_media_buy() which uses the real
        API in E2E mode (server-generated ID) or factories in in-process
        mode (per-test DB, no collision).

        Returns (tenant, principal, media_buy, product) where media_buy
        is a MediaBuy ORM object (attribute access: .media_buy_id, .status).
        """
        tenant, principal = self.setup_default_data()
        product, _pricing = self.setup_product_chain(
            tenant,
            placements=[
                {"placement_id": "plc_a", "name": "Placement A"},
                {"placement_id": "plc_b", "name": "Placement B"},
            ],
        )
        media_buy = self.seed_media_buy(
            tenant=tenant,
            principal=principal,
            product=product,
            status="active",
            buyer_ref="test-buyer-ref",
            packages=[{"product_id": product.product_id, "budget": 5000.0}],
        )
        self._seeded_media_buy_id = media_buy.media_buy_id
        return tenant, principal, media_buy, product

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks."""
        from src.core.schemas import UpdateMediaBuySuccess

        # Adapter: no manual approval by default
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        mock_adapter.validate_media_buy_request.return_value = None
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None

        def _adapter_update_side_effect(
            media_buy_id: str = "", buyer_ref: str = "", **kwargs: Any
        ) -> UpdateMediaBuySuccess:
            from src.core.schemas import AffectedPackage

            action = kwargs.get("action", "")
            package_id = kwargs.get("package_id") or "pkg_001"
            paused = action in ("pause_media_buy", "pause_package")
            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                affected_packages=[AffectedPackage(package_id=package_id, paused=paused)],
            )

        mock_adapter.update_media_buy.side_effect = _adapter_update_side_effect
        self.mock["adapter"].return_value = mock_adapter

        # Audit logger: no-op
        mock_audit = MagicMock()
        mock_audit.log_operation.return_value = None
        mock_audit.log_security_violation.return_value = None
        self.mock["audit"].return_value = mock_audit

        # Context manager: creates real DB records for FK constraints.
        # Uses shared helper from IntegrationEnv.
        self.mock["context_mgr"].return_value = self._build_mock_context_manager(tool_name="update_media_buy")

    REST_METHOD = "put"

    @property
    def REST_ENDPOINT(self) -> str:  # noqa: N802
        """Dynamic endpoint — uses the seeded media_buy_id."""
        return f"/api/v1/media-buys/{self._seeded_media_buy_id}"

    def call_impl(self, **kwargs: Any) -> Any:
        """Call _update_media_buy_impl with real DB."""
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.tools.media_buy_update import _update_media_buy_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", None) or self.identity

        req = kwargs.pop("req", None)
        if req is None:
            req = UpdateMediaBuyRequest(**kwargs)

        return _update_media_buy_impl(req=req, identity=identity)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Call update_media_buy_raw (A2A wrapper)."""
        from src.core.tools.media_buy_update import update_media_buy_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is not None:
            flat = req.model_dump(mode="json", exclude_none=True)
            flat.update(kwargs)
            kwargs = flat
        return update_media_buy_raw(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call update_media_buy MCP wrapper."""
        import asyncio
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock as MM

        from fastmcp.server.context import Context

        from src.core.tools.media_buy_update import update_media_buy
        from tests.harness.transport import Transport

        self._commit_factory_data()

        req = kwargs.pop("req", None)
        if req is not None:
            flat = req.model_dump(mode="json", exclude_none=True)
            flat.update(kwargs)
            kwargs = flat

        identity = kwargs.pop("identity", None) or self.identity_for(Transport.MCP)
        mock_ctx = MM(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=identity)

        tool_result = asyncio.run(update_media_buy(ctx=mock_ctx, **kwargs))
        data = dict(tool_result.structured_content)
        return self.parse_rest_response(data)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build REST request body from kwargs."""
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            body = req.model_dump(mode="json", exclude_none=True)
            body.pop("media_buy_id", None)  # media_buy_id is in the URL, not body
            return body
        kwargs.pop("media_buy_id", None)
        return kwargs

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        """Parse REST/MCP response into UpdateMediaBuySuccess or UpdateMediaBuyError."""
        from src.core.schemas._base import UpdateMediaBuyError, UpdateMediaBuySuccess

        if "errors" in data and data["errors"]:
            return UpdateMediaBuyError(**data)
        return UpdateMediaBuySuccess(**data)
