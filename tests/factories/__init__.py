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
    AdvertisingPolicyFactory,
    CreativeAgentFactory,
    CurrencyLimitFactory,
    GAMInventoryFactory,
    GAMLineItemFactory,
    PropertyTagFactory,
    SignalsAgentFactory,
    SyncJobFactory,
    TenantFactory,
)
from tests.factories.creative import (
    CreativeAssignmentFactory,
    CreativeFactory,
    seeded_format_identity,
    stamp_format_identity,
)
from tests.factories.creative_asset import CreativeAssetFactory
from tests.factories.delivery_simulation import DeliverySimulationConfigFactory
from tests.factories.format import FormatFactory, FormatIdFactory
from tests.factories.inventory_profile import InventoryProfileFactory
from tests.factories.media_buy import GetMediaBuysMediaBuyFactory, MediaBuyFactory, MediaPackageFactory
from tests.factories.metrics import FormatPerformanceMetricsFactory
from tests.factories.principal import PrincipalFactory
from tests.factories.product import (
    PricingOptionFactory,
    PricingOptionRequestFactory,
    ProductFactory,
    StoredPlacementFactory,
)
from tests.factories.publisher import PublisherFactory
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
    FrequencyCapFactory,
    PropertyListReferenceFactory,
    TargetingFactory,
)
from tests.factories.user import TenantAuthConfigFactory, TenantManagementConfigFactory, UserFactory
from tests.factories.webhook import PushNotificationConfigFactory, WebhookDeliveryLogFactory, WebhookTaskContextFactory
from tests.factories.workflow import ContextFactory, WorkflowStepFactory

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
    GAMLineItemFactory,
    PropertyTagFactory,
    PublisherFactory,
    CreativeAgentFactory,
    SignalsAgentFactory,
    SyncJobFactory,
    PrincipalFactory,
    InventoryProfileFactory,
    ProductFactory,
    PricingOptionFactory,
    MediaBuyFactory,
    MediaPackageFactory,
    PushNotificationConfigFactory,
    WebhookDeliveryLogFactory,
    DeliverySimulationConfigFactory,
    CreativeFactory,
    CreativeAssignmentFactory,
    FormatPerformanceMetricsFactory,
    UserFactory,
    TenantAuthConfigFactory,
    TenantManagementConfigFactory,
    SigningKeyFactory,
    ContextFactory,
    WorkflowStepFactory,
]

__all__ = [
    "ALL_FACTORIES",
    "OMIT",
    "AccountFactory",
    "AdapterConfigFactory",
    "AdvertisingPolicyFactory",
    "AgentAccountAccessFactory",
    "BusinessEntityFactory",
    "CollectionListReferenceFactory",
    "CreativeAgentFactory",
    "CreativeAssetFactory",
    "CreativeAssetRequestFactory",
    "CreativeAssignmentFactory",
    "CreativeFactory",
    "seeded_format_identity",
    "stamp_format_identity",
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
    "GAMLineItemFactory",
    "FormatPerformanceMetricsFactory",
    "FrequencyCapFactory",
    "MediaBuyFactory",
    "MediaPackageFactory",
    "PackageRequestFactory",
    "PricingOptionFactory",
    "PricingOptionRequestFactory",
    "PrincipalFactory",
    "ProductFactory",
    "StoredPlacementFactory",
    "PropertyListReferenceFactory",
    "PropertyTagFactory",
    "PublisherFactory",
    "PushNotificationConfigFactory",
    "SignalsAgentFactory",
    "SigningKeyFactory",
    "SyncAccountsRequestFactory",
    "SyncCreativesRequestFactory",
    "SyncJobFactory",
    "TargetingFactory",
    "TenantAuthConfigFactory",
    "TenantManagementConfigFactory",
    "TenantFactory",
    "UserFactory",
    "WebhookDeliveryLogFactory",
    "WebhookTaskContextFactory",
    "ContextFactory",
    "WorkflowStepFactory",
]
