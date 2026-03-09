"""Factory_boy model factories for integration tests.

All factories use ``sqlalchemy_session = None`` and are bound dynamically
by ``IntegrationEnv.__enter__()`` to a non-scoped session.

Usage::

    from tests.factories import TenantFactory, MediaBuyFactory

    # In an IntegrationEnv context (session auto-bound):
    tenant = TenantFactory(tenant_id="t1")
    buy = MediaBuyFactory(tenant=tenant, principal__tenant=tenant)
"""

from tests.factories.core import CurrencyLimitFactory, PropertyTagFactory, PublisherPartnerFactory, TenantFactory
from tests.factories.creative import CreativeAssignmentFactory, CreativeFactory
from tests.factories.media_buy import MediaBuyFactory, MediaPackageFactory
from tests.factories.metrics import FormatPerformanceMetricsFactory
from tests.factories.principal import PrincipalFactory
from tests.factories.product import PricingOptionFactory, ProductFactory
from tests.factories.webhook import PushNotificationConfigFactory

ALL_FACTORIES = [
    TenantFactory,
    CurrencyLimitFactory,
    PropertyTagFactory,
    PublisherPartnerFactory,
    PrincipalFactory,
    ProductFactory,
    PricingOptionFactory,
    MediaBuyFactory,
    MediaPackageFactory,
    PushNotificationConfigFactory,
    CreativeFactory,
    CreativeAssignmentFactory,
    FormatPerformanceMetricsFactory,
]

__all__ = [
    "ALL_FACTORIES",
    "CreativeAssignmentFactory",
    "CreativeFactory",
    "CurrencyLimitFactory",
    "FormatPerformanceMetricsFactory",
    "MediaBuyFactory",
    "MediaPackageFactory",
    "PricingOptionFactory",
    "PrincipalFactory",
    "ProductFactory",
    "PropertyTagFactory",
    "PublisherPartnerFactory",
    "PushNotificationConfigFactory",
    "TenantFactory",
]
