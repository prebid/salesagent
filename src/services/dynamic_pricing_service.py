"""
Dynamic Pricing Service for AdCP PR #79

Calculates floor_cpm, recommended_cpm, and estimated_exposures dynamically
from cached format performance metrics.

Uses historical GAM reporting data aggregated by country + creative format.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from src.core.database.models import FormatPerformanceMetrics
from src.core.schemas import Product

logger = logging.getLogger(__name__)


class DynamicPricingService:
    """Service for calculating dynamic pricing from cached format metrics."""

    def __init__(self, db_session: Session):
        self.db = db_session

    def enrich_products_with_pricing(
        self,
        products: list[Product],
        tenant_id: str,
        country_code: str | None = None,
        min_exposures: int | None = None,
    ) -> list[Product]:
        """
        Enrich products with dynamically calculated PR #79 fields.

        Args:
            products: List of products to enrich
            tenant_id: Tenant ID for looking up metrics
            country_code: ISO country code for filtering (None = all countries)
            min_exposures: Minimum impressions needed (affects recommended_cpm)

        Returns:
            Products with populated floor_cpm, recommended_cpm, estimated_exposures
        """
        if not products:
            return products

        logger.info(
            f"Enriching {len(products)} products with dynamic pricing "
            f"(tenant={tenant_id}, country={country_code}, min_exposures={min_exposures})"
        )

        # Get recent metrics (last 30 days)
        cutoff_date = datetime.now().date() - timedelta(days=30)

        for product in products:
            try:
                pricing = self._calculate_product_pricing(product, tenant_id, country_code, min_exposures, cutoff_date)

                # Update product fields with dynamic pricing
                # Note: currency is now in pricing_options, not on Product top-level
                product.floor_cpm = pricing["floor_cpm"]
                product.recommended_cpm = pricing["recommended_cpm"]
                product.estimated_exposures = pricing["estimated_exposures"]

                logger.debug(
                    f"Product {product.product_id}: floor_cpm={pricing['floor_cpm']}, "
                    f"recommended_cpm={pricing['recommended_cpm']}, "
                    f"estimated_exposures={pricing['estimated_exposures']}"
                )

            except Exception as e:
                logger.warning(f"Failed to calculate pricing for product {product.product_id}: {e}. Using defaults.")
                # Leave defaults (floor_cpm, recommended_cpm, estimated_exposures remain None)

        return products

    def _calculate_product_pricing(
        self,
        product: Product,
        tenant_id: str,
        country_code: str | None,
        min_exposures: int | None,
        cutoff_date,
    ) -> dict:
        """Calculate pricing for a single product based on its formats."""
        # Extract creative sizes from product format IDs
        # Format IDs like "display_300x250" -> "300x250"
        creative_sizes = []
        for format_id in product.formats:
            # Handle FormatId objects (dict or object with .id attribute)
            # Pydantic validation may return dict, object, or string depending on context
            if isinstance(format_id, dict):
                format_id_str = format_id.get("id", "")
            elif hasattr(format_id, "id"):
                format_id_str = format_id.id
            else:
                format_id_str = str(format_id)

            # Extract size from format_id (e.g., "display_300x250" -> "300x250")
            parts = format_id_str.split("_")
            if len(parts) >= 2:
                # Look for dimensions pattern (NxM)
                for part in parts:
                    if "x" in part.lower():
                        creative_sizes.append(part)
                        break

        if not creative_sizes:
            logger.warning(
                f"Product {product.product_id} has no recognizable creative sizes in formats: {product.formats}"
            )
            return self._default_pricing()

        # Query format metrics for these sizes
        # GAM returns sizes with spaces (e.g., "728 x 90") but product formats use no spaces ("728x90")
        # Create normalized versions of both for matching
        normalized_sizes = [size.replace(" ", "").lower() for size in creative_sizes]

        # Query all metrics and filter with normalized comparison
        stmt = select(FormatPerformanceMetrics).where(
            and_(
                FormatPerformanceMetrics.tenant_id == tenant_id,
                FormatPerformanceMetrics.period_end >= cutoff_date,
            )
        )

        # Filter by country if specified
        if country_code:
            stmt = stmt.where(FormatPerformanceMetrics.country_code == country_code)

        all_metrics = self.db.scalars(stmt).all()

        # Filter metrics by normalized creative_size matching
        metrics = [m for m in all_metrics if m.creative_size.replace(" ", "").lower() in normalized_sizes]

        if not metrics:
            logger.debug(
                f"No cached metrics found for product {product.product_id} "
                f"(sizes={creative_sizes}, country={country_code})"
            )
            return self._default_pricing()

        # Aggregate metrics across all formats
        total_impressions = sum(m.total_impressions for m in metrics)
        weighted_median_cpm = self._calculate_weighted_avg(
            metrics, lambda m: m.median_cpm, lambda m: m.total_impressions
        )
        weighted_p75_cpm = self._calculate_weighted_avg(metrics, lambda m: m.p75_cpm, lambda m: m.total_impressions)
        weighted_p90_cpm = self._calculate_weighted_avg(metrics, lambda m: m.p90_cpm, lambda m: m.total_impressions)

        # Calculate estimated monthly impressions
        # Average daily impressions * 30 days
        period_days = (metrics[0].period_end - metrics[0].period_start).days
        if period_days > 0:
            daily_impressions = total_impressions / period_days
            estimated_monthly_impressions = int(daily_impressions * 30)
        else:
            estimated_monthly_impressions = None

        # Determine floor and recommended CPM
        floor_cpm = weighted_median_cpm  # 50th percentile as floor
        recommended_cpm = weighted_p75_cpm  # 75th percentile as standard recommendation

        # If min_exposures specified and we can't meet it, recommend higher CPM
        if min_exposures and estimated_monthly_impressions:
            if estimated_monthly_impressions < min_exposures:
                # Suggest p90 CPM to compete for more volume
                recommended_cpm = weighted_p90_cpm
                logger.debug(
                    f"Product {product.product_id}: Estimated volume ({estimated_monthly_impressions}) "
                    f"< min_exposures ({min_exposures}), recommending p90 CPM"
                )

        return {
            "currency": "USD",  # All metrics in USD
            "floor_cpm": round(floor_cpm, 2) if floor_cpm else None,
            "recommended_cpm": round(recommended_cpm, 2) if recommended_cpm else None,
            "estimated_exposures": estimated_monthly_impressions,
        }

    def _calculate_weighted_avg(self, metrics: list, value_func, weight_func) -> float | None:
        """Calculate weighted average from metrics."""
        total_weight = 0
        weighted_sum = 0

        for m in metrics:
            value = value_func(m)
            weight = weight_func(m)
            if value is not None and weight > 0:
                weighted_sum += float(value) * weight
                total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else None

    def _default_pricing(self) -> dict:
        """Return default pricing when no metrics available."""
        return {
            "currency": "USD",
            "floor_cpm": None,
            "recommended_cpm": None,
            "estimated_exposures": None,
        }
