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

from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.product import ProductRepository
from src.core.database.repositories.tenant_config import TenantConfigRepository
from src.core.database.repositories.uow import MediaBuyUoW, ProductUoW, TenantConfigUoW, WorkflowUoW
from src.core.database.repositories.workflow import WorkflowRepository

__all__ = [
    "MediaBuyRepository",
    "MediaBuyUoW",
    "ProductRepository",
    "ProductUoW",
    "TenantConfigRepository",
    "TenantConfigUoW",
    "WorkflowRepository",
    "WorkflowUoW",
]
