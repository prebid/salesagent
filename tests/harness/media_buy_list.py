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

from typing import Any, cast

from src.core.schemas._base import GetMediaBuysRequest, GetMediaBuysResponse
from tests.harness._base import IntegrationEnv
from tests.harness.transport import DeliverResult


class MediaBuyListDispatchMixin:
    """get_media_buys dispatch across impl/A2A/MCP/REST.

    Deliberately named ``_call_list_*`` / ``_deliver_list_*`` rather than
    ``call_*`` / ``deliver_*``: the composite env inherits create dispatch from
    ``MediaBuyCreateEnv`` under those public names and routes to these
    explicitly, so neither tool's dispatch can shadow the other's by MRO
    accident. The REST shaping hooks below obey the same rule for the same
    reason — they are ``_build_list_rest_body`` / ``_parse_list_rest_response``
    here, and only the env that dispatches get_media_buys as its PRIMARY verb
    binds them to the public ``build_rest_body`` / ``parse_rest_response``.
    A public builder on this mixin would shadow the CREATE builder for
    ``MediaBuyCreateListEnv`` — which is ``(MediaBuyListDispatchMixin,
    MediaBuyCreateEnv)``, so the mixin precedes the create env in its MRO — and
    break the create REST branch that tests/integration/test_harness_rest_refusal.py
    pins.

    Both dispatch spellings exist for one reason each, and neither is a second
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
        # include_snapshot is a GetMediaBuysRequest FIELD, so it rides on the request
        # rather than beside it -- the same change the transports made, and the reason
        # _get_media_buys_impl no longer declares it as a parameter. Callers may still
        # pass it as a kwarg here; it is folded into the request instead of forwarded.
        include_snapshot = kwargs.pop("include_snapshot", None)

        req = kwargs.pop("req", None)
        if req is None:
            req = GetMediaBuysRequest(**kwargs)
        if include_snapshot is not None:
            req = req.model_copy(update={"include_snapshot": include_snapshot})

        return _get_media_buys_impl(req=req, identity=identity)

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

        The wrapper path is wrong for the error path too, and for two further
        reasons. It calls the module function directly, while a failure becomes an
        ``AdCPToolError`` only in ``RegistryTool.run`` (``src/core/main.py``): through
        it none is ever raised, so
        nothing is stashed and the dispatcher captures ``None`` for BOTH the error
        envelope and the success response — while this env goes on declaring
        ``has_wire=True``. And a raised ``AdCPSalesAgentError`` propagated raw out of
        ``asyncio.run(wrapper_fn(...))`` was never serialized into a ``ToolError``, so
        ``McpDispatcher`` captured ``wire_error_envelope=None`` and every MCP error
        assertion in UC-019 graded a reconstructed exception that could not have
        failed if production stopped emitting an envelope at all
        (salesagent-3dawm.18/.19).

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

    #: The route get_media_buys answers on — ``@router.post("/media-buys/query")`` in
    #: src/routes/api_v1.py. It lives on the MIXIN, beside the other three dispatches,
    #: because a composite env dispatches this verb at a DIFFERENT endpoint from its
    #: primary one and has to be able to name this one to switch to it. The
    #: ``AccountListDispatchMixin.LIST_REST_ENDPOINT`` precedent, for the same reason.
    LIST_REST_ENDPOINT = "/api/v1/media-buys/query"

    @staticmethod
    def is_list_request(kwargs: dict[str, Any]) -> bool:
        """Whether this dispatch is get_media_buys rather than the env's primary verb.

        Discriminates on the request TYPE, so ONE rule covers every call site and
        every transport — no dispatch of create, update or delivery ever carries a
        ``GetMediaBuysRequest``. Public and owned here so the composite envs route
        REST on exactly the same predicate their ``call_impl``/``deliver_a2a``/
        ``deliver_mcp`` already route on; a second private copy is how one transport
        drifts into dispatching a different tool than the other three.
        """
        return isinstance(kwargs.get("req"), GetMediaBuysRequest)

    def _build_list_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert get_media_buys kwargs to the ``GetMediaBuysBody`` shape.

        Both call shapes are served, because both are dispatched: scenarios that
        exercise how the ROUTE treats a wire field send flat kwargs, while the
        post-create poll sends a built ``req=GetMediaBuysRequest(...)`` (it has to —
        that is the discriminator the composite env routes on). A builder that
        handled only the flat form returned ``{}`` for the poll and graded an
        unfiltered listing under a scenario name that says "by media_buy_id".

        Not equivalent to the inherited ``BaseTestEnv.build_rest_body`` either: that
        one serializes a ``req`` model wholesale via ``model_dump(mode="json",
        exclude_none=True)`` and returns ``{}`` when there is no ``req``, so it can
        shape neither of the two forms this tool is called with.
        """
        req = kwargs.get("req")
        if req is not None:
            # Narrowed to what the tool implements, the same "DTO fields INTERSECT
            # parameters" rule the transports use (and the same narrowing
            # The whole DTO, unfiltered. This used to drop every field the hand-written
            # wrapper did not declare, because the DTO was then a SUPERSET of what the tool
            # accepted -- GetMediaBuysRequest declares include_history,
            # include_webhook_activity, pagination and more that get_media_buys did not take,
            # so dumping it whole sent fields the route rejected. The REST body model IS the
            # DTO now, so every field it carries is a field the route accepts and the filter
            # could only drop one the scenario meant to send.
            #
            # exclude_unset, not exclude_none: GetMediaBuysRequest is re-based on the library
            # type, so it inherits include_snapshot with a default of False rather than None
            # -- exclude_none stopped dropping it and the built body grew a key the caller
            # never set. exclude_unset is "send only what was set", which is also what the
            # flat branch below does.
            body = req.model_dump(mode="json", exclude_unset=True)
        else:
            body = {}
            # "account" belongs here: the steps dispatch account={"account_id": ...}
            # (the AdCP 3.x reference shape), so a list naming only the legacy
            # "account_id" silently dropped the filter on REST -- the request then
            # SUCCEEDED where MCP and A2A correctly rejected it with
            # UNSUPPORTED_FEATURE. Invisible until UC-019 regained REST
            # parametrization (#1762); the same shape of harness gap as
            # CreativeFormatsEnv.build_rest_body in salesagent-3dawm.16.
            for key in ("media_buy_ids", "status_filter", "account", "account_id", "context"):
                if key in kwargs and kwargs[key] is not None:
                    body[key] = kwargs[key]
        if kwargs.get("include_snapshot"):
            body["include_snapshot"] = True
        return body

    def _parse_list_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse a get_media_buys REST body into the typed response.

        Kept as a named helper because the dual envs select it by which verb they
        dispatched, which the base cannot express. Through ``revive`` for the same reason
        the base uses it: a served document carries the context the boundary stamped, and
        ``AdcpResponse`` refuses that field on construction.
        """
        return cast("GetMediaBuysResponse", GetMediaBuysResponse.revive(data))


class MediaBuyListEnv(MediaBuyListDispatchMixin, IntegrationEnv):
    """Integration test environment for _get_media_buys_impl.

    No patches — list is read-only, no external service calls.
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a, and this env DELEGATES to
    # the client core -- it declares the tool/skill instead of overriding deliver_*.
    #
    # It carried a deliver_mcp AND a deliver_a2a override until #1928 was measured on the
    # wire. Both were justified by "production emits neither confirmed_at nor revision on
    # every media_buys item, so the core's pinned parse fails". Four dispatches -- MCP and
    # A2A, a seller-confirmed row and a never-confirmed one -- carry BOTH fields and parse
    # clean against the pinned GetMediaBuysResponse; the never-confirmed row carries
    # `confirmed_at: null`, which the pin declares as type ["string","null"]. The reason
    # was stale, so the overrides and their two _KNOWN_DELIVER_OVERRIDES rows are gone.
    #
    # RESPONSE_MODEL stays: the core dispatches, and _deliver_via_client re-parses with
    # this env's own parser, so callers still receive the LOCAL GetMediaBuysResponse.
    MCP_TOOL = "get_media_buys"
    A2A_SKILL = "get_media_buys"
    RESPONSE_MODEL = GetMediaBuysResponse

    EXTERNAL_PATCHES: dict[str, str] = {}
    # REST_ENDPOINT is declared because the route now EXISTS:
    # `@router.post("/media-buys/query")` in src/routes/api_v1.py. It did not when
    # the surrounding mixin was extracted, and declaring an endpoint for a path
    # absent from src/ was the defect that removal fixed — a REST parametrization
    # would have failed as if production were broken rather than as if the route
    # were missing. With the route landed, the opposite failure is the live one:
    # dropping the endpoint silently deletes the REST branch of every UC-019 scenario,
    # which then grades nothing instead of failing. The sibling composite env routes
    # the SAME endpoint behind its create/list discriminator
    # (`media_buy_create_list.py::REST_ENDPOINT`) — one constant, two envs.
    REST_ENDPOINT = MediaBuyListDispatchMixin.LIST_REST_ENDPOINT

    def _configure_mocks(self) -> None:
        """No mocks needed for read-only list operation."""

    def call_impl(self, **kwargs: Any) -> GetMediaBuysResponse:
        return self._call_list_impl(**kwargs)

    # ---- REST shaping hooks -------------------------------------------------
    # Bound to the public names on THIS class only; the bodies live on the mixin
    # under `_build_list_rest_body` / `_parse_list_rest_response`. See the mixin
    # docstring for why the public names must not be declared there.

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuysBody shape for REST POST."""
        return self._build_list_rest_body(**kwargs)

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse REST response JSON."""
        return self._parse_list_rest_response(data)
