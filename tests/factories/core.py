"""Factory_boy factories for core tenant-related models.

Factories: TenantFactory, CurrencyLimitFactory, PropertyTagFactory, PublisherPartnerFactory
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import factory
from factory import LazyAttribute, RelatedFactory, Sequence, SubFactory

from src.core.database.models import CurrencyLimit, PropertyTag, PublisherPartner, Tenant


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
