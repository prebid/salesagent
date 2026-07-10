"""MediaBuyLifecycleEnv — create/update/get composite for UC-019 BDD scenarios.

The wired UC-019 revision/confirmed_at invariants (#1544) need all three media
buy tools inside one scenario: Given steps create a buy (real create path) and
optionally advance it (real update / repository status transition), then the
transport-parametrized When steps read it back through get_media_buys.

Routing: the transport-dispatched surface of this env is get_media_buys ONLY —
every scenario's When step is a query. Creates and updates happen in Given /
plumbing steps via direct ``call_impl`` calls, discriminated by request type
(CreateMediaBuyRequest / flat create kwargs → create; UpdateMediaBuyRequest →
update; everything else → list). REST/E2E dispatch therefore always targets
the query endpoint.

GitHub PR #1544 review remediation (wire dormant UC-019 scenarios).
"""

from __future__ import annotations

from typing import Any

from src.core.schemas._base import GetMediaBuysRequest
from tests.harness.media_buy_dual import MediaBuyDualEnv, _is_update_request
from tests.harness.media_buy_list import MediaBuyListDispatchMixin

# Flat kwargs that identify a create_media_buy call (create_default_buy and
# direct create call_impl use these; get_media_buys never does).
_CREATE_MARKER_KEYS = frozenset({"brand", "packages"})


def _is_list_request(kwargs: dict[str, Any]) -> bool:
    req = kwargs.get("req")
    if req is not None:
        return isinstance(req, GetMediaBuysRequest)
    if _is_update_request(kwargs):
        return False
    return not (_CREATE_MARKER_KEYS & kwargs.keys())


class MediaBuyLifecycleEnv(MediaBuyDualEnv, MediaBuyListDispatchMixin):
    """MediaBuyDualEnv (create + update) extended with get_media_buys dispatch.

    Presents the *list* contract on the REST/E2E dispatch surface
    (REST_ENDPOINT / build_rest_body / parse_rest_response) because only
    queries travel over the parametrized transports in UC-019 scenarios.
    """

    REST_ENDPOINT = "/api/v1/media-buys/query"

    # -- Transport routing -------------------------------------------------

    def call_impl(self, **kwargs: Any) -> Any:
        if _is_list_request(kwargs):
            return self._call_list_impl(**kwargs)
        return super().call_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> Any:
        if _is_list_request(kwargs):
            return self._call_list_a2a(**kwargs)
        return super().call_a2a(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        if _is_list_request(kwargs):
            return self._call_list_mcp(**kwargs)
        return super().call_mcp(**kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        if _is_list_request(kwargs):
            return self._build_list_rest_body(**kwargs)
        return super().build_rest_body(**kwargs)

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        # _active_update is MediaBuyDualEnv's in-flight flag for the update
        # REST path; outside it, the only REST traffic in this env is queries.
        if self._active_update:
            return super().parse_rest_response(data)
        return self._parse_list_rest_response(data)
