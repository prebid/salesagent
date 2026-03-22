"""MediaBuyCreateEnv — integration test environment for _create_media_buy_impl.

Patches: adapter, audit logger, slack notifier, context manager.
Real: get_db_session, MediaBuyRepository, all validation (all hit real DB).

Requires: integration_db fixture.

beads: salesagent-4n0
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import CreateMediaBuyRequest
from src.core.schemas._base import CreateMediaBuyError, CreateMediaBuyResult, CreateMediaBuySuccess
from tests.harness._base import IntegrationEnv


class MediaBuyCreateEnv(IntegrationEnv):
    """Integration test environment for _create_media_buy_impl.

    Mocks external services (adapter, audit, slack, context manager).
    Everything else is real: DB, repositories, validation, schema processing.
    """

    EXTERNAL_PATCHES = {
        "adapter": "src.core.tools.media_buy_create.get_adapter",
        "audit": "src.core.tools.media_buy_create.get_audit_logger",
        "slack": "src.core.tools.media_buy_create.get_slack_notifier",
        "context_mgr": "src.core.tools.media_buy_create.get_context_manager",
        "setup_check": "src.core.tools.media_buy_create.validate_setup_complete",
    }
    REST_ENDPOINT = "/api/v1/media-buys"

    def setup_media_buy_data(self) -> tuple:
        """Create the full dependency chain needed for create_media_buy.

        Creates: tenant (with auto CurrencyLimit USD), principal,
        PropertyTag ("all_inventory"), Product with PricingOption.

        Returns (tenant, principal, product, pricing_option).
        """
        from tests.factories import (
            PricingOptionFactory,
            ProductFactory,
            PropertyTagFactory,
            PublisherPartnerFactory,
        )

        tenant, principal = self.setup_default_data()
        # Fix subdomain to be DNS-compatible (no underscores)
        if "_" in (tenant.subdomain or ""):
            tenant.subdomain = tenant.subdomain.replace("_", "-")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
        PublisherPartnerFactory(
            tenant=tenant,
            publisher_domain="testpublisher.example.com",
        )
        product = ProductFactory(
            tenant=tenant,
            product_id="guaranteed_display",
            property_tags=["all_inventory"],
        )
        pricing_option = PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            currency="USD",
            is_fixed=True,
        )
        return tenant, principal, product, pricing_option

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks."""
        # Adapter: mock create_media_buy — returns response matching the request packages.
        # The side_effect dynamically generates package_ids from the request.
        mock_adapter = MagicMock()

        def _adapter_create_response(*args: Any, **kwargs: Any) -> Any:
            """Generate adapter response with package_ids matching request packages."""
            from src.core.schemas._base import CreateMediaBuySuccess

            # Determine package count from request
            req_obj = kwargs.get("request") or (args[0] if args else None)
            pkg_count = 0
            if req_obj and hasattr(req_obj, "packages") and req_obj.packages:
                pkg_count = len(req_obj.packages)
            # Also check the 'packages' kwarg (MediaPackage list)
            pkgs_arg = kwargs.get("packages")
            if pkgs_arg:
                pkg_count = max(pkg_count, len(pkgs_arg))
            if pkg_count == 0:
                pkg_count = 1

            media_buy_id = f"mb_{uuid.uuid4().hex[:8]}"
            return CreateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=getattr(req_obj, "buyer_ref", "test-buyer"),
                packages=[
                    {
                        "package_id": f"pkg_{uuid.uuid4().hex[:8]}",
                        "product_id": f"prod_{i}",
                        "budget": 5000.0,
                        "status": "active",
                    }
                    for i in range(pkg_count)
                ],
            )

        mock_adapter.create_media_buy.side_effect = _adapter_create_response
        mock_adapter.validate_media_buy_request.return_value = None
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        self.mock["adapter"].return_value = mock_adapter

        # Audit logger: no-op
        mock_audit = MagicMock()
        mock_audit.log_operation.return_value = None
        mock_audit.log_security_violation.return_value = None
        self.mock["audit"].return_value = mock_audit

        # Slack notifier: no-op
        mock_slack = MagicMock()
        mock_slack.notify_media_buy_event.return_value = None
        self.mock["slack"].return_value = mock_slack

        # Context manager: creates real DB records (Context + WorkflowStep)
        # so FK constraints on ObjectWorkflowMapping don't fail in the manual
        # approval path.
        mock_ctx_mgr = MagicMock()

        def _create_real_context(*args: Any, **kwargs: Any) -> MagicMock:
            """Create a real Context row so workflow steps can reference it."""
            from src.core.database.database_session import get_db_session
            from src.core.database.models import Context as DBContext

            ctx_id = f"test_ctx_{uuid.uuid4().hex[:8]}"
            with get_db_session() as session:
                db_ctx = DBContext(
                    context_id=ctx_id,
                    tenant_id=self._tenant_id,
                    principal_id=self._principal_id,
                    conversation_history=[],
                )
                session.add(db_ctx)
                session.commit()
            mock_context = MagicMock()
            mock_context.context_id = ctx_id
            return mock_context

        mock_ctx_mgr.get_or_create_context.side_effect = _create_real_context

        def _create_real_step(*args: Any, **kwargs: Any) -> MagicMock:
            """Create a real WorkflowStep row so ObjectWorkflowMapping FK succeeds."""
            from src.core.database.database_session import get_db_session
            from src.core.database.models import WorkflowStep

            step_id = f"test_step_{uuid.uuid4().hex[:8]}"
            # Get the context_id from the most recent context
            ctx_id = kwargs.get("context_id") or args[0] if args else None
            if ctx_id is None:
                # Fallback: create a context too
                ctx = _create_real_context()
                ctx_id = ctx.context_id
            with get_db_session() as session:
                db_step = WorkflowStep(
                    step_id=step_id,
                    context_id=ctx_id,
                    step_type=kwargs.get("step_type", "tool_call"),
                    tool_name=kwargs.get("tool_name", "create_media_buy"),
                    status="pending",
                    owner="principal",
                )
                session.add(db_step)
                session.commit()
            mock_step = MagicMock()
            mock_step.step_id = step_id
            return mock_step

        mock_ctx_mgr.create_workflow_step.side_effect = _create_real_step
        mock_ctx_mgr.update_workflow_step.return_value = None
        mock_ctx_mgr.add_message.return_value = None
        self.mock["context_mgr"].return_value = mock_ctx_mgr

        # Setup checklist: pass by default
        self.mock["setup_check"].return_value = None

    def call_impl(self, **kwargs: Any) -> CreateMediaBuyResult:
        """Call _create_media_buy_impl with real DB."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", None) or self.identity

        # Build request from kwargs if not provided directly
        req = kwargs.pop("req", None)
        if req is None:
            req = CreateMediaBuyRequest(**kwargs)

        return asyncio.run(_create_media_buy_impl(req=req, identity=identity))

    def call_a2a(self, **kwargs: Any) -> Any:
        """Call create_media_buy_raw (A2A wrapper)."""
        from src.core.tools.media_buy_create import create_media_buy_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        # A2A wrapper takes flat kwargs, not req=. Unpack request if provided.
        req = kwargs.pop("req", None)
        if req is not None:
            flat = req.model_dump(mode="json", exclude_none=True)
            # A2A wrapper doesn't accept these fields directly
            for key in ("account", "proposal_id", "total_budget"):
                flat.pop(key, None)
            flat.update(kwargs)
            kwargs = flat
        return asyncio.run(create_media_buy_raw(**kwargs))

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call create_media_buy MCP wrapper."""
        import asyncio as aio
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock as MM

        from fastmcp.server.context import Context

        from src.core.tools.media_buy_create import create_media_buy
        from tests.harness.transport import Transport

        self._commit_factory_data()

        # MCP wrapper takes flat kwargs, not req=. Unpack request if provided.
        req = kwargs.pop("req", None)
        if req is not None:
            flat = req.model_dump(mode="json", exclude_none=True)
            for key in ("account", "proposal_id", "total_budget"):
                flat.pop(key, None)
            flat.update(kwargs)
            kwargs = flat

        identity = kwargs.pop("identity", None) or self.identity_for(Transport.MCP)
        mock_ctx = MM(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=identity)

        tool_result = aio.run(create_media_buy(ctx=mock_ctx, **kwargs))
        # Parse the flattened structured_content back into CreateMediaBuyResult
        data = dict(tool_result.structured_content)
        return self.parse_rest_response(data)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build REST request body from kwargs."""
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            return req.model_dump(mode="json", exclude_none=True)
        return kwargs

    def parse_rest_response(self, data: dict[str, Any]) -> CreateMediaBuyResult:
        """Parse REST response JSON.

        CreateMediaBuyResult serializes with flattened response fields + status.
        Reconstruct by splitting status from the rest.
        """
        status = data.pop("status", "completed")
        if "errors" in data and data["errors"]:
            response = CreateMediaBuyError(**data)
        else:
            response = CreateMediaBuySuccess(**data)
        return CreateMediaBuyResult(response=response, status=status)
