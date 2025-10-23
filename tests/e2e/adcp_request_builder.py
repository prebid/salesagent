"""
AdCP V2.3 Request Builder Helpers

Utilities for building valid AdCP-compliant requests for E2E tests.
All helpers enforce the NEW AdCP V2.3 format with proper schema validation.
"""

import uuid
from datetime import UTC, datetime
from typing import Any


def generate_buyer_ref(prefix: str = "test") -> str:
    """Generate a unique buyer reference."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def build_adcp_media_buy_request(
    product_ids: list[str],
    total_budget: float,
    start_time: str | datetime,
    end_time: str | datetime,
    promoted_offering: str = "Test Campaign Product",  # For backward compat, converted to brand_manifest
    buyer_ref: str | None = None,
    targeting_overlay: dict[str, Any] | None = None,
    currency: str = "USD",
    pacing: str = "even",
    webhook_url: str | None = None,
    brand_manifest: dict[str, Any] | str | None = None,  # AdCP spec field (preferred)
) -> dict[str, Any]:
    """
    Build a valid AdCP V2.3 create_media_buy request.

    Args:
        product_ids: List of product IDs to include
        total_budget: Total budget for the campaign
        start_time: Campaign start (ISO 8601 string or datetime)
        end_time: Campaign end (ISO 8601 string or datetime)
        promoted_offering: DEPRECATED - Use brand_manifest instead. Auto-converted if provided.
        buyer_ref: Optional buyer reference (generated if not provided)
        targeting_overlay: Optional targeting parameters
        currency: Currency code (default: USD)
        pacing: Budget pacing strategy (default: even)
        webhook_url: Optional webhook for async notifications
        brand_manifest: Brand information (name, URL, etc.) - AdCP spec field

    Returns:
        Valid AdCP V2.3 CreateMediaBuyRequest dict

    Example:
        >>> request = build_adcp_media_buy_request(
        ...     product_ids=["prod_1"],
        ...     total_budget=5000.0,
        ...     start_time="2025-10-01T00:00:00Z",
        ...     end_time="2025-10-31T23:59:59Z",
        ...     brand_manifest={"name": "Nike Air Jordan 2025 Basketball Shoes"}
        ... )
    """
    # Convert datetime to ISO 8601 string if needed
    if isinstance(start_time, datetime):
        start_time = start_time.isoformat()
    if isinstance(end_time, datetime):
        end_time = end_time.isoformat()

    # Generate buyer_ref if not provided
    if buyer_ref is None:
        buyer_ref = generate_buyer_ref()

    # Convert promoted_offering to brand_manifest if needed (backward compatibility)
    if brand_manifest is None and promoted_offering:
        brand_manifest = {"name": promoted_offering}

    # Build the request following AdCP V2.3 spec exactly
    # Note: ALL budgets are plain numbers per spec (currency from pricing_option_id)
    request: dict[str, Any] = {
        "buyer_ref": buyer_ref,
        "brand_manifest": brand_manifest,  # AdCP spec field (not promoted_offering)
        "packages": [
            {
                "buyer_ref": generate_buyer_ref("pkg"),
                "products": product_ids,
                "budget": total_budget,  # Package budget is plain number per AdCP spec
            }
        ],
        "start_time": start_time,
        "end_time": end_time,
        "budget": total_budget,  # Top-level budget is plain number per AdCP spec
    }

    # Add optional fields
    if targeting_overlay:
        request["packages"][0]["targeting_overlay"] = targeting_overlay

    if webhook_url:
        request["reporting_webhook"] = {
            "url": webhook_url,
            "reporting_frequency": "daily",
            "authentication": {"type": "none"},
        }

    return request


def build_sync_creatives_request(
    creatives: list[dict[str, Any]],
    patch: bool = False,
    dry_run: bool = False,
    webhook_url: str | None = None,
    assignments: dict[str, list[str]] | None = None,
    delete_missing: bool = False,
    validation_mode: str = "strict",
) -> dict[str, Any]:
    """
    Build a valid AdCP V2.3 sync_creatives request.

    Args:
        creatives: List of creative objects to sync
        patch: If True, only update provided fields (default: False)
        dry_run: If True, preview changes without applying (default: False)
        webhook_url: Optional webhook for async notifications
        assignments: Optional dict mapping creative_id to list of package_ids
        delete_missing: If True, delete creatives not in the sync list (default: False)
        validation_mode: Validation mode - "strict" or "lenient" (default: strict)

    Returns:
        Valid AdCP V2.3 SyncCreativesRequest dict
    """
    request: dict[str, Any] = {
        "creatives": creatives,
        "patch": patch,
        "dry_run": dry_run,
        "validation_mode": validation_mode,
        "delete_missing": delete_missing,
    }

    if assignments:
        request["assignments"] = assignments

    if webhook_url:
        request["push_notification_config"] = {
            "url": webhook_url,
            "authentication": {"type": "none"},
        }

    return request


def build_creative(
    creative_id: str,
    format_id: str,
    name: str,
    asset_url: str,
    click_through_url: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    """
    Build a valid AdCP V2.4 creative object with assets.

    Args:
        creative_id: Unique creative identifier
        format_id: Format ID (e.g., "display_300x250")
        name: Human-readable creative name
        asset_url: URL to the creative asset (converted to assets structure)
        click_through_url: Optional click-through destination
        status: Creative status (default: active)

    Returns:
        Valid AdCP V2.4 Creative dict with assets
    """
    # Build assets structure based on format type
    # For display formats, use image asset
    # For video formats, use video asset
    # Default to image for now
    assets: dict[str, Any] = {
        "primary": {
            "asset_type": "image",
            "url": asset_url,
        }
    }

    creative: dict[str, Any] = {
        "creative_id": creative_id,
        "format_id": format_id,
        "name": name,
        "assets": assets,
        "status": status,
    }

    if click_through_url:
        creative["click_through_url"] = click_through_url

    return creative


def build_update_media_buy_request(
    media_buy_id: str | None = None,
    buyer_ref: str | None = None,
    active: bool | None = None,
    budget: dict[str, Any] | None = None,
    packages: list[dict[str, Any]] | None = None,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """
    Build a valid AdCP V2.3 update_media_buy request.

    Note: Either media_buy_id OR buyer_ref must be provided (AdCP oneOf constraint).

    Args:
        media_buy_id: Media buy ID to update
        buyer_ref: Buyer reference to update (alternative to media_buy_id)
        active: Optional active status update
        budget: Optional budget update
        packages: Optional package updates
        webhook_url: Optional webhook for async notifications

    Returns:
        Valid AdCP V2.3 UpdateMediaBuyRequest dict

    Raises:
        ValueError: If neither or both media_buy_id and buyer_ref provided
    """
    if not media_buy_id and not buyer_ref:
        raise ValueError("Either media_buy_id or buyer_ref must be provided")
    if media_buy_id and buyer_ref:
        raise ValueError("Cannot provide both media_buy_id and buyer_ref (oneOf constraint)")

    request: dict[str, Any] = {}

    # Add identifier (oneOf)
    if media_buy_id:
        request["media_buy_id"] = media_buy_id
    else:
        request["buyer_ref"] = buyer_ref

    # Add optional fields
    if active is not None:
        request["active"] = active
    if budget is not None:
        request["budget"] = budget
    if packages is not None:
        request["packages"] = packages
    if webhook_url:
        request["push_notification_config"] = {
            "url": webhook_url,
            "authentication": {"type": "none"},
        }

    return request


def get_test_date_range(days_from_now: int = 1, duration_days: int = 30) -> tuple[str, str]:
    """
    Get a test-friendly date range in ISO 8601 format.

    Args:
        days_from_now: How many days in the future to start (default: 1)
        duration_days: Campaign duration in days (default: 30)

    Returns:
        Tuple of (start_time, end_time) as ISO 8601 strings
    """
    from datetime import timedelta

    now = datetime.now(UTC)
    start = now + timedelta(days=days_from_now)
    end = start + timedelta(days=duration_days)

    return (start.isoformat(), end.isoformat())
