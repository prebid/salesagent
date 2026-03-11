"""
Standard constants for AdCP adapter implementations.
"""

# Standardized update_media_buy actions
UPDATE_ACTIONS = {
    "pause_media_buy": "Pause the entire media buy (campaign/order)",
    "resume_media_buy": "Resume the entire media buy (campaign/order)",
    "pause_package": "Pause a specific package (flight/line item)",
    "resume_package": "Resume a specific package (flight/line item)",
    "update_package_budget": "Update the budget for a specific package",
    "update_package_impressions": "Update the impression goal for a specific package",
    "activate_order": "Activate non-guaranteed orders for delivery",
    "submit_for_approval": "Submit guaranteed orders for manual approval",
    "approve_order": "Approve orders (admin only)",
    "archive_order": "Archive completed campaigns",
}

# All adapters must support these standard actions
REQUIRED_UPDATE_ACTIONS = list(UPDATE_ACTIONS.keys())

# Map adapter short names to platform_mappings keys.
# Used by both ORM Principal and Pydantic Principal to resolve adapter-specific IDs.
ADAPTER_PLATFORM_MAP: dict[str, str] = {
    "gam": "google_ad_manager",
    "google_ad_manager": "google_ad_manager",
    "kevel": "kevel",
    "triton": "triton",
    "broadstreet": "broadstreet",
    "mock": "mock",
}

# Legacy field names for backwards-compatible advertiser ID lookup
_OLD_FIELD_MAP: dict[str, str] = {
    "gam": "gam_advertiser_id",
    "kevel": "kevel_advertiser_id",
    "triton": "triton_advertiser_id",
    "broadstreet": "broadstreet_advertiser_id",
    "mock": "mock_advertiser_id",
}


def resolve_adapter_id(platform_mappings: dict, adapter_name: str) -> str | None:
    """Resolve the adapter-specific advertiser ID from platform_mappings.

    Shared implementation for both ORM Principal.get_adapter_id()
    and Pydantic Principal.get_adapter_id().
    """
    platform_key = ADAPTER_PLATFORM_MAP.get(adapter_name)
    if not platform_key:
        return None

    platform_data = platform_mappings.get(platform_key, {})
    if isinstance(platform_data, dict):
        for field in ["advertiser_id", "id", "company_id"]:
            if field in platform_data:
                return str(platform_data[field]) if platform_data[field] else None

    # Fallback to old format for backwards compatibility
    old_field = _OLD_FIELD_MAP.get(adapter_name)
    if old_field and old_field in platform_mappings:
        return str(platform_mappings[old_field]) if platform_mappings[old_field] else None

    return None
