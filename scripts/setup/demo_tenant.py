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

from scripts.setup.seed_products import canonical_declaration
from scripts.setup.setup_tenant import print_created
from src.services.tenant_provisioning import CreatedTenant, DemoCatalogue, TenantSetup
from src.services.tenant_provisioning import provision_demo as provision_demo_tenant

DEMO_TENANT_ID = "demo"

#: The publisher the sample products sell, whole. Its adagents.json is stored without a fetch.
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


def demo_setup(virtual_host: str) -> TenantSetup:
    """The demo tenant served at *virtual_host*: id ``demo``, mock adapter, auto-approved formats."""
    return TenantSetup(
        name="Demo Sales Agent",
        virtual_host=virtual_host,
        tenant_id=DEMO_TENANT_ID,
        adapter="mock",
        auto_approve_all=True,
    )


def provision_demo(virtual_host: str) -> CreatedTenant | None:
    """Create the demo tenant served at *virtual_host*, or nothing (``None``) when any tenant exists.

    The write is :func:`src.services.tenant_provisioning.provision_demo`'s, in one transaction.
    """
    catalogue = DemoCatalogue(publisher_domain=DEMO_PUBLISHER, publisher_name="Demo Publisher", products=DEMO_PRODUCTS)
    return provision_demo_tenant(demo_setup(virtual_host), catalogue)


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

    created = provision_demo(args.virtual_host)
    if created is None:
        print("Already set up: the database holds a tenant. The demo tenant was not created.")
        return 0
    print_created(demo_setup(args.virtual_host), created)
    print(f"Sample catalogue: {len(DEMO_PRODUCTS)} products selling {DEMO_PUBLISHER}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
