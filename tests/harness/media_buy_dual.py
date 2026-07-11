"""MediaBuyDualEnv — composite environment for UC-026 BDD scenarios.

UC-026 scenarios use both create and update flows within the same test:
Given steps create a media buy (create path), then When steps update it
(update path). This env extends MediaBuyCreateEnv with update-module
patches and delegates update requests to the appropriate production code.

beads: salesagent-a3xo
"""

from __future__ import annotations

from typing import Any, Self
from unittest.mock import MagicMock, patch

from src.core.schemas import UpdateMediaBuyRequest
from tests.harness._mixins import make_adapter_update_side_effect
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.media_buy_update import _WRAPPER_UNSUPPORTED_FIELDS

_UPDATE_MODULE = "src.core.tools.media_buy_update"

_UPDATE_PATCHES = {
    "update_adapter": f"{_UPDATE_MODULE}.get_adapter",
    "update_audit": f"{_UPDATE_MODULE}.get_audit_logger",
    "update_context_mgr": f"{_UPDATE_MODULE}.get_context_manager",
}


def _is_update_request(kwargs: dict[str, Any]) -> bool:
    req = kwargs.get("req")
    if isinstance(req, UpdateMediaBuyRequest):
        return True
    if req is not None:
        return False  # an explicit non-update req (e.g. a CreateMediaBuyRequest)
    # No req object — a raw payload (e.g. an update whose invalid revision failed
    # UpdateMediaBuyRequest construction, so the step sends the raw fields). It
    # MUST still route to update, not create: ``revision`` is an update-only
    # optimistic-concurrency token and ``media_buy_id`` targets an existing buy,
    # so either identifies a raw update payload. Routing it to create would fail
    # on missing brand/packages/start_time instead of validating the revision.
    return "revision" in kwargs or "media_buy_id" in kwargs


class MediaBuyDualEnv(MediaBuyCreateEnv):
    """Extends MediaBuyCreateEnv with update-path dispatch for UC-026 scenarios.

    Adds patches for the update module (adapter, audit, context_mgr) alongside
    the create module patches. Routes UpdateMediaBuyRequest through update
    wrappers instead of create wrappers.
    """

    _seeded_media_buy_id: str = "NOT_SEEDED"

    def __enter__(self) -> Self:
        result = super().__enter__()
        self._update_patchers: list = []
        for name, target in _UPDATE_PATCHES.items():
            patcher = patch(target)
            self.mock[name] = patcher.start()
            self._update_patchers.append(patcher)
        self._configure_update_mocks()
        return result

    def __exit__(self, *exc: object) -> bool:
        for patcher in reversed(self._update_patchers):
            try:
                patcher.stop()
            except Exception:
                pass
        self._update_patchers = []
        return super().__exit__(*exc)

    def _configure_update_mocks(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        mock_adapter.validate_media_buy_request.return_value = None
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None

        mock_adapter.update_media_buy.side_effect = make_adapter_update_side_effect()
        self.mock["update_adapter"].return_value = mock_adapter

        mock_audit = MagicMock()
        mock_audit.log_operation.return_value = None
        mock_audit.log_security_violation.return_value = None
        self.mock["update_audit"].return_value = mock_audit

        self.mock["update_context_mgr"].return_value = self._build_mock_context_manager(tool_name="update_media_buy")

    # -- Update dispatch methods -----------------------------------------------

    def call_impl(self, **kwargs: Any) -> Any:
        if _is_update_request(kwargs):
            return self._call_update_impl(**kwargs)
        return super().call_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> Any:
        if _is_update_request(kwargs):
            return self._call_update_a2a(**kwargs)
        return super().call_a2a(**kwargs)

    def call_mcp(self, **kwargs: Any) -> Any:
        if _is_update_request(kwargs):
            return self._call_update_mcp(**kwargs)
        return super().call_mcp(**kwargs)

    def _run_rest_request(self, endpoint: str, **kwargs: Any) -> Any:
        if _is_update_request(kwargs):
            return self._run_update_rest_request(**kwargs)
        return super()._run_rest_request(endpoint, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        # Selecting the parser here (not in a per-layer boolean) keeps every
        # build→parse path consistent — including dispatcher-driven ones that
        # never route through _run_rest_request. See MediaBuyCreateEnv._rest_parser.
        if _is_update_request(kwargs):
            self._rest_parser = self._parse_update_rest_response
            return self._build_update_rest_body(**kwargs)
        return super().build_rest_body(**kwargs)

    # -- Concrete update transport implementations -----------------------------

    def _call_update_impl(self, **kwargs: Any) -> Any:
        if self.e2e_config is not None:
            return self._call_update_via_live_server(kwargs)

        from src.core.tools.media_buy_update import _update_media_buy_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None:
            from src.core.schemas import UpdateMediaBuyRequest as UMR

            req = UMR(**kwargs)
        return _update_media_buy_impl(req=req, identity=identity)

    def _call_update_via_live_server(self, kwargs: dict[str, Any]) -> Any:
        """Realize an update on the LIVE SERVER (transport-aware Given plumbing).

        The in-process impl reads/writes the suite DB, which the live server
        never sees (docs/test-redesign/e2e-rest-ledger-retirement.md). The
        update endpoint is per-buy (PUT /api/v1/media-buys/{id}), which the
        static-endpoint RestE2EDispatcher cannot express — drive the shared
        ``live_server_request`` with the update body/parse surface this env
        already owns.
        """
        identity = kwargs.pop("identity", self.identity)
        endpoint = self._update_endpoint(kwargs)
        body = self._build_update_rest_body(**kwargs)
        data = self.live_server_request("put", endpoint, body=body, identity=identity)
        return self._parse_update_rest_response(data)

    def _call_update_a2a(self, **kwargs: Any) -> Any:
        from src.core.tools.media_buy_update import update_media_buy_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is not None:
            flat = req.model_dump(mode="json", exclude_none=True)
            for key in _WRAPPER_UNSUPPORTED_FIELDS:
                flat.pop(key, None)
            flat.update(kwargs)
            kwargs = flat
        return update_media_buy_raw(**kwargs)

    def _call_update_mcp(self, **kwargs: Any) -> Any:
        import asyncio
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock as MM

        from fastmcp.server.context import Context

        from src.core.tool_error_logging import _translate_to_tool_error
        from src.core.tools.media_buy_update import update_media_buy
        from tests.harness.transport import Transport

        self._commit_factory_data()

        req = kwargs.pop("req", None)
        if req is not None:
            flat = req.model_dump(mode="json", exclude_none=True)
            for key in _WRAPPER_UNSUPPORTED_FIELDS:
                flat.pop(key, None)
            flat.update(kwargs)
            kwargs = flat

        identity = kwargs.pop("identity", self.identity_for(Transport.MCP))
        mock_ctx = MM(spec=Context)

        # The wrapper reads BOTH get_state("identity") and get_state("context_id");
        # a plain return_value would hand the identity object to the context_id
        # DB filter (psycopg2 "can't adapt type 'ResolvedIdentity'").
        async def _get_state(key: str) -> Any:
            return identity if key == "identity" else None

        mock_ctx.get_state = AsyncMock(side_effect=_get_state)

        # Direct-wrapper dispatch (bypasses FastMCP's TypeAdapter, so the AdCP
        # boundary — not FastMCP's arg validation — rejects a bad field, matching
        # a2a/rest). On error, mirror the FULL production MCP boundary round-trip
        # the in-memory Client would run: translate AdCPError -> AdCPToolError
        # (two-layer JSON envelope), then unwrap it back to a reconstructed
        # AdCPError with the real envelope stashed as ``_wire_error_envelope`` —
        # exactly what ``_run_mcp_client`` does. This gives the McpDispatcher BOTH
        # a proper AdCPError (``ctx["error"]``) and the real
        # ``wire_error_envelope`` (e.g. the CONFLICT resource_id/expected/current
        # details). A bare AdCPError would yield ``wire_error_envelope=None``.
        from tests.harness._base import _unwrap_mcp_tool_error

        try:
            tool_result = asyncio.run(update_media_buy(ctx=mock_ctx, **kwargs))
        except Exception as exc:  # noqa: BLE001 — normalized to the MCP wire shape below
            try:
                _translate_to_tool_error(exc)  # always raises (AdCPToolError / ToolError)
            except Exception as tool_exc:
                raise _unwrap_mcp_tool_error(tool_exc) from exc
        data = dict(tool_result.structured_content)
        return self._parse_update_rest_response(data)

    def _build_update_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.pop("identity", None)
        req = kwargs.pop("req", None)
        if req is not None:
            body = req.model_dump(mode="json", exclude_none=True)
            body.pop("media_buy_id", None)
            return body
        kwargs.pop("media_buy_id", None)
        return kwargs

    def _update_endpoint(self, kwargs: dict[str, Any]) -> str:
        """Per-buy REST update endpoint; falls back to the seeded buy when the request omits the id."""
        req = kwargs.get("req")
        media_buy_id = getattr(req, "media_buy_id", None) or self._seeded_media_buy_id
        return f"/api/v1/media-buys/{media_buy_id}"

    def rest_dispatch_target(self, kwargs: dict[str, Any]) -> tuple[str, str]:
        """(method, endpoint) for the e2e REST dispatcher, resolved per request.

        Updates PUT the per-buy route; everything else takes the env's static
        endpoint (create here; the lifecycle subclass's query route for lists).
        """
        if _is_update_request(kwargs):
            return "put", self._update_endpoint(kwargs)
        return getattr(self, "REST_METHOD", "post"), self.REST_ENDPOINT

    def _run_update_rest_request(self, **kwargs: Any) -> Any:
        from tests.harness.transport import Transport

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        if identity is _NO_OVERRIDE:
            identity = self.identity_for(Transport.REST)

        self._commit_factory_data()
        client = self.get_rest_client()

        # Reuse the shared wire-header builder so REST stamps x-dry-run in
        # dry-run mode exactly like the A2A/MCP paths (was dropped here). See #1544.
        headers: dict[str, str] = {}
        if identity is not None and identity.auth_token:
            headers = self._wire_auth_headers(identity.auth_token, identity.tenant_id)

        endpoint = self._update_endpoint(kwargs)
        # Route through build_rest_body (not _build_update_rest_body directly):
        # the dispatcher parses AFTER this method returns, so the parse
        # selector must be stashed alongside the body build.
        body = self.build_rest_body(**kwargs)
        return client.put(endpoint, json=body, headers=headers)

    def _parse_update_rest_response(self, data: dict[str, Any]) -> Any:
        from src.core.schemas._base import UpdateMediaBuyError, UpdateMediaBuySuccess

        if "errors" in data and data["errors"]:
            return UpdateMediaBuyError(**data)
        return UpdateMediaBuySuccess(**data)
