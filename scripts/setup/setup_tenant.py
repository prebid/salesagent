#!/usr/bin/env python3
"""Create a tenant: the operator's command for provisioning a seller.

The serving application creates no tenant (#2260). A deployment's tenants come from here, from
a super admin in the Admin UI, or from ``scripts/setup/demo_tenant.py`` for a local demo.

Run once per tenant, by someone with database access::

    python -m scripts.setup.setup_tenant "Acme" --virtual-host sales.acme.com --adapter mock

Running it again for a tenant id, subdomain or virtual host that is already taken refuses and
changes nothing.
"""

from __future__ import annotations

import argparse
import sys

from src.services.tenant_provisioning import (
    DEFAULT_CURRENCIES,
    CreatedTenant,
    TenantAlreadyExistsError,
    TenantSetup,
    create_tenant,
)

__all__ = ["DEFAULT_CURRENCIES", "CreatedTenant", "TenantAlreadyExistsError", "TenantSetup", "create_tenant", "main"]


def print_created(setup: TenantSetup, created: CreatedTenant) -> None:
    """Tell the operator what was created, and show the principal token, once."""
    gam_note = ""
    if setup.adapter == "google_ad_manager":
        gam_note = (
            "\n   For GAM, set the advertiser mapping in the Admin UI before this principal\n   can create media buys."
        )
    access = []
    if setup.authorized_domains:
        access.append(f"   Authorized domains: {', '.join(setup.authorized_domains)}")
    if setup.admin_email:
        access.append(f"   Admin user: {setup.admin_email}")
    access_info = "\n".join(access) if access else "   Super admins only (SUPER_ADMIN_EMAILS)"

    print(
        f"""
Tenant created.

Name:          {setup.name}
Tenant ID:     {created.tenant_id}
Virtual host:  {setup.virtual_host}
Adapter:       {setup.adapter}
Currencies:    {", ".join(DEFAULT_CURRENCIES)}

Principal token for MCP/A2A/REST (principal {created.principal_id}). It is shown once; store it:

   {created.token}

A request names this tenant by its Host ({setup.virtual_host}) or by the header
x-adcp-tenant: {created.tenant_id}{gam_note}

Access control:
{access_info}
"""
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a Sales Agent tenant")
    parser.add_argument("name", help='Tenant display name (e.g., "New York Times")')
    parser.add_argument("--tenant-id", help="Tenant ID (default: generated from name)")
    parser.add_argument("--subdomain", help="Subdomain (default: same as tenant ID)")
    parser.add_argument(
        "--virtual-host",
        required=True,
        help="The host this tenant is served at, port included when not the scheme's "
        'default (e.g. "nytimes.example.com" or "nytimes.example.com:8443"). The agent '
        "card publishes this string verbatim, and it is how a request names the tenant.",
    )
    parser.add_argument(
        "--adapter", choices=["mock", "google_ad_manager", "kevel"], default="mock", help="Primary ad server adapter"
    )
    parser.add_argument(
        "--gam-network-code",
        help="Google Ad Manager network code (optional - will be auto-detected from refresh token)",
    )
    parser.add_argument("--gam-refresh-token", help="Google Ad Manager OAuth refresh token")
    parser.add_argument("--kevel-network-id", help="Kevel network ID")
    parser.add_argument("--kevel-api-key", help="Kevel API key")
    parser.add_argument("--authorized-domain", action="append", help="Authorized domain (can be used multiple times)")
    parser.add_argument("--admin-email", help="Initial admin email address")
    parser.add_argument("--manual-approval", action="store_true", help="Require manual approval for operations")
    parser.add_argument("--auto-approve-all", action="store_true", help="Auto-approve all creative formats")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.adapter == "kevel" and not args.kevel_network_id:
        parser.error("--kevel-network-id required for Kevel")
    if args.adapter == "google_ad_manager" and not args.gam_refresh_token:
        print("Warning: no refresh token given for Google Ad Manager; configure it in the Admin UI.")

    setup = TenantSetup(
        name=args.name,
        virtual_host=args.virtual_host,
        tenant_id=args.tenant_id,
        subdomain=args.subdomain,
        adapter=args.adapter,
        gam_network_code=args.gam_network_code,
        gam_refresh_token=args.gam_refresh_token,
        kevel_network_id=args.kevel_network_id,
        kevel_api_key=args.kevel_api_key,
        authorized_domains=tuple(args.authorized_domain or ()),
        admin_email=args.admin_email,
        manual_approval=args.manual_approval,
        auto_approve_all=args.auto_approve_all,
    )
    try:
        created = create_tenant(setup)
    except TenantAlreadyExistsError as exc:
        print(f"Error: {exc}. Nothing was created.", file=sys.stderr)
        return 1
    print_created(setup, created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
