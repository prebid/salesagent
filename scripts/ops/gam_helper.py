"""Helper functions for Google Ad Manager authentication and client creation."""

import logging

from googleads import ad_manager
from sqlalchemy import select

from src.adapters.gam.auth import GAMAuthManager
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Tenant

logger = logging.getLogger(__name__)


def get_ad_manager_client_for_tenant(tenant_id: str) -> ad_manager.AdManagerClient | None:
    """
    Get a Google Ad Manager client for a specific tenant.

    Supports both OAuth (refresh token) and service account authentication,
    determined by the tenant's adapter config (gam_auth_method field).

    
    Args:
        tenant_id: The tenant ID to get the client for

    Returns:
        An initialized Google Ad Manager client, or None if configuration is missing

    Raises:
        ValueError: If required configuration is missing
        Exception: If credential creation or token refresh fails
    """
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        tenant_name = tenant.name

        if tenant.ad_server != "google_ad_manager":
            raise ValueError(f"Tenant {tenant_id} is not configured for Google Ad Manager (using {tenant.ad_server})")

        adapter_config = db_session.scalars(
            select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
        ).first()
        if not adapter_config:
            raise ValueError(f"No adapter configuration found for tenant {tenant_id}")

        gam_network_code = adapter_config.gam_network_code
        if not gam_network_code:
            raise ValueError(f"GAM network code not configured for tenant {tenant_id}")

        auth_method = adapter_config.gam_auth_method or "oauth"

        if auth_method == "service_account":
            if not adapter_config.gam_service_account_json:
                raise ValueError(f"GAM service account JSON not configured for tenant {tenant_id}")
            auth_config: dict = {"service_account_json": adapter_config.gam_service_account_json}
        else:
            if not adapter_config.gam_refresh_token:
                raise ValueError(f"GAM refresh token not configured for tenant {tenant_id}")
            auth_config = {"refresh_token": adapter_config.gam_refresh_token}

    try:
        oauth2_client = GAMAuthManager(auth_config).get_credentials()
        client = ad_manager.AdManagerClient(
            oauth2_client, f"AdCP-Sales-Agent-{tenant_name}", network_code=gam_network_code
        )
        logger.info(f"Created GAM client for tenant {tenant_id} (auth={auth_method}, network={gam_network_code})")
        return client
    except Exception as e:
        logger.error(f"Failed to create GAM client for tenant {tenant_id}: {str(e)}")
        raise


def test_gam_connection(tenant_id: str) -> dict:
    """
    Test the GAM connection for a tenant by making a simple API call.

    Args:
        tenant_id: The tenant ID to test

    Returns:
        A dict with 'success' boolean and 'message' string
    """
    try:
        client = get_ad_manager_client_for_tenant(tenant_id)
        if not client:
            return {"success": False, "message": "Failed to create GAM client"}

        # Try to get the network information as a test
        network_service = client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()

        return {
            "success": True,
            "message": f"Successfully connected to GAM network: {network['displayName']} (ID: {network['id']})",
        }

    except Exception as e:
        return {"success": False, "message": f"GAM connection test failed: {str(e)}"}


def get_gam_network_info(tenant_id: str) -> dict:
    """
    Get GAM network information for a tenant including timezone and currency.

    Returns:
        Dictionary with network information including timezone, currency, etc.
    """
    try:
        client = get_ad_manager_client_for_tenant(tenant_id)
        if not client:
            raise ValueError(f"No GAM client available for tenant {tenant_id}")
        network_service = client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()

        # Extract network information
        network_info = {
            "network_code": network.networkCode,
            "network_id": network.id,
            "display_name": network.displayName,
            "timezone": network.timeZone,
            "currency_code": network.currencyCode,
            "effective_root_ad_unit_id": (
                network.effectiveRootAdUnitId if hasattr(network, "effectiveRootAdUnitId") else None
            ),
        }

        logger.info(f"Retrieved GAM network info for tenant {tenant_id}: {network_info}")
        return network_info

    except Exception as e:
        logger.error(f"Error getting GAM network info for tenant {tenant_id}: {e}")
        # Return defaults
        return {
            "network_code": None,
            "network_id": None,
            "display_name": None,
            "timezone": "America/New_York",  # Default fallback
            "currency_code": "USD",
            "effective_root_ad_unit_id": None,
        }


def ensure_network_timezone(tenant_id: str) -> str:
    """
    Ensure we have the network timezone, fetching and caching it if necessary.

    Returns:
        The network timezone string (fetched or cached)
    """

    # First try to get cached timezone from adapter config
    with get_db_session():
        # TODO: Add proper caching once we have a config column in adapter_config table
        # For now, always fetch from GAM
        logger.info(f"Fetching network timezone from GAM for tenant {tenant_id}...")
        try:
            network_info = get_gam_network_info(tenant_id)
            timezone = network_info.get("timezone", "America/New_York")
            logger.info(f"Got network timezone for tenant {tenant_id}: {timezone}")
            return timezone
        except Exception as e:
            logger.error(f"Error fetching network timezone for tenant {tenant_id}: {str(e)}")
            # Default to America/New_York if we can't get the timezone
            return "America/New_York"


def get_gam_config_for_tenant(tenant_id: str) -> dict | None:
    """
    Get the GAM configuration for a tenant.

    Args:
        tenant_id: The tenant ID

    Returns:
        A dict with GAM configuration, or None if not configured
    """
    with get_db_session() as db_session:
        adapter_config = db_session.scalars(
            select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
        ).first()

        if not adapter_config:
            return None

        return {
            "network_code": adapter_config.gam_network_code,
            "has_refresh_token": bool(adapter_config.gam_refresh_token),
            "auth_method": adapter_config.gam_auth_method or "oauth",
            "has_service_account": bool(adapter_config.gam_service_account_json),
            "company_id": adapter_config.advertiser_id if hasattr(adapter_config, "advertiser_id") else None,
            "trafficker_id": adapter_config.gam_trafficker_id,
            "manual_approval_required": adapter_config.gam_manual_approval_required,
        }
