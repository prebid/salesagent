#!/usr/bin/env python3
"""
Sync all GAM-enabled tenants via the sync API.
This script is intended to be run as a cron job.
"""

import logging
import os
import sys

import requests

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.admin.sync_api import initialize_superadmin_api_key
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Tenant

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def sync_all_gam_tenants():
    """Sync all tenants that have Google Ad Manager configured."""
    # Get API key
    api_key = initialize_superadmin_api_key()

    # Get all GAM tenants from database using ORM
    with get_db_session() as session:
        tenants = (
            session.query(Tenant, AdapterConfig)
            .join(AdapterConfig, Tenant.tenant_id == AdapterConfig.tenant_id)
            .filter(
                Tenant.ad_server == "google_ad_manager",
                Tenant.is_active,
                AdapterConfig.gam_network_code.isnot(None),
                AdapterConfig.gam_refresh_token.isnot(None),
            )
            .all()
        )

    if not tenants:
        logger.info("No GAM tenants found to sync")
        return

    logger.info(f"Found {len(tenants)} GAM tenants to sync")

    # Sync each tenant
    for tenant, _adapter_config in tenants:
        tenant_id = tenant.tenant_id
        tenant_name = tenant.name

        logger.info(f"Syncing tenant: {tenant_name} ({tenant_id})")

        try:
            # Call sync API
            response = requests.post(
                f"http://localhost:{os.environ.get('ADCP_SALES_PORT', 8080)}/api/v1/sync/trigger/{tenant_id}",
                headers={"X-API-Key": api_key},
                json={"sync_type": "full"},
                timeout=300,  # 5 minute timeout per tenant
            )

            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "completed":
                    logger.info(f"✓ Sync completed for {tenant_name}")
                    if "summary" in result:
                        summary = result["summary"]
                        logger.info(f"  - Ad units: {summary.get('ad_units', {}).get('total', 0)}")
                        logger.info(f"  - Targeting keys: {summary.get('custom_targeting', {}).get('total_keys', 0)}")
                else:
                    logger.warning(f"Sync status for {tenant_name}: {result.get('status')}")
            elif response.status_code == 409:
                logger.info(f"Sync already in progress for {tenant_name}")
            else:
                logger.error(f"Failed to sync {tenant_name}: HTTP {response.status_code}")

        except requests.exceptions.Timeout:
            logger.error(f"Sync timeout for {tenant_name}")
        except Exception as e:
            logger.error(f"Error syncing {tenant_name}: {e}")

    logger.info("Sync job completed")


if __name__ == "__main__":
    logger.info("Starting scheduled sync job")
    sync_all_gam_tenants()
