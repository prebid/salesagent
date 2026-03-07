"""ProductEnv — unit test environment for _get_products_impl.

Patches: ProductUoW, get_principal_object, convert_product_model_to_schema,
         PolicyCheckService, generate_variants_for_brief, DynamicPricingService,
         get_factory (ranking), resolve_property_list, get_db_session,
         get_adapter.

Usage::

    async def test_something(self):
        with ProductEnv() as env:
            env.add_product(product_id="prod_001", name="Display Ad")
            response = await env.call_impl(brief="display ads")
            assert len(response.products) == 1

Available mocks via env.mock:
    "uow"                  -- ProductUoW class mock
    "principal"            -- get_principal_object mock
    "convert"              -- convert_product_model_to_schema mock (identity)
    "policy_service"       -- PolicyCheckService class mock
    "dynamic_variants"     -- generate_variants_for_brief AsyncMock
    "ranking_factory"      -- get_factory mock (AI ranking)
    "dynamic_pricing"      -- DynamicPricingService class mock
    "resolve_property_list" -- resolve_property_list AsyncMock
    "db_session"           -- get_db_session mock (for pricing session)
    "get_adapter"          -- get_adapter mock (for adapter support annotation)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import Product
from tests.harness._base import BaseTestEnv
from tests.harness._mixins import ProductMixin

_DEFAULT_PRICING_OPTION = {
    "pricing_model": "cpm",
    "rate": "5.00",
    "currency": "USD",
    "pricing_option_id": "po_default",
}

_DEFAULT_PUBLISHER_PROPERTY = {
    "selection_type": "all",
    "publisher_domain": "test-publisher.com",
}


def _make_product(
    product_id: str = "prod_001",
    name: str = "Test Product",
    description: str = "A test product",
    format_ids: list[dict[str, str]] | None = None,
    property_tags: list[str] | None = None,
    delivery_type: str = "guaranteed",
    pricing_options: list[dict[str, Any]] | None = None,
    allowed_principal_ids: list[str] | None = None,
    channels: list[str] | None = None,
    delivery_measurement: dict[str, str] | None = None,
    publisher_properties: list[dict[str, Any]] | None = None,
    estimated_exposures: int | None = None,
    **extra: Any,
) -> Product:
    """Build a Product schema instance for unit testing."""
    return Product(
        product_id=product_id,
        name=name,
        description=description,
        format_ids=format_ids or [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        property_tags=property_tags or ["all_inventory"],
        delivery_type=delivery_type,
        pricing_options=pricing_options or [_DEFAULT_PRICING_OPTION],
        allowed_principal_ids=allowed_principal_ids,
        channels=channels or [],
        delivery_measurement=delivery_measurement or {"provider": "publisher"},
        publisher_properties=publisher_properties or [_DEFAULT_PUBLISHER_PROPERTY],
        estimated_exposures=estimated_exposures,
        **extra,
    )


class ProductEnv(ProductMixin, BaseTestEnv):
    """Unit test environment for _get_products_impl.

    All dependencies mocked. Fast, isolated.

    Fluent API (from ProductMixin):
        set_policy_approved()            -- policy check returns approved
        set_policy_blocked(reason)       -- policy check returns blocked
        set_dynamic_variants(variants)   -- configure dynamic variant generation
        set_property_list(ids)           -- configure property list resolver
        set_ranking_disabled()           -- disable AI ranking
        call_impl(brief, **kw)           -- call _get_products_impl

    Unit-only API:
        add_product(...)                 -- add a Product schema to the UoW repo
    """

    MODULE = "src.core.tools.products"
    EXTERNAL_PATCHES = {
        "uow": "src.core.database.repositories.uow.ProductUoW",
        "principal": f"{MODULE}.get_principal_object",
        "convert": f"{MODULE}.convert_product_model_to_schema",
        "policy_service": f"{MODULE}.PolicyCheckService",
        "dynamic_variants": "src.services.dynamic_products.generate_variants_for_brief",
        "ranking_factory": "src.services.ai.factory.get_factory",
        "dynamic_pricing": "src.services.dynamic_pricing_service.DynamicPricingService",
        "resolve_property_list": "src.core.property_list_resolver.resolve_property_list",
        "db_session": f"{MODULE}.get_db_session",
        "get_adapter": "src.core.helpers.adapter_helpers.get_adapter",
    }

    ASYNC_PATCHES = {"dynamic_variants", "resolve_property_list"}

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._products: list[Product] = []
        self._uow_instance: MagicMock | None = None

    def _configure_mocks(self) -> None:
        # Principal: return a valid mock principal
        self.mock["principal"].return_value = MagicMock(
            principal_id=self._principal_id,
            name="Test Principal",
            platform_mappings={"mock": {"advertiser_id": "test_adv"}},
        )

        # UoW: context manager with product repository
        self._uow_instance = MagicMock()
        self._uow_instance.products = MagicMock()
        self._uow_instance.products.list_all.return_value = []
        self._uow_instance.__enter__ = MagicMock(return_value=self._uow_instance)
        self._uow_instance.__exit__ = MagicMock(return_value=False)
        self.mock["uow"].return_value = self._uow_instance

        # Convert: identity function (return product as-is)
        self.mock["convert"].side_effect = lambda product_obj, **kw: product_obj

        # DB session: no-op context manager (used by pricing block)
        mock_session = MagicMock()
        self.mock["db_session"].return_value.__enter__ = MagicMock(return_value=mock_session)
        self.mock["db_session"].return_value.__exit__ = MagicMock(return_value=False)

        # Adapter: mock with supported pricing models
        mock_adapter = MagicMock()
        mock_adapter.get_supported_pricing_models.return_value = ["cpm", "cpc", "flat_rate"]
        self.mock["get_adapter"].return_value = mock_adapter

        # Product mixin defaults (policy, variants, pricing, ranking, property list)
        self._configure_product_mocks()

    def add_product(
        self,
        product_id: str = "prod_001",
        name: str = "Test Product",
        description: str = "A test product",
        format_ids: list[dict[str, str]] | None = None,
        property_tags: list[str] | None = None,
        delivery_type: str = "guaranteed",
        pricing_options: list[dict[str, Any]] | None = None,
        allowed_principal_ids: list[str] | None = None,
        channels: list[str] | None = None,
        delivery_measurement: dict[str, str] | None = None,
        publisher_properties: list[dict[str, Any]] | None = None,
        estimated_exposures: int | None = None,
        **extra: Any,
    ) -> Product:
        """Add a Product schema instance to the UoW repository.

        Returns the Product for further customization.
        """
        product = _make_product(
            product_id=product_id,
            name=name,
            description=description,
            format_ids=format_ids,
            property_tags=property_tags,
            delivery_type=delivery_type,
            pricing_options=pricing_options,
            allowed_principal_ids=allowed_principal_ids,
            channels=channels,
            delivery_measurement=delivery_measurement,
            publisher_properties=publisher_properties,
            estimated_exposures=estimated_exposures,
            **extra,
        )
        self._products.append(product)

        # Update UoW repo mock
        if self._uow_instance is not None:
            self._uow_instance.products.list_all.return_value = list(self._products)

        return product
