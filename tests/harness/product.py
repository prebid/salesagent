"""ProductEnv — integration test environment for _get_products_impl.

Patches: PolicyCheckService, generate_variants_for_brief,
         get_factory (ranking), resolve_property_list.
Real: ProductUoW, get_principal_object, convert_product_model_to_schema,
      DynamicPricingService, adapter metadata, audit logger, get_db_session.

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    async def test_something(self, integration_db):
        with ProductEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            ProductFactory(tenant=tenant)
            PricingOptionFactory(product__tenant=tenant)

            response = await env.call_impl(brief="video ads")
            assert len(response.products) >= 1

Available mocks via env.mock:
    "policy_service"       -- PolicyCheckService class mock
    "dynamic_variants"     -- generate_variants_for_brief AsyncMock
    "ranking_factory"      -- get_factory mock (AI ranking)
    "resolve_property_list" -- resolve_property_list AsyncMock

Transport support:
    call_impl(**kw)          -- direct _get_products_impl (sync wrapper around async)
    call_a2a(**kw)           -- get_products_raw A2A wrapper
    call_mcp(**kw)           -- get_products MCP wrapper via _run_mcp_wrapper
    build_rest_body(**kw)    -- POST /api/v1/products body
    parse_rest_response(d)   -- JSON -> GetProductsResponse
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.schemas import GetProductsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import ProductMixin


class ProductEnv(ProductMixin, IntegrationEnv):
    """Integration test environment for _get_products_impl.

    Only mocks external services (policy, dynamic variants,
    AI ranking, property list resolution). Everything else is real:
    - Real ProductUoW -> real DB queries
    - Real get_principal_object -> real DB queries
    - Real convert_product_model_to_schema -> real conversion
    - Real DynamicPricingService -> real DB queries (FormatPerformanceMetrics)
    - Real audit logging

    Fluent API (from ProductMixin):
        set_policy_approved()            -- policy check returns approved
        set_policy_blocked(reason)       -- policy check returns blocked
        set_dynamic_variants(variants)   -- configure dynamic variant generation
        set_property_list(ids)           -- configure property list resolver
        set_ranking_disabled()           -- disable AI ranking
        call_impl(brief, **kw)           -- call _get_products_impl
    """

    EXTERNAL_PATCHES = {
        "policy_service": "src.core.tools.products.PolicyCheckService",
        "dynamic_variants": "src.services.dynamic_products.generate_variants_for_brief",
        "ranking_factory": "src.services.ai.factory.get_factory",
        "resolve_property_list": "src.core.property_list_resolver.resolve_property_list",
    }

    ASYNC_PATCHES = {"dynamic_variants", "resolve_property_list"}

    REST_ENDPOINT = "/api/v1/products"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def _configure_mocks(self) -> None:
        self._configure_product_mocks()

    def call_impl(self, **kwargs: Any) -> GetProductsResponse:  # type: ignore[override]
        """Sync wrapper around ProductMixin's async call_impl.

        The base ImplDispatcher calls call_impl() synchronously, so this
        override bridges async -> sync via asyncio.run(). Direct callers
        can still ``await super().call_impl()`` from async test functions.
        """
        return asyncio.run(super().call_impl(**kwargs))

    def call_a2a(self, **kwargs: Any) -> GetProductsResponse:
        """Call get_products_raw (A2A wrapper)."""
        from src.core.tools.products import get_products_raw

        self._commit_factory_data()
        identity = kwargs.pop("identity", None) or self.identity
        return asyncio.run(get_products_raw(identity=identity, **kwargs))

    def call_mcp(self, **kwargs: Any) -> GetProductsResponse:
        """Call get_products MCP wrapper with mock Context."""
        from src.core.tools.products import get_products

        # MCP wrapper takes individual params, not 'req'
        kwargs.pop("req", None)
        kwargs.pop("identity", None)  # MCP gets identity from ctx.get_state()
        return self._run_mcp_wrapper(get_products, GetProductsResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetProductsBody shape for REST POST."""
        body: dict[str, Any] = {}
        if "brief" in kwargs:
            body["brief"] = kwargs["brief"]
        if "brand" in kwargs:
            body["brand"] = kwargs["brand"]
        if "filters" in kwargs:
            body["filters"] = kwargs["filters"]
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> GetProductsResponse:
        """Parse REST JSON into GetProductsResponse."""
        return GetProductsResponse(**data)
