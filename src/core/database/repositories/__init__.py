"""Repository layer for tenant-scoped database access.

Repositories encapsulate all data access logic. The tenant_id is baked into
the repository at construction time, so every query is tenant-scoped by default.

Usage:
    # Direct repository usage (when you already have a session)
    with get_db_session() as session:
        repo = MediaBuyRepository(session, tenant_id)
        media_buy = repo.get_by_id("mb_123")

    # Unit of Work (preferred — manages session lifecycle)
    with MediaBuyUoW(tenant_id) as uow:
        media_buy = uow.media_buys.get_by_id("mb_123")
        # auto-commits on clean exit, rolls back on exception
"""

from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.adapter_config import AdapterConfigRepository, TenantNotConfiguredError
from src.core.database.repositories.advertiser_mapping import (
    AdvertiserMappingRepository,
    GamAdvertiserRepository,
)
from src.core.database.repositories.audit_log import AuditLogRepository
from src.core.database.repositories.currency_limit import CurrencyLimitRepository
from src.core.database.repositories.inventory_profile import InventoryProfileRepository
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.principal import PrincipalRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.push_notification import (
    PushNotificationConfigRepository,
    PushNotificationConfigSnapshot,
)
from src.core.database.repositories.signal_usage import SignalUsage, SignalUsageRepository
from src.core.database.repositories.sync_job import SyncJobRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.tenant_signal import TenantSignalRepository
from src.core.database.repositories.tenant_signing_credentials import TenantSigningCredentialRepository
from src.core.database.repositories.tenant_signing_policy import TenantSigningPolicyRepository
from src.core.database.repositories.uow import (
    AccountUoW,
    MediaBuyUoW,
    ProductUoW,
    PushNotificationUoW,
    TenantConfigUoW,
    WorkflowUoW,
)
from src.core.database.repositories.webhook_subscription import WebhookSubscriptionRepository
from src.core.database.repositories.workflow import WorkflowRepository

__all__ = [
    "AccountRepository",
    "AccountUoW",
    "AdapterConfigRepository",
    "AdvertiserMappingRepository",
    "AuditLogRepository",
    "GamAdvertiserRepository",
    "TenantNotConfiguredError",
    "CurrencyLimitRepository",
    "InventoryProfileRepository",
    "MediaBuyRepository",
    "MediaBuyUoW",
    "PrincipalRepository",
    "ProductRepository",
    "ProductUoW",
    "PushNotificationConfigRepository",
    "PushNotificationConfigSnapshot",
    "PushNotificationUoW",
    "SignalUsage",
    "SignalUsageRepository",
    "SyncJobRepository",
    "TenantSignalRepository",
    "TenantConfigRepository",
    "TenantConfigUoW",
    "TenantSigningCredentialRepository",
    "TenantSigningPolicyRepository",
    "WebhookSubscriptionRepository",
    "WorkflowRepository",
    "WorkflowUoW",
]
