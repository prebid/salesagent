"""MediaBuyCreateEnv — integration test environment for _create_media_buy_impl.

Patches: adapter, audit logger, slack notifier, context manager.
Real: get_db_session, MediaBuyRepository, all validation (all hit real DB).

Requires: integration_db fixture.

beads: salesagent-4n0
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import CreateMediaBuyRequest
from src.core.schemas._base import CreateMediaBuyResult
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
    }
    REST_ENDPOINT = "/api/v1/media-buys"

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for external mocks."""
        # Adapter: mock create_media_buy to return success
        mock_adapter = MagicMock()
        mock_adapter.create_media_buy.return_value = {
            "platform_order_id": "mock_order_001",
            "platform_line_item_ids": {},
        }
        mock_adapter.validate_media_buy_request.return_value = None
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None
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

        # Context manager: return mock that creates workflow steps
        mock_ctx_mgr = MagicMock()
        mock_context = MagicMock()
        mock_context.context_id = "test_ctx_001"
        mock_ctx_mgr.get_or_create_context.return_value = mock_context
        mock_step = MagicMock()
        mock_step.step_id = "test_step_001"
        mock_ctx_mgr.create_workflow_step.return_value = mock_step
        mock_ctx_mgr.update_workflow_step.return_value = None
        self.mock["context_mgr"].return_value = mock_ctx_mgr

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
        return asyncio.run(create_media_buy_raw(**kwargs))

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call create_media_buy MCP wrapper."""
        from src.core.tools.media_buy_create import create_media_buy

        return self._run_mcp_wrapper(create_media_buy, CreateMediaBuyResult, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build REST request body from kwargs."""
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            return req.model_dump(mode="json", exclude_none=True)
        return kwargs

    def parse_rest_response(self, data: dict[str, Any]) -> CreateMediaBuyResult:
        """Parse REST response JSON."""
        return CreateMediaBuyResult(**data)
