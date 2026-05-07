"""Tenant status checking utilities.

Used to determine if a tenant is ready to accept agent requests.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)


def is_tenant_ad_server_configured(tenant_id: str) -> bool:
    """
    Check if tenant has a properly configured ad server.

    A tenant is considered configured if:
    - For GAM: OAuth token OR service account credentials exist
    - For Mock: Always configured
    - For Triton: API key exists
    - For others: Adapter config exists

    Args:
        tenant_id: Tenant ID to check

    Returns:
        True if ad server is configured and ready, False otherwise
    """
    try:
        with get_db_session() as session:
            # Eager load adapter_config to avoid N+1 query
            stmt = (
                select(Tenant).options(joinedload(Tenant.adapter_config)).filter_by(tenant_id=tenant_id, is_active=True)
            )
            tenant = session.scalars(stmt).first()

            if not tenant:
                logger.warning(f"Tenant {tenant_id} not found or inactive")
                return False

            # Check if adapter config exists
            adapter_config = tenant.adapter_config
            if not adapter_config or not adapter_config.adapter_type:
                logger.info(f"Tenant {tenant_id} has no adapter configuration")
                return False

            # Check adapter-specific requirements
            adapter_type = adapter_config.adapter_type

            if adapter_type == "google_ad_manager":
                # GAM requires either OAuth refresh token OR service account credentials
                has_oauth = bool(adapter_config.gam_refresh_token)
                # Check encrypted field directly (more efficient than decrypting just to check existence)
                has_service_account = bool(
                    adapter_config._gam_service_account_json and adapter_config.gam_service_account_email
                )
                has_auth = has_oauth or has_service_account
                if not has_auth:
                    logger.info(
                        f"Tenant {tenant_id} GAM adapter missing authentication "
                        "(needs OAuth token or service account credentials)"
                    )
                return has_auth

            elif adapter_type == "mock":
                # Mock adapter is NOT considered configured for production use
                # Users should configure a real ad server (GAM, etc.)
                return False

            elif adapter_type == "triton":
                # Triton requires API key
                has_key = bool(adapter_config.triton_api_key)
                if not has_key:
                    logger.info(f"Tenant {tenant_id} Triton adapter missing API key")
                return has_key

            else:
                # Unknown adapter type - consider it configured if it has a type
                logger.warning(f"Unknown adapter type '{adapter_type}' for tenant {tenant_id}")
                return True

    except Exception as e:
        logger.error(f"Error checking tenant {tenant_id} configuration: {e}", exc_info=True)
        return False


def get_tenant_status(tenant_id: str) -> dict:
    """
    Get detailed status information for a tenant.

    Returns:
        dict with keys:
        - exists: bool
        - is_active: bool
        - has_adapter: bool
        - adapter_type: str | None
        - is_configured: bool (ready to accept requests)
        - missing_config: list[str] (what's missing)
    """
    missing_config: list[str] = []
    status: dict[str, bool | str | list[str] | None] = {
        "exists": False,
        "is_active": False,
        "has_adapter": False,
        "adapter_type": None,
        "is_configured": False,
        "missing_config": missing_config,
    }

    try:
        with get_db_session() as session:
            # Eager load adapter_config to avoid N+1 query
            stmt = select(Tenant).options(joinedload(Tenant.adapter_config)).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()

            if not tenant:
                missing_config.append("Tenant does not exist")
                return status

            status["exists"] = True
            status["is_active"] = tenant.is_active

            if not tenant.is_active:
                missing_config.append("Tenant is not active")
                return status

            adapter_config = tenant.adapter_config
            if not adapter_config or not adapter_config.adapter_type:
                missing_config.append("No ad server selected")
                return status

            status["has_adapter"] = True
            status["adapter_type"] = adapter_config.adapter_type

            # Check adapter-specific config
            adapter_type = adapter_config.adapter_type

            if adapter_type == "google_ad_manager":
                # Check for either OAuth or service account authentication
                has_oauth = bool(adapter_config.gam_refresh_token)
                has_service_account = bool(
                    adapter_config._gam_service_account_json and adapter_config.gam_service_account_email
                )
                if not has_oauth and not has_service_account:
                    missing_config.append("GAM authentication not configured (OAuth or service account required)")
                if not adapter_config.gam_network_code:
                    missing_config.append("GAM network code not set")

            elif adapter_type == "triton":
                if not adapter_config.triton_api_key:
                    missing_config.append("Triton API key not set")

            # Mock adapter doesn't need additional config
            status["is_configured"] = len(missing_config) == 0

    except Exception as e:
        logger.error(f"Error getting tenant status for {tenant_id}: {e}", exc_info=True)
        missing_config.append(f"Error: {str(e)}")

    return status
