"""MediaBuyListEnv — integration test environment for _get_media_buys_impl.

Minimal harness — list operation has no adapter calls, just DB queries.
Patches only audit logger.

Requires: integration_db fixture + existing media buys in the DB.

beads: salesagent-4n0
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas._base import GetMediaBuysRequest, GetMediaBuysResponse
from tests.harness._base import IntegrationEnv


class MediaBuyListEnv(IntegrationEnv):
    """Integration test environment for _get_media_buys_impl.

    Minimal patches — list is read-only, no adapter calls.
    """

    EXTERNAL_PATCHES = {
        "audit": "src.core.tools.media_buy_list.get_audit_logger",
    }
    # No REST endpoint for get_media_buys (MCP + A2A only)
    REST_ENDPOINT = ""

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults."""
        mock_audit = MagicMock()
        mock_audit.log_operation.return_value = None
        self.mock["audit"].return_value = mock_audit

    def call_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        """Call _get_media_buys_impl with real DB."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", None) or self.identity
        include_snapshot = kwargs.pop("include_snapshot", False)

        req = kwargs.pop("req", None)
        if req is None:
            req = GetMediaBuysRequest(**kwargs)

        return _get_media_buys_impl(req=req, identity=identity, include_snapshot=include_snapshot)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Call get_media_buys_raw (A2A wrapper)."""
        from src.core.tools.media_buy_list import get_media_buys_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return get_media_buys_raw(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call get_media_buys MCP wrapper."""
        from src.core.tools.media_buy_list import get_media_buys

        return self._run_mcp_wrapper(get_media_buys, GetMediaBuysResponse, **kwargs)

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse REST response JSON."""
        return GetMediaBuysResponse(**data)
