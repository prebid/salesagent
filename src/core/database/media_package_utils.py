"""Utilities for MediaPackage database operations.

This module provides helper functions for dual-write pattern when updating MediaPackage records.
During the transition period, we write pricing data to BOTH dedicated columns AND the JSON config.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import attributes

from src.core.database.models import MediaPackage as DBMediaPackage
from src.core.schemas import Budget


def update_media_package_fields(
    media_package: DBMediaPackage,
    budget: Budget | float | dict | None = None,
    bid_price: float | None = None,
    pacing: str | None = None,
) -> None:
    """Update MediaPackage with dual-write pattern for pricing fields.

    This function writes pricing data to BOTH:
    1. Dedicated database columns (budget, bid_price, pacing) - NEW
    2. package_config JSON (for backward compatibility) - EXISTING

    Args:
        media_package: The MediaPackage database record to update
        budget: Budget value (can be Budget object, float, dict, or None)
        bid_price: Bid price for auction pricing (float or None)
        pacing: Pacing strategy ("even", "asap", "front_loaded", etc.)

    Returns:
        None (updates media_package in-place)

    Example:
        with get_db_session() as session:
            media_package = session.query(MediaPackage).filter_by(...).first()
            update_media_package_fields(
                media_package,
                budget=1000.0,
                bid_price=5.5,
                pacing="even"
            )
            session.commit()
    """
    # Extract budget amount from various formats
    budget_value: float | None = None
    budget_currency: str | None = None
    budget_pacing: str | None = None

    if budget is not None:
        if isinstance(budget, Budget):
            # Budget object
            budget_value = budget.total
            budget_currency = budget.currency
            budget_pacing = budget.pacing
        elif isinstance(budget, dict):
            # Dict format (legacy)
            budget_value = budget.get("total")
            budget_currency = budget.get("currency", "USD")
            budget_pacing = budget.get("pacing")
        elif isinstance(budget, (int, float)):
            # Simple float/int
            budget_value = float(budget)
        else:
            # Unknown format - log warning
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Unknown budget format: {type(budget)}, treating as None")

    # 1. Write to dedicated database columns (NEW)
    if budget_value is not None:
        media_package.budget = Decimal(str(budget_value))
    else:
        media_package.budget = None

    if bid_price is not None:
        media_package.bid_price = Decimal(str(bid_price))
    else:
        media_package.bid_price = None

    # Pacing can come from function arg or Budget object
    pacing_value = pacing or budget_pacing
    if pacing_value:
        media_package.pacing = pacing_value
    else:
        media_package.pacing = None

    # 2. Write to package_config JSON (EXISTING - backward compatibility)
    if media_package.package_config is None:
        media_package.package_config = {}

    # Budget in JSON (for backward compatibility)
    if budget is not None:
        if isinstance(budget, Budget):
            # Store full Budget object structure in JSON
            media_package.package_config["budget"] = {
                "total": budget_value,
                "currency": budget_currency,
                "pacing": budget_pacing,
            }
        elif isinstance(budget, dict):
            # Already a dict, store as-is
            media_package.package_config["budget"] = budget
        else:
            # Simple float - store as float (legacy format)
            media_package.package_config["budget"] = budget_value

    # Bid price in JSON (stored in pricing_info sub-object)
    if bid_price is not None:
        if "pricing_info" not in media_package.package_config:
            media_package.package_config["pricing_info"] = {}
        media_package.package_config["pricing_info"]["bid_price"] = float(bid_price)

    # Pacing in JSON (top-level or in budget)
    if pacing_value:
        media_package.package_config["pacing"] = pacing_value

    # Mark JSON field as modified (required for SQLAlchemy JSONB updates)
    attributes.flag_modified(media_package, "package_config")


def extract_pricing_from_package_data(package_data: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    """Extract pricing fields from package data dict for dual-write.

    Args:
        package_data: Package configuration dictionary (from adapter response or request)

    Returns:
        Tuple of (budget_value, bid_price, pacing)
    """
    budget_value: float | None = None
    bid_price: float | None = None
    pacing: str | None = None

    # Extract budget
    budget = package_data.get("budget")
    if budget is not None:
        if isinstance(budget, dict):
            budget_value = budget.get("total")
            pacing = budget.get("pacing")
        elif isinstance(budget, (int, float)):
            budget_value = float(budget)

    # Extract bid_price from pricing_info
    pricing_info = package_data.get("pricing_info")
    if pricing_info and isinstance(pricing_info, dict):
        bid_price = pricing_info.get("bid_price")

    # Extract pacing (can be top-level or in budget)
    if pacing is None:
        pacing = package_data.get("pacing")

    return budget_value, bid_price, pacing
