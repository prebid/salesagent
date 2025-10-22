#!/usr/bin/env python3
"""List all custom domains that may need registration with Approximated.

This script finds all tenants with custom domains (virtual_host field)
and displays them so you can check/register them via the Admin UI.

Usage:
    python scripts/list_custom_domains.py
"""

import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def list_custom_domains():
    """List all existing tenant custom domains."""
    logger.info("🔍 Finding tenants with custom domains...\n")

    with get_db_session() as session:
        stmt = select(Tenant).where(Tenant.virtual_host.isnot(None))
        tenants_with_domains = session.scalars(stmt).all()

        if not tenants_with_domains:
            logger.info("✅ No tenants with custom domains found")
            return

        logger.info(f"📋 Found {len(tenants_with_domains)} tenant(s) with custom domains:\n")

        for tenant in tenants_with_domains:
            domain = tenant.virtual_host
            logger.info(f"  🌐 {domain}")
            logger.info(f"     Tenant: {tenant.name} ({tenant.tenant_id})")
            logger.info(f"     Admin UI: /admin/tenant/{tenant.tenant_id}/settings?section=general")
            logger.info("")

        logger.info("\n💡 To check/register these domains:")
        logger.info("   1. Go to Admin UI → Tenant Settings → General")
        logger.info("   2. Click 'Check Status' to see if domain is registered")
        logger.info("   3. Click 'Register Domain' if needed")
        logger.info("   4. Wait 1-2 minutes for TLS certificate to be issued")


if __name__ == "__main__":
    list_custom_domains()
