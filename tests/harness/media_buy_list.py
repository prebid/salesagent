"""MediaBuyListEnv — integration test environment for _get_media_buys_impl.

Minimal harness — list operation has no adapter calls, just DB queries.
No patches needed (pure DB read).

Requires: integration_db fixture + existing media buys in the DB.

beads: salesagent-4n0
"""

from __future__ import annotations

from typing import Any

from src.core.schemas._base import GetMediaBuysRequest, GetMediaBuysResponse
from tests.harness._base import IntegrationEnv


class MediaBuyListEnv(IntegrationEnv):
    """Integration test environment for _get_media_buys_impl.

    No patches — list is read-only, no external service calls.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/media-buys/query"

    def _configure_mocks(self) -> None:
        """No mocks needed for read-only list operation."""

    def call_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        """Call _get_media_buys_impl with real DB."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        include_snapshot = kwargs.pop("include_snapshot", False)

        req = kwargs.pop("req", None)
        if req is None:
            req = GetMediaBuysRequest(**kwargs)

        return _get_media_buys_impl(req=req, identity=identity, include_snapshot=include_snapshot)

    def call_a2a(self, **kwargs: Any) -> Any:
        """Dispatch get_media_buys through the REAL A2A pipeline (on_message_send).

        The production A2A path is ``_handle_get_media_buys_skill`` —
        ``get_media_buys_raw`` has ZERO production callers, so dispatching to it
        here gave false confidence (#1417): a boundary fix on the raw
        wrapper made 'A2A' tests green while the real skill handler still
        leaked bare ValidationErrors.
        """
        return self._run_a2a_handler("get_media_buys", GetMediaBuysResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        """Call get_media_buys MCP wrapper."""
        from src.core.tools.media_buy_list import get_media_buys

        return self._run_mcp_wrapper(get_media_buys, GetMediaBuysResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuysBody shape for REST POST.

        With ``req=`` (a validated GetMediaBuysRequest): delegates to
        serialize_request so the wire carries the real ``account`` shape (a
        nested reference, not a flat ``account_id`` the old allowlist here
        never matched) plus ``include_snapshot`` merged in — it's a real
        transport param, not a request-model field, so serialize_request
        never sees it. Without ``req=`` (dispatch_malformed_request path):
        returns the remaining kwargs verbatim, matching the base class's
        no-``req`` contract — production, not the test process, rejects them.
        """
        from tests.harness._base import serialize_request

        include_snapshot = kwargs.pop("include_snapshot", None)
        req = kwargs.pop("req", None)
        if req is not None:
            body = serialize_request(req)
        else:
            body = dict(kwargs)
        if include_snapshot is not None:
            body["include_snapshot"] = include_snapshot
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse REST response JSON."""
        return GetMediaBuysResponse(**data)
