"""Signals-based product catalog provider for upstream signals discovery integration."""

import logging
from typing import Any

from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ModelProduct
from src.core.product_conversion import convert_product_model_to_schema
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
        from adcp.types import CpmAuctionPricingOption
        from adcp.types.generated_poc.cpm_auction_option import PriceGuidance
        from adcp.types.generated_poc.product import DeliveryMeasurement, PropertyTag, PublisherProperties5

        from src.core.schemas import FormatId

        return Product(
            product_id=product_id,
            name=product_name,
            description=product_description,
            format_ids=[
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),  # type: ignore[arg-type]
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_728x90"),  # type: ignore[arg-type]
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_pre_roll"),  # type: ignore[arg-type]
            ],
            delivery_type="non_guaranteed",  # type: ignore[arg-type]  # String matches DeliveryType enum
            measurement=None,  # Optional - signals products don't include measurement
            creative_policy=None,  # Optional - signals products don't include creative policy
            is_custom=True,  # These are custom products created from signals
            brief_relevance=f"Generated from {len(signals)} signals in {category} category for: {brief[:100]}...",
            publisher_properties=[
                PublisherProperties5(
                    selection_type="by_tag",
                    property_tags=[PropertyTag("all_inventory")],  # Tag for all inventory
                    publisher_domain="publisher.example.com",  # Placeholder domain
                )
            ],  # Required per AdCP spec
            estimated_exposures=None,  # Optional - signals products don't have exposure estimates
            delivery_measurement=DeliveryMeasurement(provider="Signals Discovery Agent"),  # Required in adcp 2.5.0
            product_card=None,  # Optional - new field from product details
            product_card_detailed=None,  # Optional - new field from product details
            placements=None,  # Optional - new field from product details
            reporting_capabilities=None,  # Optional - new field from product details
            pricing_options=[
                CpmAuctionPricingOption(
                    pricing_option_id="cpm_usd_auction",
                    pricing_model="cpm",  # Required literal for discriminated union
                    is_fixed=False,  # Required literal for CpmAuctionPricingOption
                    currency="USD",
                    price_guidance=PriceGuidance(
                        floor=float(adjusted_price),
                        p25=None,  # Optional percentile
                        p50=float(adjusted_price) * 1.2,
                        p75=float(adjusted_price) * 1.5,
                        p90=float(adjusted_price) * 1.8,
                    ),
                    min_spend_per_package=100.0,
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
                    # Use proper conversion function to ensure all required fields are present
                    # (delivery_measurement, pricing_options, publisher_properties, etc.)
                    try:
                        product = convert_product_model_to_schema(db_product)
                        products.append(product)
                    except ValueError as convert_error:
                        # Log conversion errors but continue with other products
                        logger.warning(f"Skipping product {db_product.product_id}: {convert_error}")

        except Exception as e:
            logger.error(f"Error fetching database products: {e}")

        return products  # type: ignore[return-value]  # Library Product is compatible with our extended Product
