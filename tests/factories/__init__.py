"""Factory_boy model factories for integration tests.

All factories use ``sqlalchemy_session = None`` and are bound dynamically
by ``IntegrationEnv.__enter__()`` to a non-scoped session.

Usage::

    from tests.factories import TenantFactory, MediaBuyFactory

    # In an IntegrationEnv context (session auto-bound):
    tenant = TenantFactory(tenant_id="t1")
    buy = MediaBuyFactory(tenant=tenant, principal__tenant=tenant)
"""

from tests.factories.account import (
    AccountFactory,
    AgentAccountAccessFactory,
    BusinessEntityFactory,
)
from tests.factories.core import (
    AdapterConfigFactory,
    AuthorizedPropertyFactory,
    CreativeAgentFactory,
    CurrencyLimitFactory,
    GAMInventoryFactory,
    PropertyTagFactory,
    PublisherPartnerFactory,
    SignalsAgentFactory,
    TenantFactory,
)
from tests.factories.creative import CreativeAssignmentFactory, CreativeFactory
from tests.factories.creative_asset import CreativeAssetFactory
from tests.factories.delivery_simulation import DeliverySimulationConfigFactory
from tests.factories.format import FormatFactory, FormatIdFactory
from tests.factories.inventory_profile import InventoryProfileFactory
from tests.factories.media_buy import GetMediaBuysMediaBuyFactory, MediaBuyFactory, MediaPackageFactory
from tests.factories.metrics import FormatPerformanceMetricsFactory
from tests.factories.principal import PrincipalFactory
from tests.factories.product import PricingOptionFactory, PricingOptionRequestFactory, ProductFactory
from tests.factories.request import (
    OMIT,
    CreateMediaBuyRequestFactory,
    CreativeAssetRequestFactory,
    ListAccountsRequestFactory,
    ListCreativeFormatsRequestFactory,
    PackageRequestFactory,
    SyncAccountsRequestFactory,
    SyncCreativesRequestFactory,
)
from tests.factories.signing import SigningKeyFactory
from tests.factories.targeting import (
    CollectionListReferenceFactory,
    PropertyListReferenceFactory,
    TargetingFactory,
)
from tests.factories.user import TenantAuthConfigFactory, UserFactory
from tests.factories.webhook import PushNotificationConfigFactory, WebhookTaskContextFactory

# Only SQLAlchemyModelFactory subclasses belong here: the harness rebinds
# ``_meta.sqlalchemy_session`` across every entry. Plain ``factory.Factory``
# builders (BusinessEntityFactory, WebhookTaskContextFactory, TargetingFactory,
# CreativeAssetFactory, Format*Factory, and the ``*RequestFactory`` family that
# builds request DTOs) have no session to bind and are exported through
# ``__all__`` only.
ALL_FACTORIES = [
    TenantFactory,
    AccountFactory,
    AgentAccountAccessFactory,
    AdapterConfigFactory,
    CurrencyLimitFactory,
    GAMInventoryFactory,
    PropertyTagFactory,
    PublisherPartnerFactory,
    AuthorizedPropertyFactory,
    CreativeAgentFactory,
    SignalsAgentFactory,
    PrincipalFactory,
    InventoryProfileFactory,
    ProductFactory,
    PricingOptionFactory,
    MediaBuyFactory,
    MediaPackageFactory,
    PushNotificationConfigFactory,
    DeliverySimulationConfigFactory,
    CreativeFactory,
    CreativeAssignmentFactory,
    FormatPerformanceMetricsFactory,
    UserFactory,
    TenantAuthConfigFactory,
    SigningKeyFactory,
]

__all__ = [
    "ALL_FACTORIES",
    "OMIT",
    "AccountFactory",
    "AdapterConfigFactory",
    "AuthorizedPropertyFactory",
    "AgentAccountAccessFactory",
    "BusinessEntityFactory",
    "CollectionListReferenceFactory",
    "CreativeAgentFactory",
    "CreativeAssetFactory",
    "CreativeAssetRequestFactory",
    "CreativeAssignmentFactory",
    "CreativeFactory",
    "CreateMediaBuyRequestFactory",
    "DeliverySimulationConfigFactory",
    "FormatFactory",
    "FormatIdFactory",
    "GetMediaBuysMediaBuyFactory",
    "InventoryProfileFactory",
    "ListAccountsRequestFactory",
    "ListCreativeFormatsRequestFactory",
    "CurrencyLimitFactory",
    "GAMInventoryFactory",
    "FormatPerformanceMetricsFactory",
    "MediaBuyFactory",
    "MediaPackageFactory",
    "PackageRequestFactory",
    "PricingOptionFactory",
    "PricingOptionRequestFactory",
    "PrincipalFactory",
    "ProductFactory",
    "PropertyListReferenceFactory",
    "PropertyTagFactory",
    "PublisherPartnerFactory",
    "PushNotificationConfigFactory",
    "SignalsAgentFactory",
    "SigningKeyFactory",
    "SyncAccountsRequestFactory",
    "SyncCreativesRequestFactory",
    "TargetingFactory",
    "TenantAuthConfigFactory",
    "TenantFactory",
    "UserFactory",
    "WebhookTaskContextFactory",
]
