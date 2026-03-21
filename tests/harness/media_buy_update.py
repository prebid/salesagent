"""MediaBuyUpdateEnv — integration test environment for _update_media_buy_impl.

Patches: adapter, audit logger, slack notifier, context manager.
Real: get_db_session, MediaBuyRepository, all validation (all hit real DB).

Requires: integration_db fixture + an existing media buy in the DB.

beads: salesagent-4n0
"""

from __future__ import annotations

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

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults."""
        mock_adapter = MagicMock()
        mock_adapter.update_media_buy.return_value = {"status": "success"}
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
        return update_media_buy_raw(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call update_media_buy MCP wrapper."""
        from src.core.tools.media_buy_update import update_media_buy

        return self._run_mcp_wrapper(update_media_buy, UpdateMediaBuySuccess, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build REST request body."""
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            return req.model_dump(mode="json", exclude_none=True)
        return kwargs

    def parse_rest_response(self, data: dict[str, Any]) -> UpdateMediaBuySuccess:
        """Parse REST response JSON."""
        return UpdateMediaBuySuccess(**data)
