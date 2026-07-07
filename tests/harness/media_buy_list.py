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


class MediaBuyListDispatchMixin:
    """get_media_buys dispatch shared by MediaBuyListEnv and MediaBuyLifecycleEnv.

    Provides the four transport bodies as ``_call_list_*`` / ``_*_list_rest_*``
    so a composite env (create + update + get) can route to them without
    duplicating the dispatch logic.
    """

    def _call_list_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        """Call _get_media_buys_impl with real DB."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        include_snapshot = kwargs.pop("include_snapshot", False)

        req = kwargs.pop("req", None)
        if req is None:
            req = GetMediaBuysRequest(**kwargs)

        return _get_media_buys_impl(req=req, identity=identity, include_snapshot=include_snapshot)

    def _call_list_a2a(self, **kwargs: Any) -> Any:
        """Call get_media_buys_raw (A2A wrapper)."""
        from src.core.tools.media_buy_list import get_media_buys_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return get_media_buys_raw(**kwargs)

    def _call_list_mcp(self, **kwargs: Any) -> Any:
        """Call get_media_buys MCP wrapper."""
        from src.core.tools.media_buy_list import get_media_buys

        return self._run_mcp_wrapper(get_media_buys, GetMediaBuysResponse, **kwargs)

    def _build_list_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuysBody shape for REST POST."""
        body: dict[str, Any] = {}
        for key in ("media_buy_ids", "status_filter", "account_id", "context"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        if kwargs.get("include_snapshot"):
            body["include_snapshot"] = True
        return body

    def _parse_list_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse REST response JSON."""
        return GetMediaBuysResponse(**data)


class MediaBuyListEnv(MediaBuyListDispatchMixin, IntegrationEnv):
    """Integration test environment for _get_media_buys_impl.

    No patches — list is read-only, no external service calls.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/media-buys/query"

    def _configure_mocks(self) -> None:
        """No mocks needed for read-only list operation."""

    def call_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        return self._call_list_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> Any:
        return self._call_list_a2a(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        return self._call_list_mcp(**kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        return self._build_list_rest_body(**kwargs)

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        return self._parse_list_rest_response(data)
