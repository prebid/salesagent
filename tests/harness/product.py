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
"""

from __future__ import annotations

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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    def _configure_mocks(self) -> None:
        self._configure_product_mocks()

    REST_ENDPOINT = "/api/v1/products"

    def call_a2a(self, **kwargs: Any) -> GetProductsResponse:
        """Call get_products via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("get_products", GetProductsResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> GetProductsResponse:
        """Call get_products via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("get_products", GetProductsResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetProductsBody shape for REST POST.

        GetProductsBody (src/routes/api_v1.py) accepts:
            brief, brand, filters, adcp_version
        """
        _BODY_FIELDS = ("brief", "brand", "filters", "adcp_version")
        return {k: kwargs[k] for k in _BODY_FIELDS if k in kwargs and kwargs[k] is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> GetProductsResponse:
        """Parse REST JSON response into GetProductsResponse."""
        return GetProductsResponse(**data)
