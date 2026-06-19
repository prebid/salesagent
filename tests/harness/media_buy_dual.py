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
    return isinstance(req, UpdateMediaBuyRequest)


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
            # Set flag before returning — reset in parse_rest_response after routing,
            # not in a finally block here (the dispatcher calls parse_rest_response
            # after this method returns, so a finally reset races with routing).
            self._active_update = True
            return self._run_update_rest_request(**kwargs)
        return super()._run_rest_request(endpoint, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        # The E2E dispatcher (RestE2EDispatcher) reads REST_ENDPOINT/REST_METHOD as
        # plain attrs and never calls _run_rest_request, so set the mode flag + target
        # id HERE (deterministically per request, not via parse_rest_response's reset —
        # the E2E error path calls parse_rest_error, which would leave a stale flag).
        if _is_update_request(kwargs):
            self._active_update = True
            req = kwargs.get("req")
            target = self._seeded_media_buy_id
            if req is not None and getattr(req, "media_buy_id", None):
                target = req.media_buy_id
            self._update_target_id = target
            return self._build_update_rest_body(**kwargs)
        self._active_update = False
        return super().build_rest_body(**kwargs)

    @property
    def REST_ENDPOINT(self) -> str:  # noqa: N802 — matches the inherited class-attr name
        """Update scenarios PUT a per-id endpoint; create scenarios POST the collection.

        A @property (not a static attr) because the E2E dispatcher reads it directly and
        the update path needs the seeded media_buy_id in the URL. The in-process path
        ignores this value (it builds its own PUT URL in _run_update_rest_request)."""
        if self._active_update:
            return f"/api/v1/media-buys/{self._update_target_id}"
        return "/api/v1/media-buys"

    @property
    def REST_METHOD(self) -> str:  # noqa: N802 — dispatcher reads getattr(env, "REST_METHOD", "post")
        return "put" if self._active_update else "post"

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        if self._active_update:
            self._active_update = False
            return self._parse_update_rest_response(data)
        return super().parse_rest_response(data)

    _active_update: bool = False
    _update_target_id: str = "NOT_SEEDED"

    # -- Concrete update transport implementations -----------------------------

    def _call_update_impl(self, **kwargs: Any) -> Any:
        from src.core.tools.media_buy_update import _update_media_buy_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", self.identity)
        req = kwargs.pop("req", None)
        if req is None:
            from src.core.schemas import UpdateMediaBuyRequest as UMR

            req = UMR(**kwargs)
        return _update_media_buy_impl(req=req, identity=identity)

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
        from unittest.mock import MagicMock as MM

        from fastmcp.server.context import Context

        from src.core.tool_error_logging import with_error_logging
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

        # The update MCP wrapper reads two distinct state keys:
        # get_state("identity") and get_state("context_id"). A blanket
        # return_value=identity would feed the ResolvedIdentity in as the
        # context_id, which then hits a DB query and fails to adapt. Return the
        # identity only for the "identity" key; no buyer-supplied context.
        async def _get_state(key: str) -> Any:
            return identity if key == "identity" else None

        mock_ctx.get_state = _get_state

        # Invoke through the production boundary decorator that real MCP
        # registration applies (src/core/main.py: mcp.tool()(with_error_logging(fn))).
        # On error this translates the raised AdCPError into an AdCPToolError
        # carrying the two-layer wire envelope, which McpDispatcher captures as
        # wire_error_envelope — calling the raw wrapper would let the bare
        # exception escape with no wire envelope (salesagent-ihwl).
        wrapped_update_media_buy = with_error_logging(update_media_buy)
        tool_result = asyncio.run(wrapped_update_media_buy(ctx=mock_ctx, **kwargs))
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

    def _run_update_rest_request(self, **kwargs: Any) -> Any:
        from tests.harness.transport import Transport

        _NO_OVERRIDE = object()
        identity = kwargs.pop("identity", _NO_OVERRIDE)
        if identity is _NO_OVERRIDE:
            identity = self.identity_for(Transport.REST)

        self._commit_factory_data()
        client = self.get_rest_client()

        headers: dict[str, str] = {}
        if identity is not None:
            auth_token = identity.auth_token
            if auth_token:
                headers["x-adcp-auth"] = auth_token
            if identity.tenant_id:
                headers["x-adcp-tenant"] = identity.tenant_id

        body = self._build_update_rest_body(**kwargs)
        req = kwargs.get("req")
        media_buy_id = self._seeded_media_buy_id
        if req is not None and hasattr(req, "media_buy_id") and req.media_buy_id:
            media_buy_id = req.media_buy_id
        endpoint = f"/api/v1/media-buys/{media_buy_id}"
        return client.put(endpoint, json=body, headers=headers)

    def _parse_update_rest_response(self, data: dict[str, Any]) -> Any:
        from src.core.schemas._base import UpdateMediaBuyError, UpdateMediaBuyResult, UpdateMediaBuySuccess

        status = data.pop("status", "completed")
        if "errors" in data and data["errors"]:
            response = UpdateMediaBuyError(**data)
        else:
            response = UpdateMediaBuySuccess(**data)
        return UpdateMediaBuyResult(response=response, status=status)
