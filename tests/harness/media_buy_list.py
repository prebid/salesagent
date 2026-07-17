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


class _MediaBuyListDispatch(IntegrationEnv):
    """Canonical get_media_buys dispatch (impl / real-A2A-skill / MCP wrapper).

    Single home for the three list-transport bodies, shared by the standalone
    list env and the composite create->list env so they cannot drift (Pattern
    #8). Named ``_list_*`` rather than ``call_*`` so a subclass that also mixes
    in a create env can route ``call_*`` per request type and still reach the
    create env's ``call_*`` via ``super()``.
    """

    def _list_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        """Call _get_media_buys_impl with real DB."""
        from src.core.tools.media_buy_list import _get_media_buys_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        include_snapshot = kwargs.pop("include_snapshot", False)
        req = kwargs.pop("req", None)
        if req is None:
            req = GetMediaBuysRequest(**kwargs)
        return _get_media_buys_impl(req=req, identity=identity, include_snapshot=include_snapshot)

    def _list_a2a(self, **kwargs: Any) -> Any:
        """Dispatch get_media_buys through the REAL A2A pipeline (on_message_send).

        The production A2A path is ``_handle_get_media_buys_skill`` —
        ``get_media_buys_raw`` has ZERO production callers, so dispatching to it
        here gave false confidence (#1417): a boundary fix on the raw wrapper
        made 'A2A' tests green while the real skill handler still leaked bare
        ValidationErrors.
        """
        return self._run_a2a_handler("get_media_buys", GetMediaBuysResponse, **kwargs)

    def _list_mcp(self, **kwargs: Any) -> Any:
        """Dispatch get_media_buys through the full FastMCP pipeline (in-memory Client).

        Uses ``_run_mcp_client`` rather than the legacy ``_run_mcp_wrapper``: the
        client captures the real ``structured_content`` wire (which ``call_via``
        stashes into ``wire_response``), while the wrapper discards it — silently
        making any MCP wire-envelope assertion a tautology. This is the MCP analogue
        of the #1417 A2A raw-wrapper gap.
        """
        return self._run_mcp_client("get_media_buys", GetMediaBuysResponse, **kwargs)


class MediaBuyListEnv(_MediaBuyListDispatch):
    """Integration test environment for _get_media_buys_impl.

    No patches — list is read-only, no external service calls.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/media-buys/query"

    def _configure_mocks(self) -> None:
        """No mocks needed for read-only list operation."""

    def call_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        return self._list_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> Any:
        return self._list_a2a(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        return self._list_mcp(**kwargs)

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


class MediaBuyCreateListEnv(_MediaBuyListDispatch, MediaBuyCreateEnv):
    """Composite create→list env for the UC-019 post-create status poll.

    The post-create poll scenario grades the create→get seam (media-buy
    index.yaml ``create_buy`` → ``check_buy_status``): the Given drives a REAL
    ``create_media_buy`` through the current transport and the When polls
    ``get_media_buys`` for the id the create RETURNED. So this env routes by
    request type, mirroring ``MediaBuyDualEnv``'s pattern: create requests go to
    ``MediaBuyCreateEnv`` (adapter/audit/checklist patches) via ``super()``,
    everything else takes the shared ``_MediaBuyListDispatch`` path (a pure DB
    read that needs no patches).
    """

    @staticmethod
    def _is_create_request(kwargs: dict[str, Any]) -> bool:
        from src.core.schemas import CreateMediaBuyRequest

        return isinstance(kwargs.get("req"), CreateMediaBuyRequest)

    def call_impl(self, **kwargs: Any) -> Any:
        if self._is_create_request(kwargs):
            return super().call_impl(**kwargs)
        return self._list_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> Any:
        if self._is_create_request(kwargs):
            return super().call_a2a(**kwargs)
        return self._list_a2a(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        if self._is_create_request(kwargs):
            return super().call_mcp(**kwargs)
        return self._list_mcp(**kwargs)
