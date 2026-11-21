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
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from src.core.credentials import mint_token
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, CurrencyLimit, Tenant, User
from src.core.database.repositories.principal import PrincipalRepository
from src.core.database.repositories.tenant_lookup import TenantLookupRepository

#: The currencies a new tenant can be bought in. USD is required for budget validation.
DEFAULT_CURRENCIES = ("USD", "EUR", "GBP")


@dataclass(frozen=True)
class TenantSetup:
    """What the operator asked for. Every field but ``name`` and ``virtual_host`` has a default."""

    name: str
    virtual_host: str
    tenant_id: str | None = None
    subdomain: str | None = None
    adapter: str = "mock"
    gam_network_code: str | None = None
    gam_refresh_token: str | None = None
    kevel_network_id: str | None = None
    kevel_api_key: str | None = None
    authorized_domains: tuple[str, ...] = ()
    admin_email: str | None = None
    manual_approval: bool = False
    auto_approve_all: bool = False

    @property
    def resolved_tenant_id(self) -> str:
        return self.tenant_id or self.name.lower().replace(" ", "_")

    @property
    def resolved_subdomain(self) -> str:
        return self.subdomain or self.resolved_tenant_id


@dataclass(frozen=True)
class CreatedTenant:
    """What was created. ``token`` is the only copy of the first principal's credential."""

    tenant_id: str
    subdomain: str
    principal_id: str
    token: str


class TenantAlreadyExistsError(Exception):
    """A unique key the new tenant needs (id, subdomain or virtual host) is already held."""


def _platform_mappings(setup: TenantSetup) -> dict:
    if setup.adapter == "mock":
        return {"mock": {"advertiser_id": f"mock_{setup.resolved_tenant_id}"}}
    if setup.adapter in ("google_ad_manager", "kevel"):
        # The advertiser is chosen per principal in the Admin UI.
        return {setup.adapter: {"advertiser_id": ""}}
    return {}


def _adapter_config(setup: TenantSetup) -> AdapterConfig:
    tenant_id = setup.resolved_tenant_id
    if setup.adapter == "google_ad_manager":
        config = AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            gam_network_code=setup.gam_network_code,
            gam_manual_approval_required=setup.manual_approval,
        )
        config.gam_refresh_token = setup.gam_refresh_token
        return config
    if setup.adapter == "kevel":
        return AdapterConfig(
            tenant_id=tenant_id,
            adapter_type="kevel",
            kevel_network_id=setup.kevel_network_id,
            kevel_api_key=setup.kevel_api_key,
            kevel_manual_approval_required=setup.manual_approval,
        )
    return AdapterConfig(tenant_id=tenant_id, adapter_type=setup.adapter)


def add_tenant(session, setup: TenantSetup) -> CreatedTenant:
    """Add the tenant *setup* describes to *session*, with its first principal. Does not commit.

    Creates the ``Tenant``, its ``AdapterConfig``, a ``CurrencyLimit`` per
    :data:`DEFAULT_CURRENCIES`, one ``Principal`` holding a freshly minted token and, when
    ``admin_email`` is given, one admin ``User``. Nothing else.

    Raises :class:`TenantAlreadyExistsError` before adding anything when the id, subdomain or
    virtual host is taken by any tenant, active or not.
    """
    tenant_id = setup.resolved_tenant_id
    subdomain = setup.resolved_subdomain
    lookup = TenantLookupRepository(session)
    if lookup.find_by_id_or_subdomain(tenant_id, subdomain) is not None:
        raise TenantAlreadyExistsError(f"a tenant with id {tenant_id!r} or subdomain {subdomain!r} already exists")
    if lookup.find_by_virtual_host(setup.virtual_host) is not None:
        raise TenantAlreadyExistsError(f"a tenant is already served at {setup.virtual_host!r}")

    now = datetime.now(UTC)
    admin_email = setup.admin_email.lower() if setup.admin_email else None
    session.add(
        Tenant(
            tenant_id=tenant_id,
            name=setup.name,
            subdomain=subdomain,
            virtual_host=setup.virtual_host,
            ad_server=setup.adapter,
            enable_axe_signals=True,
            auto_approve_format_ids=["display_300x250_image", "display_728x90_image"],
            human_review_required=not setup.auto_approve_all,
            policy_settings={
                "enabled": True,
                "require_manual_review": False,
                "prohibited_advertisers": [],
                "prohibited_categories": [],
                "prohibited_tactics": [],
            },
            authorized_domains=list(setup.authorized_domains) or None,
            authorized_emails=[admin_email] if admin_email else None,
            is_active=True,
            billing_plan="standard",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(_adapter_config(setup))

    if admin_email:
        session.add(
            User(
                user_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                email=admin_email,
                name=admin_email.split("@")[0].title(),
                role="admin",
                is_active=True,
                created_at=now,
            )
        )

    for currency in DEFAULT_CURRENCIES:
        session.add(
            CurrencyLimit(
                tenant_id=tenant_id,
                currency_code=currency,
                min_package_budget=0.0,
                max_daily_package_spend=100000.0,
            )
        )

    token = mint_token()
    principal_id = f"{tenant_id}_default"
    PrincipalRepository(session, tenant_id).create_with_token(
        token,
        principal_id=principal_id,
        name=f"{setup.name} Default Principal",
        platform_mappings=_platform_mappings(setup),
    )
    session.flush()
    return CreatedTenant(tenant_id=tenant_id, subdomain=subdomain, principal_id=principal_id, token=token)


def create_tenant(setup: TenantSetup) -> CreatedTenant:
    """Create the tenant *setup* describes, in one transaction. See :func:`add_tenant`."""
    with get_db_session() as session:
        created = add_tenant(session, setup)
        session.commit()
    return created


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
