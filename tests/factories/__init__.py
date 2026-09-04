"""Factory_boy model factories for integration tests.

All factories use ``sqlalchemy_session = None`` and are bound dynamically
by ``IntegrationEnv.__enter__()`` to a non-scoped session.

Usage::

    from tests.factories import TenantFactory, MediaBuyFactory

    # In an IntegrationEnv context (session auto-bound):
    tenant = TenantFactory(tenant_id="t1")
    buy = MediaBuyFactory(tenant=tenant, principal__tenant=tenant)
"""

from tests.factories.account import AccountFactory, AgentAccountAccessFactory
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
from tests.factories.product import PricingOptionFactory, ProductFactory
from tests.factories.targeting import (
    CollectionListReferenceFactory,
    PropertyListReferenceFactory,
    TargetingFactory,
)
from tests.factories.tmp_provider import (
    TMPProviderFactory,
    delete_tmp_providers,
    plant_seller_agent_host,
    replace_tmp_providers,
)
from tests.factories.user import TenantAuthConfigFactory, UserFactory
from tests.factories.webhook import PushNotificationConfigFactory, WebhookTaskContextFactory

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
    TMPProviderFactory,
]

__all__ = [
    "ALL_FACTORIES",
    "AccountFactory",
    "AdapterConfigFactory",
    "AuthorizedPropertyFactory",
    "AgentAccountAccessFactory",
    "CollectionListReferenceFactory",
    "CreativeAgentFactory",
    "CreativeAssetFactory",
    "CreativeAssignmentFactory",
    "CreativeFactory",
    "DeliverySimulationConfigFactory",
    "FormatFactory",
    "FormatIdFactory",
    "GetMediaBuysMediaBuyFactory",
    "InventoryProfileFactory",
    "CurrencyLimitFactory",
    "GAMInventoryFactory",
    "FormatPerformanceMetricsFactory",
    "MediaBuyFactory",
    "MediaPackageFactory",
    "PricingOptionFactory",
    "PrincipalFactory",
    "ProductFactory",
    "PropertyListReferenceFactory",
    "PropertyTagFactory",
    "PublisherPartnerFactory",
    "PushNotificationConfigFactory",
    "SignalsAgentFactory",
    "TargetingFactory",
    "TenantAuthConfigFactory",
    "TenantFactory",
    "TMPProviderFactory",
    "delete_tmp_providers",
    "plant_seller_agent_host",
    "replace_tmp_providers",
    "UserFactory",
    "WebhookTaskContextFactory",
]
