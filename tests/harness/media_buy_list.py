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

import asyncio
import logging
from typing import Any, cast

from src.core.schemas._base import GetMediaBuysRequest, GetMediaBuysResponse
from tests.harness._base import IntegrationEnv, rest_body_from_kwargs
from tests.harness._realize import realize_e2e
from tests.harness.database_faults import DatabaseFaultMixin
from tests.helpers.scheduler_isolation import run_status_tick


def _run_status_sweep(_env: object) -> list[logging.LogRecord]:
    """One status-sweep tick, with the log records it produced.

    In-process and e2e share this tick. The e2e server also runs its own
    scheduler, so the scenario's summary line is what proves this tick did
    the writes.
    """
    return asyncio.run(run_status_tick())


class MediaBuyListDispatchMixin:
    """get_media_buys dispatch across impl/A2A/MCP/REST.

    The implementation and REST helpers use list-specific names so neither
    tool's dispatch can shadow the other's by MRO accident. The REST shaping
    hooks are ``_build_list_rest_body`` / ``_parse_list_rest_response``
    here, and only the env that dispatches get_media_buys as its PRIMARY verb
    binds them to the public ``build_rest_body`` / ``parse_rest_response``.
    A public builder on this mixin would shadow the CREATE builder for
    ``MediaBuyCreateListEnv`` — which is ``(MediaBuyListDispatchMixin,
    MediaBuyCreateEnv)``, so the mixin precedes the create env in its MRO — and
    break the create REST branch that tests/integration/test_harness_rest_refusal.py
    pins.

    MCP and A2A use the shared core; the composite env's owner hooks select the
    list tool and this mixin's local response reader.
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
        ``address_tool`` already routes on; a second private copy is how one transport
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

        Both shapes are ``rest_body_from_kwargs``, the builder every verb dispatched in
        both shapes shares. A ``req`` model is dumped whole: the REST body model IS the
        DTO, so every field it carries is one the route accepts.
        """
        # "account" belongs in the flat fields: the steps dispatch account={"account_id": ...}
        # (the AdCP 3.x reference shape), so a list naming only the legacy "account_id"
        # silently dropped the filter on REST -- the request then SUCCEEDED where MCP and
        # A2A correctly rejected it with UNSUPPORTED_FEATURE (#1762).
        body = rest_body_from_kwargs(kwargs, ("media_buy_ids", "status_filter", "account", "account_id", "context"))
        if kwargs.get("include_snapshot"):
            body["include_snapshot"] = True
        return body

    def _parse_list_rest_response(self, data: dict[str, Any]) -> GetMediaBuysResponse:
        """Parse a get_media_buys REST body into the typed response.

        Kept as a named helper because the dual envs select it by which verb they
        dispatched, which the base cannot express. Through ``revive`` for the same reason
        the base uses it: a served document carries the context the boundary stamped, and
        ``SalesAgentAdcpResponse`` refuses that field on construction.
        """
        return cast("GetMediaBuysResponse", GetMediaBuysResponse.revive(data))


class MediaBuyListEnv(DatabaseFaultMixin, MediaBuyListDispatchMixin, IntegrationEnv):
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
    # RESPONSE_MODEL stays: the core dispatches and parses once with
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

    @realize_e2e(_run_status_sweep)
    def run_status_sweep(self) -> list[logging.LogRecord]:
        return _run_status_sweep(self)
