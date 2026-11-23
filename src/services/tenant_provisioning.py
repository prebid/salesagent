"""Create a tenant: the operator's provisioning commands' one write path (#2260).

The serving application creates no tenant. A deployment's tenants come from a super admin
in the Admin UI, from ``scripts/setup/setup_tenant.py`` (:func:`create_tenant`), or, for a
local demo on an empty database, from ``scripts/setup/demo_tenant.py`` (:func:`provision_demo`).
The commands parse arguments and print; everything they write is written here, in one
``TenantProvisioningUoW`` per tenant, so a refused run writes nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.core.credentials import mint_token
from src.core.database.models import (
    AdapterConfig,
    CurrencyLimit,
    PricingOption,
    Product,
    ProductPublisherSelection,
    Tenant,
    User,
)
from src.core.database.repositories.uow import TenantProvisioningUoW
from src.services.publisher_ingest import seed_publisher_document, website_document, website_property_id

#: The currencies a new tenant can be bought in. USD is required for budget validation.
DEFAULT_CURRENCIES = ("USD", "EUR", "GBP")

#: A product as the seeders write it: the Product's own fields plus a ``pricing`` block. A
#: plain dict because that is what the seeders' literals already are, and what
#: ``scripts/setup/seed_storyboard_tenant.py`` derives from the pinned bundle's test kit.
ProductSpec = dict[str, Any]


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


@dataclass(frozen=True)
class DemoCatalogue:
    """The publisher a demo tenant sells, whole, and the products selling it."""

    publisher_domain: str
    publisher_name: str
    products: Sequence[ProductSpec]


class TenantAlreadyExistsError(Exception):
    """A unique key the new tenant needs (id, subdomain or virtual host) is already held."""


def create_tenant(setup: TenantSetup) -> CreatedTenant:
    """Create the tenant *setup* describes, with its first principal, in one transaction.

    Creates the ``Tenant``, its ``AdapterConfig``, a ``CurrencyLimit`` per
    :data:`DEFAULT_CURRENCIES`, one ``Principal`` holding a freshly minted token and, when
    ``admin_email`` is given, one admin ``User``. Nothing else.

    Raises :class:`TenantAlreadyExistsError`, having written nothing, when the id, subdomain
    or virtual host is taken by any tenant, active or not.
    """
    with TenantProvisioningUoW(setup.resolved_tenant_id) as uow:
        return _add_tenant(uow, setup)


def provision_demo(setup: TenantSetup, catalogue: DemoCatalogue) -> CreatedTenant | None:
    """Create the demo tenant *setup* describes and its *catalogue*, or nothing when any tenant exists.

    First-run only: returns ``None``, having written nothing, when the database already holds
    a tenant. Otherwise creates what :func:`create_tenant` creates plus one stored publisher
    and the catalogue's products selling it whole, in one transaction.
    """
    with TenantProvisioningUoW(setup.resolved_tenant_id) as uow:
        assert uow.tenants is not None and uow.publishers is not None and uow.products is not None
        if uow.tenants.any_exists():
            return None
        created = _add_tenant(uow, setup)
        tenant = uow.tenants.find_by_id(created.tenant_id)
        assert tenant is not None
        agent_url = tenant.agent_url
        document = website_document(
            agent_url,
            domain=catalogue.publisher_domain,
            property_id=website_property_id(catalogue.publisher_domain),
            name=catalogue.publisher_name,
        )
        seed_publisher_document(
            uow.publishers,
            catalogue.publisher_domain,
            document,
            agent_url=agent_url,
            display_name=catalogue.publisher_name,
        )
        selectors = [{"publisher_domain": catalogue.publisher_domain, "selection_type": "all"}]
        for spec in catalogue.products:
            uow.products.create(seeded_product(created.tenant_id, spec, selectors))
        return created


def seeded_product(tenant_id: str, spec: ProductSpec, publisher_selectors: list[dict[str, str]]) -> Product:
    """The ``Product`` *spec* describes, with its one ``PricingOption``, selling *publisher_selectors*.

    A product is never one row: without the pricing option it answers ``get_products``
    with a price nobody can buy at. Every JSONB column the model does not default is set
    to ``None`` explicitly, because the table's constraints tell SQL NULL from an absent key.
    """
    pricing = spec["pricing"]
    return Product(
        tenant_id=tenant_id,
        product_id=spec["product_id"],
        name=spec["name"],
        description=spec["description"],
        format_options=spec["format_options"],
        targeting_template=spec["targeting_template"],
        delivery_type=spec["delivery_type"],
        measurement=None,
        creative_policy=None,
        price_guidance=None,
        countries=None,
        implementation_config=None,
        publisher_selections=ProductPublisherSelection.from_selectors(tenant_id, publisher_selectors),
        pricing_options=[
            PricingOption.create(
                tenant_id=tenant_id,
                product_id=spec["product_id"],
                pricing_model=pricing["model"],
                rate=pricing["rate"],
                currency=pricing.get("currency", "USD"),
                is_fixed=pricing["is_fixed"],
                price_guidance=None,  # Not used for fixed-price products
            )
        ],
    )


def _add_tenant(uow: TenantProvisioningUoW, setup: TenantSetup) -> CreatedTenant:
    assert uow.tenants is not None and uow.provisioning is not None and uow.principals is not None
    tenant_id = setup.resolved_tenant_id
    subdomain = setup.resolved_subdomain
    if uow.tenants.find_by_id_or_subdomain(tenant_id, subdomain) is not None:
        raise TenantAlreadyExistsError(f"a tenant with id {tenant_id!r} or subdomain {subdomain!r} already exists")
    if uow.tenants.find_by_virtual_host(setup.virtual_host) is not None:
        raise TenantAlreadyExistsError(f"a tenant is already served at {setup.virtual_host!r}")

    now = datetime.now(UTC)
    admin_email = setup.admin_email.lower() if setup.admin_email else None
    uow.provisioning.add_tenant(
        _tenant(setup, admin_email, now),
        _adapter_config(setup),
        [
            CurrencyLimit(
                tenant_id=tenant_id,
                currency_code=currency,
                min_package_budget=0.0,
                max_daily_package_spend=100000.0,
            )
            for currency in DEFAULT_CURRENCIES
        ],
        admin_user=_admin_user(tenant_id, admin_email, now) if admin_email else None,
    )

    token = mint_token()
    principal_id = f"{tenant_id}_default"
    uow.principals.create_with_token(
        token,
        principal_id=principal_id,
        name=f"{setup.name} Default Principal",
        platform_mappings=_platform_mappings(setup),
    )
    return CreatedTenant(tenant_id=tenant_id, subdomain=subdomain, principal_id=principal_id, token=token)


def _tenant(setup: TenantSetup, admin_email: str | None, now: datetime) -> Tenant:
    return Tenant(
        tenant_id=setup.resolved_tenant_id,
        name=setup.name,
        subdomain=setup.resolved_subdomain,
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


def _admin_user(tenant_id: str, email: str, now: datetime) -> User:
    return User(
        user_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        email=email,
        name=email.split("@")[0].title(),
        role="admin",
        is_active=True,
        created_at=now,
    )


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
