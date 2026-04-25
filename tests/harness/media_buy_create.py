"""MediaBuyCreateEnv -- integration test environment for _create_media_buy_impl.

Mirrors the ProductEnv pattern: subclasses IntegrationEnv, provides a sync/async
bridging call_impl, and lets tests bring their own catalog (Tenant + Principal +
PropertyTag + Product + PricingOption) via factories within the with-block.

Usage::

    @pytest.mark.requires_db
    def test_something(integration_db):
        with MediaBuyCreateEnv(tenant_id="t1", principal_id="p1", dry_run=True) as env:
            tenant = TenantFactory(tenant_id="t1")
            PropertyTagFactory(tenant=tenant, tag_id="all_inventory")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            product = ProductFactory(tenant=tenant, product_id="prod_display")
            PricingOptionFactory(product=product)
            env.commit_catalog()

            result = env.call_impl(req=CreateMediaBuyRequest(...))
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from src.core.schemas import CreateMediaBuyResult
from src.core.tools.media_buy_create import _create_media_buy_impl, create_media_buy_raw
from tests.harness._base import IntegrationEnv


class MediaBuyCreateEnv(IntegrationEnv):
    """Integration env for _create_media_buy_impl.

    No external services patched -- mock adapter handles tenants with
    ``ad_server="mock"``, and dry_run mode skips workflow side effects.

    Three dispatch paths for cross-transport differential testing:

    * ``call_impl(req=...)``         -> direct _impl (CreateMediaBuyResult)
    * ``call_a2a_as_dict(req=...)``  -> create_media_buy_raw (A2A wrapper), dict
    * ``call_mcp_as_dict(req=...)``  -> full FastMCP Client pipeline,       dict

    CreateMediaBuyResult has a custom ``@model_serializer`` that flattens
    ``response`` fields plus ``status`` into a single dict, so MCP/A2A results
    are directly comparable via dict equality after normalization.
    """

    EXTERNAL_PATCHES: dict[str, str] = {}

    def commit_catalog(self) -> None:
        """Flush + commit factory-created rows so _impl's separate session sees them."""
        if self._session is not None:
            self._session.commit()

    def call_impl(self, **kwargs: Any) -> CreateMediaBuyResult:  # type: ignore[override]
        req = kwargs.pop("req")
        identity = kwargs.pop("identity", None) or self.identity

        async def _run() -> CreateMediaBuyResult:
            return await _create_media_buy_impl(req=req, identity=identity, **kwargs)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        return _run()  # type: ignore[return-value]  # caller in async ctx awaits

    # ------------------------------------------------------------------ #
    # Cross-transport dispatch (flat-dict return for differential)
    # ------------------------------------------------------------------ #

    def call_a2a_as_dict(self, **kwargs: Any) -> dict[str, Any]:
        """Call the A2A ``_raw`` wrapper. Returns flattened result dict.

        Follows the CreativeSyncEnv precedent of calling ``*_raw`` directly
        rather than going through ``_run_a2a_handler``, which has known
        skill-handler issues for some operations.
        """
        req = kwargs.pop("req")
        identity = kwargs.pop("identity", None) or self.identity
        self._commit_factory_data()

        flat = req.model_dump(exclude_none=True)
        # context is a payload-level field the wrapper handles separately
        flat.pop("context", None)

        async def _run() -> CreateMediaBuyResult:
            return await create_media_buy_raw(**flat, identity=identity, **kwargs)

        result = asyncio.run(_run())
        return result.model_dump(mode="json")

    def call_mcp_as_dict(self, **kwargs: Any) -> dict[str, Any]:
        """Call create_media_buy via FastMCP in-memory Client. Returns
        ``structured_content`` dict unchanged so callers can diff it against
        the dicts from ``call_a2a_as_dict`` and ``call_impl(...).model_dump()``.
        """
        from fastmcp import Client

        from src.core.main import mcp
        from tests.harness.transport import Transport

        req = kwargs.pop("req")
        identity = kwargs.pop("identity", None) or self.identity_for(Transport.MCP)
        self._commit_factory_data()

        arguments = {**req.model_dump(exclude_none=True), **kwargs}

        auth_token = identity.auth_token if identity else None
        if not auth_token:
            raise RuntimeError(
                "MCP dispatch requires an auth_token on identity. Use integration "
                "mode with a PrincipalFactory-created principal."
            )

        headers = {
            "x-adcp-auth": auth_token,
            "x-adcp-tenant": identity.tenant_id or "",
        }

        async def _call() -> dict[str, Any]:
            mock_th = patch("src.core.transport_helpers.get_http_headers", return_value=headers)
            mock_mw = patch("src.core.mcp_auth_middleware.get_http_headers", return_value=headers)
            with mock_th, mock_mw:
                async with Client(mcp) as client:
                    result = await client.call_tool("create_media_buy", arguments)
                    return dict(result.structured_content or {})

        return asyncio.run(_call())
