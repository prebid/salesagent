"""Helper functions for reading product pricing from database.

Handles transition from legacy pricing fields to pricing_options table.
"""

import logging
from typing import Any

from sqlalchemy import inspect

from src.core.database.models import Product as ProductModel

logger = logging.getLogger(__name__)


def get_product_pricing_options(product: ProductModel) -> list[dict[str, Any]]:
    """Get pricing options for a product, with fallback to legacy fields.

    This function handles the transition period where some products may still
    use legacy pricing fields (cpm, price_guidance) while others use the new
    pricing_options table.

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

    # Try to load from pricing_options relationship first
    if pricing_options_loaded and product.pricing_options:
        for po in product.pricing_options:
            # Generate pricing_option_id if not present (for backward compatibility)
            pricing_option_id = getattr(po, "pricing_option_id", None)
            if not pricing_option_id:
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

    # Fallback to legacy fields if no pricing_options exist
    logger.debug(f"Product {product.product_id} has no pricing_options, using legacy fields")

    # Check if legacy fields exist (they may be removed by migration)
    if not hasattr(product, "is_fixed_price") or not hasattr(product, "cpm"):
        logger.warning(f"Product {product.product_id} has neither pricing_options nor legacy fields")
        return []

    currency = getattr(product, "currency", "USD") or "USD"

    # Convert legacy fixed CPM
    if product.is_fixed_price and product.cpm:
        pricing_options_list.append(
            {
                "pricing_option_id": f"cpm_{currency.lower()}_fixed",
                "pricing_model": "cpm",
                "rate": float(product.cpm),
                "currency": currency,
                "is_fixed": True,
                "price_guidance": None,
                "parameters": None,
                "min_spend_per_package": float(product.min_spend) if product.min_spend else None,
            }
        )

    # Convert legacy auction CPM with price guidance
    elif not product.is_fixed_price and hasattr(product, "price_guidance") and product.price_guidance:
        pg = product.price_guidance
        # Convert old format (min/max) to new format (floor/p90)
        guidance: dict[str, Any]
        if "min" in pg and "floor" not in pg:
            guidance = {"floor": pg["min"]}
            if "max" in pg and pg["max"] != pg["min"]:
                guidance["p90"] = pg["max"]
        else:
            guidance = pg  # type: ignore[assignment]

        pricing_options_list.append(
            {
                "pricing_option_id": f"cpm_{currency.lower()}_auction",
                "pricing_model": "cpm",
                "rate": None,
                "currency": currency,
                "is_fixed": False,
                "price_guidance": guidance,
                "parameters": None,
                "min_spend_per_package": float(product.min_spend) if product.min_spend else None,
            }
        )

    return pricing_options_list


def get_primary_pricing_option(product: ProductModel) -> dict[str, Any] | None:
    """Get the primary (first) pricing option for a product.

    Returns None if product has no pricing.
    """
    options = get_product_pricing_options(product)
    return options[0] if options else None
