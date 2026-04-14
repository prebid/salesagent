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

from src.core.schemas import CreateMediaBuyResult
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.harness._base import IntegrationEnv


class MediaBuyCreateEnv(IntegrationEnv):
    """Integration env for _create_media_buy_impl.

    No external services patched -- mock adapter handles tenants with
    ``ad_server="mock"``, and dry_run mode skips workflow side effects.
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
