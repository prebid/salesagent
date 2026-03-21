"""MediaBuyUpdateEnv — integration test environment for _update_media_buy_impl.

Patches: adapter, audit logger, context manager, setup checklist.
Real: get_db_session, MediaBuyRepository, all validation (all hit real DB).

Requires: integration_db fixture + an existing media buy in the DB.

beads: salesagent-4n0
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import UpdateMediaBuyRequest
from src.core.schemas._base import UpdateMediaBuyError, UpdateMediaBuySuccess
from tests.harness._base import IntegrationEnv


class MediaBuyUpdateEnv(IntegrationEnv):
    """Integration test environment for _update_media_buy_impl.

    Mocks external services. Everything else is real.
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_update.get_adapter",
        "audit": "src.core.tools.media_buy_update.get_audit_logger",
        "context_mgr": "src.core.tools.media_buy_update.get_context_manager",
    }
    REST_ENDPOINT = "/api/v1/media-buys"

    def setup_update_data(self) -> tuple:
        """Create the full dependency chain needed for update_media_buy.

        Creates: tenant (with auto CurrencyLimit USD), principal,
        PropertyTag, PublisherPartner, Product, PricingOption,
        existing MediaBuy with MediaPackage.

        Returns (tenant, principal, media_buy, package).
        """
        from tests.factories import (
            MediaBuyFactory,
            MediaPackageFactory,
            PricingOptionFactory,
            ProductFactory,
            PropertyTagFactory,
            PublisherPartnerFactory,
        )

        tenant, principal = self.setup_default_data()
        # Fix subdomain to be DNS-compatible
        if "_" in (tenant.subdomain or ""):
            tenant.subdomain = tenant.subdomain.replace("_", "-")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        PublisherPartnerFactory(tenant=tenant, publisher_domain="testpublisher.example.com")
        product = ProductFactory(
            tenant=tenant,
            product_id="guaranteed_display",
            property_tags=["all_inventory"],
        )
        PricingOptionFactory(product=product, pricing_model="cpm", currency="USD", is_fixed=True)
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_existing",
            buyer_ref="my_ref_01",
            status="active",
        )
        package = MediaPackageFactory(
            media_buy=media_buy,
            package_id="pkg_001",
            package_config={
                "package_id": "pkg_001",
                "product_id": product.product_id,
                "budget": 5000.0,
            },
        )
        # Create Context + WorkflowStep so ObjectWorkflowMapping FK succeeds
        from src.core.database.models import Context, WorkflowStep

        context = Context(
            context_id="test_ctx_001",
            tenant_id=tenant.tenant_id,
            principal_id=principal.principal_id,
        )
        self._session.add(context)
        self._session.flush()
        wf_step = WorkflowStep(
            step_id="test_step_001",
            context_id="test_ctx_001",
            step_type="tool_call",
            tool_name="update_media_buy",
            status="pending",
            owner="system",
        )
        self._session.add(wf_step)
        self._session.flush()
        return tenant, principal, media_buy, package

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults."""
        mock_adapter = MagicMock()
        mock_adapter.update_media_buy.return_value = UpdateMediaBuySuccess(
            media_buy_id=f"mb_{uuid.uuid4().hex[:8]}",
            buyer_ref="test-buyer",
            affected_packages=[],
        )
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        self.mock["adapter"].return_value = mock_adapter

        mock_audit = MagicMock()
        mock_audit.log_operation.return_value = None
        mock_audit.log_security_violation.return_value = None
        self.mock["audit"].return_value = mock_audit

        mock_ctx_mgr = MagicMock()
        mock_context = MagicMock()
        mock_context.context_id = "test_ctx_001"
        mock_ctx_mgr.get_or_create_context.return_value = mock_context
        mock_step = MagicMock()
        mock_step.step_id = "test_step_001"
        mock_ctx_mgr.create_workflow_step.return_value = mock_step
        mock_ctx_mgr.update_workflow_step.return_value = None
        self.mock["context_mgr"].return_value = mock_ctx_mgr

    def call_impl(self, **kwargs: Any) -> UpdateMediaBuySuccess | UpdateMediaBuyError:
        """Call _update_media_buy_impl with real DB."""
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
        # A2A wrapper takes flat kwargs, not req=.
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
        return self.parse_rest_response(dict(tool_result.structured_content))

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build REST request body."""
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            return req.model_dump(mode="json", exclude_none=True)
        return kwargs

    def parse_rest_response(self, data: dict[str, Any]) -> UpdateMediaBuySuccess:
        """Parse REST response JSON."""
        data.pop("status", None)
        if "errors" in data and data["errors"]:
            return UpdateMediaBuyError(**data)
        return UpdateMediaBuySuccess(**data)
