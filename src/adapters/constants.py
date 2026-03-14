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
