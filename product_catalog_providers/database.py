"""Database-backed product catalog provider (current implementation)."""

import json
import logging
from typing import Any

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption as PricingOptionModel
from src.core.database.models import Product as ProductModel
from src.core.schemas import PriceGuidance, PricingModel, PricingOption, PricingParameters, Product

from .base import ProductCatalogProvider

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _convert_pricing_option(po: PricingOptionModel) -> PricingOption:
    """Convert database PricingOption to Pydantic PricingOption."""
    return PricingOption(
        pricing_model=PricingModel(po.pricing_model),
        rate=float(po.rate) if po.rate is not None else None,
        currency=po.currency,
        is_fixed=po.is_fixed,
        price_guidance=(
            PriceGuidance(
                floor=po.price_guidance.get("floor", 0.0),
                p25=po.price_guidance.get("p25"),
                p50=po.price_guidance.get("p50"),
                p75=po.price_guidance.get("p75"),
                p90=po.price_guidance.get("p90"),
            )
            if po.price_guidance
            else None
        ),
        parameters=PricingParameters(**po.parameters) if po.parameters else None,
        min_spend_per_package=float(po.min_spend_per_package) if po.min_spend_per_package is not None else None,
    )


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
            products = (
                db_session.query(ProductModel).filter_by(tenant_id=tenant_id).order_by(ProductModel.product_id).all()
            )

            loaded_products = []
            for product_obj in products:
                # Load pricing options if available (AdCP PR #88)
                pricing_options = None
                if product_obj.pricing_options:
                    try:
                        pricing_options = [_convert_pricing_option(po) for po in product_obj.pricing_options]
                    except Exception as e:
                        logger.warning(f"Failed to load pricing options for product {product_obj.product_id}: {e}")
                        # Fall back to legacy pricing fields
                        pricing_options = None

                # Convert ORM object to dictionary
                product_data = {
                    "product_id": product_obj.product_id,
                    "name": product_obj.name,
                    "description": product_obj.description,
                    "formats": product_obj.formats,
                    "delivery_type": product_obj.delivery_type,
                    # NEW: Pricing options (AdCP PR #88)
                    "pricing_options": pricing_options,
                    # DEPRECATED: Legacy pricing fields (still supported for backward compatibility)
                    "is_fixed_price": product_obj.is_fixed_price,
                    "cpm": product_obj.cpm,
                    "min_spend": product_obj.min_spend,
                    "currency": product_obj.currency,
                    "price_guidance": product_obj.price_guidance,
                    "is_custom": product_obj.is_custom,
                    "countries": product_obj.countries,
                }

                # Handle JSONB fields - PostgreSQL returns them as Python objects, SQLite as strings
                if product_data.get("formats"):
                    if isinstance(product_data["formats"], str):
                        product_data["formats"] = json.loads(product_data["formats"])

                # Remove internal fields that shouldn't be exposed to buyers
                product_data.pop("targeting_template", None)  # Internal targeting config
                product_data.pop("price_guidance", None)  # Not part of Product schema
                product_data.pop("implementation_config", None)  # Proprietary ad server config
                product_data.pop("countries", None)  # Not part of Product schema

                # Fix missing required fields for Pydantic validation

                # 1. Fix missing description (required field)
                if not product_data.get("description"):
                    product_data["description"] = f"Advertising product: {product_data.get('name', 'Unknown Product')}"

                # 2. Fix missing is_custom (should default to False)
                if product_data.get("is_custom") is None:
                    product_data["is_custom"] = False

                # 3. Convert formats to format IDs (strings) as expected by Product schema
                if product_data.get("formats"):
                    logger.debug(
                        f"Original formats for {product_data.get('product_id')}: {product_data['formats']} (type: {type(product_data['formats'])})"
                    )
                    format_ids = []
                    for i, format_obj in enumerate(product_data["formats"]):
                        logger.debug(f"Processing format {i}: {format_obj} (type: {type(format_obj)})")
                        # Handle case where format_obj might be a string instead of dict
                        if isinstance(format_obj, str):
                            # Check if it's a JSON string first
                            try:
                                parsed = json.loads(format_obj)
                                if isinstance(parsed, dict) and "format_id" in parsed:
                                    # It's a format object with format_id
                                    format_ids.append(parsed["format_id"])
                                    logger.debug(f"Extracted format_id from JSON string: {parsed['format_id']}")
                                else:
                                    # It's just a format identifier string
                                    format_ids.append(format_obj)
                                    logger.debug(f"Using string as format_id: {format_obj}")
                            except (json.JSONDecodeError, TypeError):
                                # It's a plain string format identifier
                                format_ids.append(format_obj)
                                logger.debug(f"Using plain string as format_id: {format_obj}")
                        elif isinstance(format_obj, dict):
                            # It's a format object, extract the format_id
                            format_id = format_obj.get("format_id")
                            if format_id:
                                format_ids.append(format_id)
                                logger.debug(f"Extracted format_id from dict: {format_id}")
                            else:
                                # Try to construct format_id from other fields
                                name = format_obj.get("name", "unknown_format")
                                format_ids.append(name)
                                logger.debug(f"Using name as format_id: {name}")
                        else:
                            logger.warning(f"Skipping unexpected format type: {type(format_obj)} - {format_obj}")
                            continue

                    product_data["formats"] = format_ids
                    logger.debug(f"Final converted formats for {product_data.get('product_id')}: {format_ids}")

                # 4. Convert DECIMAL fields to float for Pydantic validation
                if product_data.get("min_spend") is not None:
                    logger.debug(
                        f"Original min_spend for {product_data.get('product_id')}: {product_data['min_spend']} (type: {type(product_data['min_spend'])})"
                    )
                    try:
                        product_data["min_spend"] = float(product_data["min_spend"])
                        logger.debug(f"Converted min_spend to float: {product_data['min_spend']}")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Failed to convert min_spend to float: {e}, setting to None")
                        product_data["min_spend"] = None

                if product_data.get("cpm") is not None:
                    logger.debug(
                        f"Original cpm for {product_data.get('product_id')}: {product_data['cpm']} (type: {type(product_data['cpm'])})"
                    )
                    try:
                        product_data["cpm"] = float(product_data["cpm"])
                        logger.debug(f"Converted cpm to float: {product_data['cpm']}")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Failed to convert cpm to float: {e}, setting to None")
                        product_data["cpm"] = None

                # Validate against AdCP protocol schema before returning
                try:
                    logger.debug(
                        f"About to validate product {product_data.get('product_id')}: price_guidance={product_data.get('price_guidance')} (type: {type(product_data.get('price_guidance'))})"
                    )
                    validated_product = Product(**product_data)
                    loaded_products.append(validated_product)
                    logger.debug(f"Successfully validated product {product_data.get('product_id')}")
                except Exception as e:
                    logger.error(f"Product {product_data.get('product_id')} failed validation: {e}")
                    logger.debug(f"Product data that failed: {product_data}")
                    # Skip invalid products rather than failing entire request
                    continue

            return loaded_products
