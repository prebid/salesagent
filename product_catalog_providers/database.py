"""Database-backed product catalog provider (current implementation)."""

import logging
from typing import Any

from sqlalchemy.orm import joinedload

from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ProductModel
from src.core.product_conversion import convert_product_model_to_schema
from src.core.schemas import Product

from .base import ProductCatalogProvider

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class DatabaseProductCatalog(ProductCatalogProvider):
    """
    Simple database-backed product catalog.
    Returns all products from the database without filtering by brief.

    This maintains backward compatibility with the current implementation.
    """

    async def get_products(
        self,
        brief: str,
        tenant_id: str,
        principal_id: str | None = None,
        context: dict[str, Any] | None = None,
        principal_data: dict[str, Any] | None = None,
    ) -> list[Product]:
        """
        Get all products for the tenant from the database.

        Note: Currently ignores the brief and returns all products.
        Future enhancement could add brief-based filtering.
        """
        with get_db_session() as db_session:
            # Eager load pricing_options relationship to avoid N+1 queries
            # Use SQLAlchemy 2.0 select() pattern for consistency
            from sqlalchemy import select

            stmt = (
                select(ProductModel)
                .options(joinedload(ProductModel.pricing_options), joinedload(ProductModel.tenant))
                .filter_by(tenant_id=tenant_id)
                .order_by(ProductModel.product_id)
            )
            # unique() must be called on execute result BEFORE scalars() with joinedload
            result = db_session.execute(stmt).unique()
            products = list(result.scalars().all())

            # Convert database Product models to AdCP Product schema
            loaded_products = []
            for product_obj in products:
                try:
                    # Use shared conversion function (handles all required fields, pricing options, etc.)
                    validated_product = convert_product_model_to_schema(product_obj)
                    loaded_products.append(validated_product)
                    logger.debug(f"Successfully converted product {product_obj.product_id}")
                except Exception as e:
                    # CRITICAL: Product conversion failures indicate data corruption or schema mismatch
                    # We MUST fail loudly, not silently skip products
                    error_msg = (
                        f"Product '{product_obj.product_id}' failed to convert to AdCP schema. "
                        f"This indicates data corruption or migration issue. Error: {e}"
                    )
                    logger.error(error_msg)
                    # Re-raise with context - don't silently skip products!
                    raise ValueError(error_msg) from e

            # Return library Product list - compatible with our extended Product at runtime
            return loaded_products  # type: ignore[return-value]
