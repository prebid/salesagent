"""Factory_boy factories for Product and PricingOption models."""

from __future__ import annotations

from decimal import Decimal

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import PricingOption, Product
from tests.factories.core import TenantFactory


class ProductFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = Product
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    product_id = Sequence(lambda n: f"prod_{n:04d}")
    name = LazyAttribute(lambda o: f"Product {o.product_id}")
    description = LazyAttribute(lambda o: f"Description for {o.name}")
    format_ids = factory.LazyFunction(
        lambda: [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]
    )
    targeting_template = factory.LazyFunction(lambda: {"geo": ["US"]})
    delivery_type = "guaranteed"
    property_tags = factory.LazyFunction(lambda: ["all_inventory"])
    delivery_measurement = factory.LazyFunction(lambda: {"provider": "publisher"})


class PricingOptionFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = PricingOption
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    product = SubFactory(ProductFactory)
    tenant_id = LazyAttribute(lambda o: o.product.tenant_id)
    product_id = LazyAttribute(lambda o: o.product.product_id)
    pricing_model = "cpm"
    rate = Decimal("5.00")
    currency = "USD"
    is_fixed = True
