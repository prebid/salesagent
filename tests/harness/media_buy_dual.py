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
from tests.harness.media_buy_create import MediaBuyCreateEnv

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
        from src.core.schemas import AffectedPackage, UpdateMediaBuySuccess

        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        mock_adapter.validate_media_buy_request.return_value = None
        mock_adapter.add_creative_assets.return_value = None
        mock_adapter.associate_creatives.return_value = None

        def _adapter_update_side_effect(
            media_buy_id: str = "", buyer_ref: str = "", **kwargs: Any
        ) -> UpdateMediaBuySuccess:
            action = kwargs.get("action", "")
            package_id = kwargs.get("package_id") or "pkg_001"
            paused = action in ("pause_media_buy", "pause_package")
            return UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref,
                affected_packages=[AffectedPackage(package_id=package_id, paused=paused)],
            )

        mock_adapter.update_media_buy.side_effect = _adapter_update_side_effect
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
            self._active_update = True
            try:
                return self._run_update_rest_request(**kwargs)
            finally:
                self._active_update = False
        return super()._run_rest_request(endpoint, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        if _is_update_request(kwargs):
            return self._build_update_rest_body(**kwargs)
        return super().build_rest_body(**kwargs)

    def parse_rest_response(self, data: dict[str, Any]) -> Any:
        if self._active_update:
            return self._parse_update_rest_response(data)
        return super().parse_rest_response(data)

    _active_update: bool = False

    # -- Concrete update transport implementations -----------------------------

    def _call_update_impl(self, **kwargs: Any) -> Any:
        from src.core.tools.media_buy_update import _update_media_buy_impl

        self._commit_factory_data()
        identity = kwargs.pop("identity", None) or self.identity
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
            flat.update(kwargs)
            kwargs = flat
        return update_media_buy_raw(**kwargs)

    def _call_update_mcp(self, **kwargs: Any) -> Any:
        import asyncio
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock as MM

        from fastmcp.server.context import Context

        from src.core.tools.media_buy_update import update_media_buy
        from tests.harness.transport import Transport

        self._commit_factory_data()

        req = kwargs.pop("req", None)
        if req is not None:
            flat = req.model_dump(mode="json", exclude_none=True)
            flat.update(kwargs)
            kwargs = flat

        identity = kwargs.pop("identity", None) or self.identity_for(Transport.MCP)
        mock_ctx = MM(spec=Context)
        mock_ctx.get_state = AsyncMock(return_value=identity)

        tool_result = asyncio.run(update_media_buy(ctx=mock_ctx, **kwargs))
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
        from src.core.schemas._base import UpdateMediaBuyError, UpdateMediaBuySuccess

        if "errors" in data and data["errors"]:
            return UpdateMediaBuyError(**data)
        return UpdateMediaBuySuccess(**data)
