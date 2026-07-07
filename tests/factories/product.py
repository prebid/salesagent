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


def create_buying_mode_test_products(tenant) -> tuple[Product, Product]:
    """Create the standard two-product fixture for UC-001 buying_mode tests.

    Returns a (display_premium, video_premium) pair with CPM pricing options attached.
    Used by both the BDD step file and the cross-transport integration tests so the
    setup stays in one place.
    """
    p1 = ProductFactory(
        tenant=tenant,
        product_id="display_premium",
        name="Display Premium",
        description="Premium display inventory",
        format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
        delivery_type="guaranteed",
    )
    PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("12.0"), is_fixed=True)

    p2 = ProductFactory(
        tenant=tenant,
        product_id="video_premium",
        name="Video Premium",
        description="Premium video inventory",
        format_ids=[{"agent_url": "https://test.com", "id": "video_15s"}],
        delivery_type="guaranteed",
    )
    PricingOptionFactory(product=p2, pricing_model="cpm", rate=Decimal("18.0"), is_fixed=True)

    return p1, p2
