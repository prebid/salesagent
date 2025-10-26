"""Helper functions for reading product pricing from database.

Handles transition from legacy pricing fields to pricing_options table.
"""

import logging
from typing import Any

from sqlalchemy import inspect

from src.core.database.models import Product as ProductModel

logger = logging.getLogger(__name__)


def get_product_pricing_options(product: ProductModel) -> list[dict[str, Any]]:
    """Get pricing options for a product.

    All products must have pricing_options - legacy fields are no longer supported.

    Args:
        product: Product ORM model (pricing_options relationship will be loaded if needed)

    Returns:
        List of pricing option dicts with keys:
        - pricing_model: str (e.g., "cpm")
        - rate: float | None
        - currency: str
        - is_fixed: bool
        - price_guidance: dict | None
        - parameters: dict | None
        - min_spend_per_package: float | None
    """
    pricing_options_list = []

    # Check if pricing_options relationship is loaded and has data
    # Use inspect to safely check without triggering lazy load if not needed
    state = inspect(product)
    pricing_options_loaded = "pricing_options" not in state.unloaded

    # Load from pricing_options relationship
    logger.info(f"Product {product.name} ({product.product_id}): pricing_options_loaded={pricing_options_loaded}, has pricing_options={bool(product.pricing_options)}, count={len(product.pricing_options) if pricing_options_loaded else 'N/A'}")
    if pricing_options_loaded and product.pricing_options:
        for po in product.pricing_options:
            # Generate pricing_option_id if not present (for backward compatibility)
            pricing_option_id = getattr(po, "pricing_option_id", None)
            if not pricing_option_id:
                # Construct pricing_option_id in a stable, human-readable format
                # Format: {pricing_model}_{currency}_{fixed|auction}
                fixed_str = "fixed" if po.is_fixed else "auction"
                pricing_option_id = f"{po.pricing_model}_{po.currency.lower()}_{fixed_str}"

            pricing_options_list.append(
                {
                    "pricing_option_id": pricing_option_id,
                    "pricing_model": po.pricing_model,
                    "rate": float(po.rate) if po.rate else None,
                    "currency": po.currency,
                    "is_fixed": po.is_fixed,
                    "price_guidance": po.price_guidance,
                    "parameters": po.parameters,
                    "min_spend_per_package": float(po.min_spend_per_package) if po.min_spend_per_package else None,
                }
            )
        return pricing_options_list

    # Product has no pricing options - this should not happen
    logger.error(f"Product {product.product_id} has no pricing_options - this is a data integrity error")
    return []


def get_primary_pricing_option(product: ProductModel) -> dict[str, Any] | None:
    """Get the primary (first) pricing option for a product.

    Returns None if product has no pricing.
    """
    options = get_product_pricing_options(product)
    return options[0] if options else None
