"""Pricing helpers shared by ad server adapters."""

from __future__ import annotations

from typing import Any

from src.core.schemas import MediaPackage


def resolve_package_rate(
    package: MediaPackage,
    package_pricing_info: dict[str, dict[str, Any]] | None,
) -> float:
    """Resolve the effective rate for a package.

    Uses the validated pricing option when one was resolved for the package
    (``rate`` for fixed pricing, ``bid_price`` for auction pricing), and falls
    back to the legacy ``package.cpm`` otherwise.

    Args:
        package: The package whose rate is being resolved.
        package_pricing_info: Optional validated pricing information per package
            (AdCP PR #88), mapping package_id →
            {pricing_model, rate, currency, is_fixed, bid_price}.

    Returns:
        The rate to bill this package at.
    """
    pricing_info = package_pricing_info.get(package.package_id) if package_pricing_info else None
    if not pricing_info:
        # Fallback to legacy package.cpm
        return package.cpm
    # Use rate from pricing option (fixed) or bid_price (auction)
    if pricing_info["is_fixed"]:
        return pricing_info["rate"]
    return pricing_info.get("bid_price", package.cpm)
