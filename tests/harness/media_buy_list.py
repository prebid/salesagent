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
from tests.harness.media_buy_create import MediaBuyCreateEnv


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
        """Convert kwargs to GetMediaBuysBody shape for REST POST."""
        body: dict[str, Any] = {}
        for key in ("media_buy_ids", "status_filter", "account_id", "context"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        if kwargs.get("include_snapshot"):
            body["include_snapshot"] = True
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse REST response JSON."""
        return GetMediaBuysResponse(**data)


class MediaBuyCreateListEnv(MediaBuyCreateEnv):
    """Composite create→list env for the UC-019 post-create status poll.

    The post-create poll scenario grades the create→get seam (media-buy
    index.yaml ``create_buy`` → ``check_buy_status``): the Given drives a REAL
    ``create_media_buy`` through the current transport and the When polls
    ``get_media_buys`` for the id the create RETURNED. So this env routes by
    request type, mirroring ``MediaBuyDualEnv``'s pattern: create requests go
    to ``MediaBuyCreateEnv`` (adapter/audit/checklist patches), everything
    else takes the list path — a pure DB read that needs no patches
    (``MediaBuyListEnv``'s dispatch, inlined below since both parents define
    the same ``call_*`` surface).
    """

    @staticmethod
    def _is_create_request(kwargs: dict[str, Any]) -> bool:
        from src.core.schemas import CreateMediaBuyRequest

        return isinstance(kwargs.get("req"), CreateMediaBuyRequest)

    def call_impl(self, **kwargs: Any) -> Any:
        if self._is_create_request(kwargs):
            return super().call_impl(**kwargs)
        return self._call_list_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> Any:
        if self._is_create_request(kwargs):
            return super().call_a2a(**kwargs)
        return self._call_list_a2a(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        if self._is_create_request(kwargs):
            return super().call_mcp(**kwargs)
        return self._call_list_mcp(**kwargs)

    # -- list side (same bodies as MediaBuyListEnv) --------------------------

    def _call_list_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        from src.core.tools.media_buy_list import _get_media_buys_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        include_snapshot = kwargs.pop("include_snapshot", False)
        req = kwargs.pop("req", None)
        if req is None:
            req = GetMediaBuysRequest(**kwargs)
        return _get_media_buys_impl(req=req, identity=identity, include_snapshot=include_snapshot)

    def _call_list_a2a(self, **kwargs: Any) -> Any:
        # Same real-pipeline dispatch as MediaBuyListEnv.call_a2a: the
        # production A2A path is the skill handler, not the dead raw wrapper.
        return self._run_a2a_handler("get_media_buys", GetMediaBuysResponse, **kwargs)

    def _call_list_mcp(self, **kwargs: Any) -> Any:
        from src.core.tools.media_buy_list import get_media_buys

        return self._run_mcp_wrapper(get_media_buys, GetMediaBuysResponse, **kwargs)
