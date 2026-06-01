"""Admin utilities package."""

# Export decorator
from src.admin.utils.audit_decorator import log_admin_action

# Export all helper functions (previously in utils.py)
from src.admin.utils.helpers import (
    LimitedResult,
    execute_limited,
    get_custom_targeting_mappings,
    get_tenant_config_from_db,
    is_admin_production,
    is_super_admin,
    is_tenant_admin,
    parse_json_config,
    require_auth,
    require_tenant_access,
    translate_custom_targeting,
    validate_gam_network_response,
    validate_gam_user_response,
)

__all__ = [
    # Decorator
    "log_admin_action",
    # Auth/authorization functions
    "is_admin_production",
    "is_super_admin",
    "is_tenant_admin",
    "require_auth",
    "require_tenant_access",
    # Utility functions
    "parse_json_config",
    "get_tenant_config_from_db",
    "validate_gam_network_response",
    "validate_gam_user_response",
    "get_custom_targeting_mappings",
    "translate_custom_targeting",
    # Query helpers
    "LimitedResult",
    "execute_limited",
]
