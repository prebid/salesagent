#!/usr/bin/env python3
"""Create a local demo tenant on an empty database: mock adapter, currencies, sample catalogue.

First-run only. The command runs while the database holds no tenant at all; once any tenant
exists the deployment counts as set up, and the command creates nothing and says so. It is
therefore safe to run on every ``docker compose --profile demo up``::

    python -m scripts.setup.demo_tenant --virtual-host localhost:8000

The principal token is minted fresh and printed once. Nothing here is a fixed credential.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import func, select

from scripts.setup.seed_products import canonical_declaration, seed_product, seed_verified_website
from scripts.setup.setup_tenant import CreatedTenant, TenantSetup, add_tenant, print_created
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

DEMO_TENANT_ID = "demo"

#: The publisher the sample products sell, whole. Stored without a fetch (seed_verified_website).
DEMO_PUBLISHER = "psatest.com"

DEMO_PRODUCTS = (
    {
        "product_id": "demo_display_news",
        "name": "Premium Display - News",
        "description": "Premium news site display inventory",
        "format_options": [canonical_declaration("image", "display_300x250_image", width=300, height=250)],
        "targeting_template": {"geo_countries": ["US", "CA"]},
        "delivery_type": "guaranteed",
        "pricing": {"model": "cpm", "rate": 8.0, "is_fixed": True},
    },
    {
        "product_id": "demo_display_ros",
        "name": "Run of Site Display",
        "description": "General display inventory across all properties",
        "format_options": [canonical_declaration("image", "display_728x90_image", width=728, height=90)],
        "targeting_template": {"geo_countries": ["US", "CA"]},
        "delivery_type": "guaranteed",
        "pricing": {"model": "cpm", "rate": 2.5, "is_fixed": True},
    },
)


@dataclass(frozen=True)
class DemoTenant:
    setup: TenantSetup
    created: CreatedTenant


def provision_demo(virtual_host: str) -> DemoTenant | None:
    """Create the demo tenant served at *virtual_host*, or nothing when any tenant exists.

    Returns ``None`` when the database already holds a tenant. Otherwise creates, in one
    transaction, what :func:`scripts.setup.setup_tenant.add_tenant` creates for a mock tenant
    plus one stored publisher and the :data:`DEMO_PRODUCTS` selling it.
    """
    setup = TenantSetup(
        name="Demo Sales Agent",
        virtual_host=virtual_host,
        tenant_id=DEMO_TENANT_ID,
        adapter="mock",
        auto_approve_all=True,
    )
    with get_db_session() as session:
        if session.scalar(select(func.count()).select_from(Tenant)):
            return None
        created = add_tenant(session, setup)
        seed_verified_website(session, created.tenant_id, DEMO_PUBLISHER, name="Demo Publisher")
        for spec in DEMO_PRODUCTS:
            seed_product(session, created.tenant_id, spec)
        session.commit()
    return DemoTenant(setup=setup, created=created)


def main(argv: list[str] | None = None) -> int:
    from src.core.agent_identity import deployment_virtual_host

    parser = argparse.ArgumentParser(description="Create a local demo tenant on an empty database")
    parser.add_argument(
        "--virtual-host",
        default=deployment_virtual_host(),
        help="The host the demo tenant is served at, port included (default: the host "
        "ADCP_AGENT_URL or SALES_AGENT_DOMAIN declares, else localhost:<ADCP_SALES_PORT> "
        "outside production)",
    )
    args = parser.parse_args(argv)
    if not args.virtual_host:
        parser.error("--virtual-host is required: this production deployment declares no host")

    demo = provision_demo(args.virtual_host)
    if demo is None:
        print("Already set up: the database holds a tenant. The demo tenant was not created.")
        return 0
    print_created(demo.setup, demo.created)
    print(f"Sample catalogue: {len(DEMO_PRODUCTS)} products selling {DEMO_PUBLISHER}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
