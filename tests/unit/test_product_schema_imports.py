"""Guard test: Product schema imports work from both old and new locations.

This test ensures backward compatibility after extracting Product schemas
from src/core/schemas.py into src/core/schemas/product.py.
Both import paths must resolve to the same classes.
"""


class TestProductSchemaImportPaths:
    """Verify product schemas are importable from src.core.schemas (backward compat)."""

    def test_product_importable_from_schemas(self):
        from src.core.schemas import Product

        assert Product is not None

    def test_product_card_importable_from_schemas(self):
        from src.core.schemas import ProductCard

        assert ProductCard is not None

    def test_product_card_detailed_importable_from_schemas(self):
        from src.core.schemas import ProductCardDetailed

        assert ProductCardDetailed is not None

    def test_placement_importable_from_schemas(self):
        from src.core.schemas import Placement

        assert Placement is not None

    def test_product_performance_importable_from_schemas(self):
        from src.core.schemas import ProductPerformance

        assert ProductPerformance is not None

    def test_product_filters_importable_from_schemas(self):
        from src.core.schemas import ProductFilters

        assert ProductFilters is not None

    def test_get_products_request_importable_from_schemas(self):
        from src.core.schemas import GetProductsRequest

        assert GetProductsRequest is not None

    def test_get_products_response_importable_from_schemas(self):
        from src.core.schemas import GetProductsResponse

        assert GetProductsResponse is not None

    def test_product_catalog_importable_from_schemas(self):
        from src.core.schemas import ProductCatalog

        assert ProductCatalog is not None


class TestProductSchemaFromSubmodule:
    """Verify product schemas are importable from src.core.schemas.product."""

    def test_product_importable_from_product_module(self):
        from src.core.schemas.product import Product

        assert Product is not None

    def test_get_products_response_importable_from_product_module(self):
        from src.core.schemas.product import GetProductsResponse

        assert GetProductsResponse is not None

    def test_product_filters_importable_from_product_module(self):
        from src.core.schemas.product import ProductFilters

        assert ProductFilters is not None


class TestProductSchemaIdentity:
    """Verify both import paths resolve to the SAME class objects."""

    def test_product_is_same_class(self):
        from src.core.schemas import Product as FromSchemas
        from src.core.schemas.product import Product as FromProduct

        assert FromSchemas is FromProduct

    def test_get_products_response_is_same_class(self):
        from src.core.schemas import GetProductsResponse as FromSchemas
        from src.core.schemas.product import GetProductsResponse as FromProduct

        assert FromSchemas is FromProduct

    def test_product_filters_is_same_class(self):
        from src.core.schemas import ProductFilters as FromSchemas
        from src.core.schemas.product import ProductFilters as FromProduct

        assert FromSchemas is FromProduct
