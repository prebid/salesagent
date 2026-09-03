"""MediaBuyListEnv — integration test environment for _get_media_buys_impl.

Minimal harness — list operation has no adapter calls, just DB queries.
No patches needed (pure DB read).

Requires: integration_db fixture + existing media buys in the DB.

The dispatch itself lives in ``MediaBuyListDispatchMixin`` so a composite env can
reuse it verbatim: ``MediaBuyCreateListEnv`` (tests/harness/media_buy_create_list.py)
needs the SAME get_media_buys dispatch alongside the create path, and a second copy
of these bodies would be a DRY violation — the next fix to the list dispatch
would land in one copy only.

GH #1335, GH #1900
"""

from __future__ import annotations

from typing import Any

from src.core.schemas._base import GetMediaBuysRequest, GetMediaBuysResponse
from tests.harness._base import IntegrationEnv
from tests.harness.transport import DeliverResult


class MediaBuyListDispatchMixin:
    """get_media_buys dispatch across impl/A2A/MCP.

    Deliberately named ``_call_list_*`` / ``_deliver_list_*`` rather than
    ``call_*`` / ``deliver_*``: the composite env inherits create dispatch from
    ``MediaBuyCreateEnv`` under those public names and routes to these
    explicitly, so neither tool's dispatch can shadow the other's by MRO
    accident.

    Both spellings exist for one reason each, and neither is a second
    implementation: ``_deliver_list_*`` returns the ``DeliverResult`` (payload
    AND wire) that a ``deliver_*`` override must return, and ``_call_list_*``
    is that same result's ``.payload``, for a caller that routes at the
    ``call_*`` frame.
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

    def _deliver_list_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buys through the REAL A2A pipeline (on_message_send).

        The production A2A path is ``_handle_get_media_buys_skill`` —
        ``get_media_buys_raw`` has ZERO production callers, so dispatching to it
        here gave false confidence (#1417): a boundary fix on the raw
        wrapper made 'A2A' tests green while the real skill handler still
        leaked bare ValidationErrors.
        """
        return self._run_a2a_handler("get_media_buys", GetMediaBuysResponse, **kwargs)

    def _deliver_list_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buys through the REAL FastMCP ``Client`` pipeline.

        Was ``_run_mcp_wrapper``, which is deprecated precisely because it hand-builds
        a mock Context and calls the wrapper directly: it skips the middleware,
        TypeAdapter validation and the token→DB→identity auth chain, and — the reason
        it had to change here — it stashes NO ``wire_response``. Every MCP assertion
        on this tool therefore graded a re-serialized typed payload rather than the
        bytes a buyer receives, which is exactly the blind spot GH #1900 slipped
        through. ``_run_mcp_client`` stashes ``structured_content``, the real MCP wire.

        The wrapper path is wrong for the error path too, and for a second reason:
        it calls the UNDECORATED module function, while ``with_error_logging`` is
        applied at registration time (``src/core/main.py``). Through it no
        ``AdCPToolError`` is ever raised, so nothing is stashed and the dispatcher
        captures ``None`` for BOTH the error envelope and the success response —
        while this env goes on declaring ``has_wire=True``.

        Through the client, the rejection moves from ``_resolve_status_filter``
        inside ``_impl`` to FastMCP's TypeAdapter at the schema boundary, which
        changes the message and field shape. That is what a real MCP buyer
        receives — ``RequestCompatMiddleware`` translates the TypeAdapter rejection
        into the two-layer error — so grading it is the point.
        """
        return self._run_mcp_client("get_media_buys", GetMediaBuysResponse, **kwargs)

    def _call_list_a2a(self, **kwargs: Any) -> Any:
        """The parsed A2A payload for get_media_buys."""
        return self._deliver_list_a2a(**kwargs).payload

    def _call_list_mcp(self, **kwargs: Any) -> Any:
        """The parsed MCP payload for get_media_buys."""
        return self._deliver_list_mcp(**kwargs).payload


class MediaBuyListEnv(MediaBuyListDispatchMixin, IntegrationEnv):
    """Integration test environment for _get_media_buys_impl.

    No patches — list is read-only, no external service calls.
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a.
    RESPONSE_MODEL = GetMediaBuysResponse

    EXTERNAL_PATCHES: dict[str, str] = {}
    REST_ENDPOINT = "/api/v1/media-buys/query"
    # POST /api/v1/media-buys/query in src/routes/api_v1.py — REST transport for
    # get_media_buys (added GH #1830). The body builder and response parser below
    # were kept through the no-route period; see their docstrings.

    def _configure_mocks(self) -> None:
        """No mocks needed for read-only list operation."""

    def call_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        return self._call_list_impl(**kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buys through the real A2A handler pipeline.

        FIXME(#1928): JUSTIFIED OVERRIDE — does NOT declare A2A_SKILL, so it does
        not take the base's client-core delegation. The core's UNWRAP parses into
        the PINNED GetMediaBuysResponse, whose media_buys items REQUIRE
        `confirmed_at` and `revision` (get-media-buys-response.json); production
        emits neither, so every response fails that parse. Parsing here with the
        LOCAL model keeps this env working while the gap stays attributable — a
        production schema defect, not a dispatch defect, and deliberately not
        hidden by loosening the core's parse. Delete this override and its
        `_KNOWN_DELIVER_OVERRIDES` entry when #1928 lands.
        """
        return self._deliver_list_a2a(**kwargs)

    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buys through the real FastMCP ``Client`` pipeline.

        FIXME(#1928): JUSTIFIED OVERRIDE, and for the SAME reason as
        :meth:`deliver_a2a` — not the stale one ("uses the legacy
        ``_run_mcp_wrapper``"), which stopped being true when GH #1900 moved this
        dispatch onto ``_run_mcp_client``. Declaring MCP_TOOL would route through
        the client core, whose UNWRAP parses into the PINNED
        GetMediaBuysResponse; production omits the required `confirmed_at` and
        `revision` on every media_buys item, so that parse fails. Dispatching
        here parses with the LOCAL model while still going through the real
        FastMCP pipeline, so ``wire_response`` carries the true
        ``structured_content`` — which is what lets the envelope `status`
        assertions (#1941) grade the actual MCP bytes.
        """
        return self._deliver_list_mcp(**kwargs)

    # ---- REST shaping hooks -------------------------------------------------
    # Declared on THIS class and deliberately NOT on MediaBuyListDispatchMixin.
    # MediaBuyCreateListEnv is `(MediaBuyListDispatchMixin, MediaBuyCreateEnv)`, so
    # the mixin precedes MediaBuyCreateEnv in its MRO: a build_rest_body on the mixin
    # would shadow the CREATE builder for that env and break the create REST arm that
    # tests/integration/test_harness_rest_refusal.py pins.

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuysBody shape for REST POST.

        NOT equivalent to the inherited default: ``BaseTestEnv.build_rest_body``
        serializes a ``req`` model wholesale via ``model_dump(mode="json",
        exclude_none=True)`` and returns ``{}`` when there is no ``req`` — it cannot
        shape the flat kwargs this tool is called with. Deleting the override would
        silently substitute that generic behavior, which is hardest to notice when
        the REST route is live.
        """
        body: dict[str, Any] = {}
        for key in ("media_buy_ids", "status_filter", "account_id", "context"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        if kwargs.get("include_snapshot"):
            body["include_snapshot"] = True
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse REST response JSON.

        Also not equivalent to the inherited default: ``BaseTestEnv.parse_rest_response``
        raises NotImplementedError, so dropping this would replace a working parser
        with a refusal.
        """
        return GetMediaBuysResponse(**data)
