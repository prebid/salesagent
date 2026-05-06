"""
Google Ad Manager (GAM) Adapter Modules

This package contains the modular components of the Google Ad Manager adapter:

- auth: Authentication and OAuth credential management
- client: API client initialization and management
- managers: Core business logic managers (orders, line items, creatives, targeting)
- utils: Shared utilities and helpers
"""

from .auth import GAMAuthManager
from .client import GAMClientManager
from .managers import (
    GAMCreativesManager,
    GAMOrdersManager,
    GAMTargetingManager,
)


def build_gam_config_from_adapter(adapter_config) -> dict:
    """Build GAM config dict from AdapterConfig model.

    Handles both OAuth and service account authentication methods.

    Args:
        adapter_config: AdapterConfig model instance

    Returns:
        Configuration dict suitable for GAMAuthManager/GAMClientManager
    """
    config = {
        "enabled": True,
        "network_code": adapter_config.gam_network_code,
        "trafficker_id": adapter_config.gam_trafficker_id,
        "manual_approval_required": adapter_config.gam_manual_approval_required,
    }

    # Detect auth method from credential presence rather than trusting
    # gam_auth_method alone. Embedded-mode provisioning paths can leave
    # gam_auth_method at its "oauth" server-default while populating
    # gam_service_account_json — see migration 47e05de8f5c2 for the default,
    # and src/admin/tenant_management_api.py:_persist_adapter_config for the
    # provisioning fix. Service-account JSON wins when both are present
    # (matches src/services/gam_advertisers_sync.py:_build_gam_client_for_tenant).
    sa_json = getattr(adapter_config, "gam_service_account_json", None)
    refresh_token = getattr(adapter_config, "gam_refresh_token", None)
    if sa_json:
        config["service_account_json"] = sa_json
    elif refresh_token:
        config["refresh_token"] = refresh_token

    return config


__all__ = [
    "GAMAuthManager",
    "GAMClientManager",
    "GAMCreativesManager",
    "GAMOrdersManager",
    "GAMTargetingManager",
    "build_gam_config_from_adapter",
]
