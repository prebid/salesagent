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
from tests.factories.audit_log import AuditLogFactory
from tests.factories.core import (
    AdapterConfigFactory,
    CurrencyLimitFactory,
    GamAdvertiserFactory,
    GAMInventoryFactory,
    GAMLineItemFactory,
    GAMOrderFactory,
    ProductInventoryMappingFactory,
    PropertyTagFactory,
    PublisherPartnerFactory,
    TenantFactory,
)
from tests.factories.creative import CreativeAssignmentFactory, CreativeFactory
from tests.factories.creative_asset import CreativeAssetFactory
from tests.factories.format import FormatFactory, FormatIdFactory
from tests.factories.inventory_profile import InventoryProfileFactory
from tests.factories.inventory_review_state import InventoryReviewStateFactory
from tests.factories.media_buy import MediaBuyFactory, MediaPackageFactory
from tests.factories.metrics import FormatPerformanceMetricsFactory
from tests.factories.principal import PrincipalFactory
from tests.factories.product import PricingOptionFactory, ProductFactory
from tests.factories.signing import (
    TenantSigningCredentialFactory,
    TenantSigningPolicyFactory,
)
from tests.factories.sync_job import SyncJobFactory
from tests.factories.tenant_signal import TenantSignalFactory
from tests.factories.user import TenantAuthConfigFactory, UserFactory
from tests.factories.webhook import PushNotificationConfigFactory, WebhookSubscriptionFactory
from tests.factories.workflow import (
    ContextFactory,
    ObjectWorkflowMappingFactory,
    WorkflowStepFactory,
)

ALL_FACTORIES = [
    TenantFactory,
    AccountFactory,
    AgentAccountAccessFactory,
    AdapterConfigFactory,
    AuditLogFactory,
    ContextFactory,
    CurrencyLimitFactory,
    GamAdvertiserFactory,
    GAMInventoryFactory,
    GAMLineItemFactory,
    GAMOrderFactory,
    ObjectWorkflowMappingFactory,
    PropertyTagFactory,
    PublisherPartnerFactory,
    PrincipalFactory,
    InventoryProfileFactory,
    InventoryReviewStateFactory,
    ProductFactory,
    ProductInventoryMappingFactory,
    PricingOptionFactory,
    MediaBuyFactory,
    MediaPackageFactory,
    PushNotificationConfigFactory,
    WebhookSubscriptionFactory,
    CreativeFactory,
    CreativeAssignmentFactory,
    FormatPerformanceMetricsFactory,
    SyncJobFactory,
    TenantSignalFactory,
    TenantSigningCredentialFactory,
    TenantSigningPolicyFactory,
    UserFactory,
    TenantAuthConfigFactory,
    WorkflowStepFactory,
]

__all__ = [
    "ALL_FACTORIES",
    "AccountFactory",
    "AdapterConfigFactory",
    "AgentAccountAccessFactory",
    "AuditLogFactory",
    "ContextFactory",
    "CreativeAssetFactory",
    "CreativeAssignmentFactory",
    "CreativeFactory",
    "FormatFactory",
    "FormatIdFactory",
    "InventoryProfileFactory",
    "InventoryReviewStateFactory",
    "CurrencyLimitFactory",
    "GamAdvertiserFactory",
    "GAMInventoryFactory",
    "GAMLineItemFactory",
    "GAMOrderFactory",
    "FormatPerformanceMetricsFactory",
    "MediaBuyFactory",
    "MediaPackageFactory",
    "ObjectWorkflowMappingFactory",
    "PricingOptionFactory",
    "PrincipalFactory",
    "ProductFactory",
    "ProductInventoryMappingFactory",
    "PropertyTagFactory",
    "PublisherPartnerFactory",
    "PushNotificationConfigFactory",
    "SyncJobFactory",
    "TenantAuthConfigFactory",
    "TenantFactory",
    "TenantSignalFactory",
    "TenantSigningCredentialFactory",
    "TenantSigningPolicyFactory",
    "UserFactory",
    "WebhookSubscriptionFactory",
    "WorkflowStepFactory",
]
