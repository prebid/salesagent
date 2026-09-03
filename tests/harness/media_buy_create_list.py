"""MediaBuyCreateListEnv — composite env for the post-create get_media_buys poll.

The UC-019 storyboard scenario polls get_media_buys for a buy the SAME scenario
just created, so it needs both tools in one environment and one identity. That is
what the graded storyboard step does too: AdCP 3.1.1
``dist/compliance/3.1.1/domains/media-buy/scenarios/available_actions.yaml`` →
phase ``read_persisted_buy_actions`` → step ``get_created_buy_available_actions``
sends ``media_buy_ids: ["$context.<id captured from create_media_buy>"]`` and
validates the response against ``media-buy/get-media-buys-response.json``.

Shape mirrors ``MediaBuyDualEnv`` (create + update): extend ``MediaBuyCreateEnv``
and route by request type. The get_media_buys dispatch itself is inherited from
``MediaBuyListDispatchMixin`` rather than re-implemented, so this env and
``MediaBuyListEnv`` grade the same tool through the same code.

REST is routed: ``POST /api/v1/media-buys/query`` (PR #1950 / #1830). List requests
use ``MediaBuyListEnv``'s body builder + query endpoint; everything else falls
through to the inherited create (or DualEnv update) REST path via ``super()``.

GH #1900 / #1830
"""

from __future__ import annotations

from typing import Any

from src.core.schemas._base import GetMediaBuysRequest
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.media_buy_list import MediaBuyListDispatchMixin, MediaBuyListEnv
from tests.harness.transport import DeliverResult


def _is_list_request(kwargs: dict[str, Any]) -> bool:
    return isinstance(kwargs.get("req"), GetMediaBuysRequest)


class MediaBuyCreateListEnv(MediaBuyListDispatchMixin, MediaBuyCreateEnv):
    """create_media_buy env that also dispatches get_media_buys.

    A ``req=GetMediaBuysRequest(...)`` kwarg routes to the list path; anything
    else falls through to the inherited create path. ``req=`` is a free
    discriminator because the dispatchers this env actually uses —
    ``_run_a2a_handler`` and ``_run_mcp_client`` (MediaBuyListDispatchMixin.call_mcp
    and MediaBuyCreateEnv.call_mcp both route through the latter) — already flatten
    a request model into the flat skill/tool parameters those wrappers accept.
    Not ``_run_mcp_wrapper``: it is deprecated, no env here calls it, and unlike
    ``_run_mcp_client`` it never stashes the real MCP wire.

    No extra patches: get_media_buys is a pure DB read with no external services,
    and the inherited create patches target the create module only.
    """

    _active_list: bool = False

    def call_impl(self, **kwargs: Any) -> Any:
        if _is_list_request(kwargs):
            return self._call_list_impl(**kwargs)
        return super().call_impl(**kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Route by request CONTENT: list requests to the list mixin, else create.

        Overrides ``deliver_*`` rather than ``call_*`` so the wire envelope
        survives; the base's ``call_a2a`` stays ``deliver_a2a(...).payload``.
        """
        if _is_list_request(kwargs):
            return self._deliver_list_a2a(**kwargs)
        return super().deliver_a2a(**kwargs)

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Content router; see :meth:`deliver_a2a`."""
        if _is_list_request(kwargs):
            return self._deliver_list_mcp(**kwargs)
        return super().deliver_mcp(**kwargs)

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        """In-process RestDispatcher reads ``REST_ENDPOINT`` before body build.

        Mirror ``MediaBuyDualEnv``: re-select the list query URL from request
        content here so a stale create-collection endpoint cannot be POSTed.
        Bypass DualEnv's update router (when present in MRO) by calling
        ``MediaBuyCreateEnv`` directly for the list arm.
        """
        self._active_list = _is_list_request(kwargs)
        if self._active_list:
            return MediaBuyCreateEnv._run_rest_request(self, "/api/v1/media-buys/query", **kwargs)
        self._active_list = False
        return super()._run_rest_request(endpoint, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """List → MediaBuyListEnv body; else ``super()`` (create or DualEnv update).

        RestE2EDispatcher reads ``REST_ENDPOINT`` after ``build_rest_body``, so the
        ``_active_list`` flag must be set here for e2e_rest (same pattern as
        ``MediaBuyDualEnv._active_update``).
        """
        if _is_list_request(kwargs):
            self._active_list = True
            req = kwargs["req"]
            return MediaBuyListEnv.build_rest_body(
                self,
                media_buy_ids=req.media_buy_ids,
                status_filter=req.status_filter,
                include_snapshot=bool(kwargs.get("include_snapshot", False)),
                account=getattr(req, "account", None),
                context=getattr(req, "context", None),
            )
        self._active_list = False
        # super(), not an explicit parent call: MediaBuyCreateUpdateListEnv resolves
        # this through MediaBuyDualEnv's stateful create/update routing, which naming
        # a parent directly would bypass.
        return super().build_rest_body(**kwargs)

    @property
    def REST_ENDPOINT(self) -> str:  # noqa: N802 — matches the inherited class-attr name
        """List → query; update (DualEnv) → per-id PUT URL; else create collection."""
        if self._active_list:
            return "/api/v1/media-buys/query"
        if getattr(self, "_active_update", False):
            return f"/api/v1/media-buys/{getattr(self, '_update_target_id', 'NOT_SEEDED')}"
        return "/api/v1/media-buys"

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        if self._active_list:
            self._active_list = False
            return MediaBuyListEnv.parse_rest_response(self, data)
        return super().parse_rest_response(data)
