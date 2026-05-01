"""Factory_boy factories for MediaBuy and MediaPackage models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import MediaBuy, MediaPackage
from tests.factories.core import TenantFactory
from tests.factories.principal import PrincipalFactory


class MediaBuyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = MediaBuy
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    principal = SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))

    media_buy_id = Sequence(lambda n: f"mb_{n:04d}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    order_name = LazyAttribute(lambda o: f"Order {o.media_buy_id}")
    advertiser_name = LazyAttribute(lambda o: o.principal.name)
    budget = Decimal("10000.00")
    currency = "USD"
    start_date = date(2025, 1, 1)
    end_date = date(2027, 12, 31)
    status = "pending_approval"
    raw_request = LazyAttribute(
        lambda o: {
            "packages": [{"package_id": "pkg_001", "product_id": "prod_001"}],
        }
    )


class MediaPackageFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = MediaPackage
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    media_buy = SubFactory(MediaBuyFactory)
    media_buy_id = LazyAttribute(lambda o: o.media_buy.media_buy_id)
    package_id = Sequence(lambda n: f"pkg_{n:04d}")
    budget = Decimal("5000.00")
    pacing = "even"
    package_config = LazyAttribute(
        lambda o: {
            "package_id": o.package_id,
            "product_id": "prod_001",
            "budget": float(o.budget),
        }
    )
