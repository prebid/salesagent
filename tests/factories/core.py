"""Factory_boy factories for core tenant-related models.

Factories: TenantFactory, CurrencyLimitFactory, PropertyTagFactory, PublisherPartnerFactory
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import factory
from factory import LazyAttribute, RelatedFactory, Sequence, SubFactory

from src.core.database.models import (
    AdapterConfig,
    CurrencyLimit,
    GamAdvertiser,
    GAMInventory,
    GAMLineItem,
    GAMOrder,
    ProductInventoryMapping,
    PropertyTag,
    PublisherPartner,
    Tenant,
)


class TenantFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Tenant
        sqlalchemy_session = None  # Bound dynamically by IntegrationEnv
        sqlalchemy_session_persistence = "commit"

    tenant_id = Sequence(lambda n: f"tenant_{n:04d}")
    name = LazyAttribute(lambda o: f"Test Publisher {o.tenant_id}")
    subdomain = LazyAttribute(lambda o: f"pub-{o.tenant_id}")
    is_active = True
    billing_plan = "standard"
    ad_server = "mock"
    # AAO setup-checklist prerequisite — seeded by default so tests don't
    # have to short-circuit ``validate_setup_complete`` via
    # ``test_session_id`` (closes #43). Tests asserting the validator
    # itself can override to None.
    public_agent_url = LazyAttribute(lambda o: f"https://{o.subdomain}.example.com/agent")
    authorized_emails = factory.LazyFunction(lambda: ["test@example.com"])
    authorized_domains = factory.LazyFunction(lambda: ["example.com"])

    @classmethod
    def make_tenant(cls, tenant_id: str = "test_tenant", **overrides: Any) -> dict[str, Any]:
        """Build a tenant dict without DB persistence.

        Uses same defaults as TenantFactory fields.
        Pass **overrides for domain fields (approval_mode, gemini_api_key, etc).
        """
        subdomain = f"pub-{tenant_id}".replace("_", "-")
        tenant: dict[str, Any] = {
            "tenant_id": tenant_id,
            "name": f"Test Publisher {tenant_id}",
            "subdomain": subdomain,
            "ad_server": "mock",
        }
        tenant.update(overrides)
        return tenant

    # Auto-create required CurrencyLimit (USD) for budget validation
    currency_usd = RelatedFactory(
        "tests.factories.core.CurrencyLimitFactory",
        factory_related_name="tenant",
    )


class CurrencyLimitFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = CurrencyLimit
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    currency_code = "USD"
    min_package_budget = Decimal("100.00")


class PublisherPartnerFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PublisherPartner
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    publisher_domain = Sequence(lambda n: f"publisher-{n:04d}.com")
    display_name = LazyAttribute(lambda o: f"Publisher {o.publisher_domain}")
    is_verified = True
    sync_status = "success"


class AdapterConfigFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = AdapterConfig
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    adapter_type = "mock"

    @classmethod
    def _create(cls, model_class: type, *args: Any, **kwargs: Any) -> Any:
        """Encrypt-and-persist GAM service-account JSON when supplied.

        The ``gam_service_account_json`` model property is an encrypted
        column with a custom setter (``encrypt_api_key``). Tests can pass
        plaintext JSON via the ``gam_service_account_json_plaintext`` kwarg
        and let the factory encrypt it at rest — instead of building, mutating,
        and re-adding the row from the test body (the test architecture rule
        documented in ``tests/CLAUDE.md``).
        """
        plaintext = kwargs.pop("gam_service_account_json_plaintext", None)
        instance = super()._create(model_class, *args, **kwargs)
        if plaintext:
            instance.gam_service_account_json = plaintext
            session = cls._meta.sqlalchemy_session
            if session is not None:
                session.commit()
        return instance


class GAMInventoryFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = GAMInventory
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    inventory_type = "ad_unit"
    inventory_id = Sequence(lambda n: f"au_{n:04d}")
    name = LazyAttribute(lambda o: f"Ad Unit {o.inventory_id}")
    path = LazyAttribute(lambda o: [o.name])
    status = "ACTIVE"
    inventory_metadata = LazyAttribute(
        lambda o: {
            "parent_id": None,
            "has_children": False,
            "ad_unit_code": f"code_{o.inventory_id}",
            "sizes": [{"width": 300, "height": 250}],
        }
    )


class PropertyTagFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PropertyTag
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    tag_id = Sequence(lambda n: f"tag_{n:04d}")
    name = LazyAttribute(lambda o: f"Tag {o.tag_id}")
    description = LazyAttribute(lambda o: f"Description for {o.name}")


class ProductInventoryMappingFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Maps a product to a GAM ad unit / placement."""

    class Meta:
        model = ProductInventoryMapping
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant_id = "test_tenant"
    product_id = "test_product"
    inventory_type = "AD_UNIT"
    inventory_id = Sequence(lambda n: f"au_{n:04d}")


class GamAdvertiserFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Sprint 5 piece D — synced GAM advertiser cache row.

    Mirrors the Sprint 5 ``gam_advertisers`` table. Tenants get a
    synced cache hydrated by ``sync_advertisers``; tests use this
    factory to seed cache rows without re-running the worker.
    """

    class Meta:
        model = GamAdvertiser
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    advertiser_id = Sequence(lambda n: str(10000 + n))
    name = LazyAttribute(lambda o: f"Advertiser {o.advertiser_id}")
    currency_code = "USD"
    status = "active"


class GAMOrderFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Synced GAM order row used by the get_media_buys projection.

    The projection module reads ``gam_orders`` for advertisers assigned
    to the calling principal and renders them as MediaBuy entries in
    the response.
    """

    class Meta:
        model = GAMOrder
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    order_id = Sequence(lambda n: f"order_{n:08d}")
    name = LazyAttribute(lambda o: f"Order {o.order_id}")
    advertiser_id = Sequence(lambda n: str(10000 + n))
    advertiser_name = LazyAttribute(lambda o: f"Advertiser {o.advertiser_id}")
    status = "APPROVED"
    total_budget = 10000.0
    currency_code = "USD"
    is_programmatic = False


class GAMLineItemFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Synced GAM line item row, child of a GAMOrder."""

    class Meta:
        model = GAMLineItem
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    line_item_id = Sequence(lambda n: f"li_{n:08d}")
    order_id = Sequence(lambda n: f"order_{n:08d}")
    name = LazyAttribute(lambda o: f"Line Item {o.line_item_id}")
    status = "DELIVERING"
    line_item_type = "STANDARD"
    cost_type = "CPM"
    cost_per_unit = 5.0
