"""Signals-based product catalog provider for upstream signals discovery integration."""

import logging
from typing import Any

from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ModelProduct
from src.core.database.product_pricing import get_product_pricing_options
from src.core.schemas import Product
from src.core.signals_agent_registry import get_signals_agent_registry

from .base import ProductCatalogProvider

logger = logging.getLogger(__name__)


class SignalsDiscoveryProvider(ProductCatalogProvider):
    """
    Product catalog provider that integrates with upstream AdCP signals discovery agents.

    This provider:
    1. Uses the signals agent registry to query all configured agents
    2. Transforms signals into custom products with appropriate targeting
    3. Falls back to database products if signals agent is unavailable
    4. Only forwards requests when a brief is provided (optimization per issue #106)

    Configuration (product-level settings):
        tenant_id: Required - tenant identifier for agent lookup
        fallback_to_database: Use database products if signals unavailable (default: True)
        max_signal_products: Maximum number of signal products to create (default: 10)

    Note: These are provider config settings, separate from agent-level configuration
    in the signals_agents table.
    """

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.tenant_id = config.get("tenant_id")
        self.fallback_to_database = config.get("fallback_to_database", True)
        self.max_signal_products = config.get("max_signal_products", 10)  # Default max products
        self.registry = get_signals_agent_registry()

    async def initialize(self) -> None:
        """Initialize - no-op since registry handles connections."""
        pass

    async def shutdown(self) -> None:
        """Clean up - no-op since registry manages connections."""
        pass

    async def get_products(
        self,
        brief: str,
        tenant_id: str,
        principal_id: str | None = None,
        context: dict[str, Any] | None = None,
        principal_data: dict[str, Any] | None = None,
    ) -> list[Product]:
        """
        Get products enhanced with signals from upstream discovery agents.

        Implementation follows the requirements from issue #106:
        - Only forward to signals agent if brief is provided
        - Include promoted_offering when configured via agent settings
        - Transform signals into custom products
        - Fall back to database products on error
        """
        products = []

        # Optimization per issue #106: "if there is no brief don't forward"
        if not brief or not brief.strip():
            logger.debug("No brief provided, skipping signals discovery")
            return await self._get_database_products(brief, tenant_id, principal_id)

        # Try to get signals from all configured agents via registry
        try:
            # Use provided tenant_id (required parameter, cannot be None)
            signals = await self.registry.get_signals(
                brief=brief,
                tenant_id=tenant_id,
                principal_id=principal_id,
                context=context,
                principal_data=principal_data,
            )
            if signals:
                logger.info(f"Retrieved {len(signals)} signals from agents")
                products = await self._transform_signals_to_products(signals, brief, tenant_id)
        except Exception as e:
            logger.error(f"Error calling signals discovery agents: {e}")

        # If no products from signals or fallback enabled, include database products
        if not products or self.fallback_to_database:
            database_products = await self._get_database_products(brief, tenant_id, principal_id)
            if not products:
                logger.info("Using database products as primary source")
                products = database_products
            else:
                logger.info(
                    f"Combining {len(products)} signal products with {len(database_products)} database products"
                )
                products.extend(database_products)

        return products

    async def _transform_signals_to_products(
        self, signals: list[dict[str, Any]], brief: str, tenant_id: str
    ) -> list[Product]:
        """Transform signals into custom products with appropriate targeting and pricing."""
        products = []

        # Group signals by category for better organization
        signals_by_category: dict[str, list[dict[str, Any]]] = {}
        for signal in signals:
            category = signal.get("category") or "general"
            if category not in signals_by_category:
                signals_by_category[category] = []
            signals_by_category[category].append(signal)

        product_count = 0
        for category, category_signals in signals_by_category.items():
            if product_count >= self.max_signal_products:
                break

            # Create a product for this category of signals
            product = await self._create_product_from_signals(category_signals, category, brief, tenant_id)
            if product:
                products.append(product)
                product_count += 1

        logger.info(f"Created {len(products)} products from {len(signals)} signals")
        return products

    async def _create_product_from_signals(
        self, signals: list[dict[str, Any]], category: str, brief: str, tenant_id: str
    ) -> Product | None:
        """Create a single product from a group of related signals."""
        if not signals:
            return None

        # Calculate average CPM and coverage
        cpm_values = [s["pricing"]["cpm"] for s in signals if s.get("pricing") and s["pricing"].get("cpm") is not None]
        coverage_percentages = [s["coverage_percentage"] for s in signals if s.get("coverage_percentage") is not None]

        avg_cpm = sum(cpm_values) / len(cpm_values) if cpm_values else 5.0
        total_coverage = sum(coverage_percentages) if coverage_percentages else 0

        # Create targeting overlay with signal IDs
        signal_ids = [s["signal_agent_segment_id"] for s in signals]
        targeting_overlay = {
            "signals": signal_ids,
            "signal_category": category,
            "signal_types": list({s["signal_type"] for s in signals}),
        }

        # Create product name and description
        signal_names = [s["name"] for s in signals[:3]]  # Use first 3 for name
        product_name = f"Signal-Enhanced {category.title()}: {', '.join(signal_names)}"
        if len(signals) > 3:
            product_name += f" (+{len(signals) - 3} more)"

        product_description = f"Custom product targeting based on signals discovery for brief: '{brief[:100]}...'"
        product_description += f"\n\nTargeted signals: {', '.join([s['name'] for s in signals])}"

        # Base price calculation (could be enhanced with more sophisticated logic)
        base_price = 5.00  # Base CPM
        adjusted_price = avg_cpm if avg_cpm > 0 else base_price

        # Generate unique product ID
        import hashlib

        product_id_hash = hashlib.md5(f"signals_{tenant_id}_{category}_{len(signals)}".encode()).hexdigest()[:12]
        product_id = f"signal_{product_id_hash}"

        # Create AdCP-compliant Product (without internal fields like tenant_id)
        from src.core.schemas import PriceGuidance, PricingOption

        return Product(
            product_id=product_id,
            name=product_name,
            description=product_description,
            formats=["display_300x250", "display_728x90", "video_pre_roll"],  # Standard format IDs
            delivery_type="non_guaranteed",  # Signals products are typically programmatic
            floor_cpm=None,  # Optional - using pricing_options instead
            recommended_cpm=None,  # Optional - using pricing_options instead
            measurement=None,  # Optional - signals products don't include measurement
            creative_policy=None,  # Optional - signals products don't include creative policy
            is_custom=True,  # These are custom products created from signals
            brief_relevance=f"Generated from {len(signals)} signals in {category} category for: {brief[:100]}...",
            property_tags=["all_inventory"],  # Required per AdCP spec (using property_tags instead of properties)
            properties=None,  # Using property_tags instead
            estimated_exposures=None,  # Optional - signals products don't have exposure estimates
            delivery_measurement=None,  # Optional - new field from product details
            product_card=None,  # Optional - new field from product details
            product_card_detailed=None,  # Optional - new field from product details
            placements=None,  # Optional - new field from product details
            reporting_capabilities=None,  # Optional - new field from product details
            pricing_options=[
                PricingOption(
                    pricing_option_id="cpm_usd_auction",
                    pricing_model="cpm",  # type: ignore[arg-type]  # String literal matches PricingModel enum
                    rate=None,  # Optional - auction-based pricing doesn't have fixed rate
                    currency="USD",
                    is_fixed=False,
                    price_guidance=PriceGuidance(
                        floor=float(adjusted_price),
                        p25=None,  # Optional percentile
                        p50=float(adjusted_price) * 1.2,
                        p75=float(adjusted_price) * 1.5,
                        p90=float(adjusted_price) * 1.8,  # Required field
                    ),
                    parameters=None,  # Optional - no additional parameters needed
                    min_spend_per_package=100.0,
                    supported=True,  # Required field - signals products are supported
                    unsupported_reason=None,  # Optional field
                )
            ],
        )

    async def _get_database_products(self, brief: str, tenant_id: str, principal_id: str | None) -> list[Product]:
        """Fallback method to get products from database."""
        from sqlalchemy import select

        products = []

        try:
            with get_db_session() as db_session:
                stmt = select(ModelProduct).filter_by(tenant_id=tenant_id)

                # Simple brief matching (could be enhanced with better search)
                if brief and brief.strip():
                    brief_lower = brief.lower()
                    stmt = stmt.where(
                        ModelProduct.name.ilike(f"%{brief_lower}%") | ModelProduct.description.ilike(f"%{brief_lower}%")
                    )

                stmt = stmt.limit(20)
                db_products = db_session.scalars(stmt).all()

                for db_product in db_products:
                    # Convert database model to AdCP-compliant Product schema
                    # (Similar to database.py approach - only include AdCP spec fields)
                    # Get pricing from pricing_options (preferred) or legacy fields (fallback)
                    pricing_options = get_product_pricing_options(db_product)
                    first_pricing = pricing_options[0] if pricing_options else {}

                    product_data = {
                        "product_id": db_product.product_id,
                        "name": db_product.name,
                        "description": db_product.description or f"Advertising product: {db_product.name}",
                        "formats": db_product.formats or [],
                        "delivery_type": "guaranteed" if first_pricing.get("is_fixed") else "non_guaranteed",
                        "is_fixed_price": first_pricing.get("is_fixed", False),
                        "cpm": first_pricing.get("rate"),
                        "min_spend": (
                            float(first_pricing["min_spend_per_package"])
                            if first_pricing.get("min_spend_per_package")
                            else None
                        ),
                        "is_custom": getattr(db_product, "is_custom", False),
                        "property_tags": getattr(
                            db_product, "property_tags", ["all_inventory"]
                        ),  # Required per AdCP spec
                    }

                    # Handle JSON fields (might be strings in SQLite)
                    if isinstance(product_data["formats"], str):
                        import json

                        try:
                            product_data["formats"] = json.loads(product_data["formats"])
                        except json.JSONDecodeError:
                            product_data["formats"] = []

                    # Extract format IDs if formats are objects
                    if product_data["formats"]:
                        format_ids = []
                        for fmt in product_data["formats"]:
                            if isinstance(fmt, dict) and "format_id" in fmt:
                                format_ids.append(fmt["format_id"])
                            elif isinstance(fmt, str):
                                format_ids.append(fmt)
                        product_data["formats"] = format_ids

                    product = Product(**product_data)
                    products.append(product)

        except Exception as e:
            logger.error(f"Error fetching database products: {e}")

        return products
