"""Shared helpers for embedded-mode integration tests.

Plain Python helpers — the matching pytest fixtures
(``embedded_app``, ``embedded_client``) live in
``tests/integration/conftest.py`` so test signatures don't have to
import them by name (which trips ruff's F811 redefinition check).
"""

from __future__ import annotations

import uuid

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, Tenant


def insert_embedded_test_tenant(
    *,
    is_embedded: bool,
    external_source: str | None = None,
    name_prefix: str = "t_emb",
) -> str:
    """Insert a minimal Tenant + USD CurrencyLimit and return its id.

    Bypasses the model-layer write guard via ``management_api_caller``
    (the same way ``test_managed_mode_auth_bypass.py`` and other embedded
    tests do for setup data).
    """
    tid = f"{name_prefix}_{'man' if is_embedded else 'open'}_{uuid.uuid4().hex[:8]}"
    with get_db_session() as session:
        session.info["management_api_caller"] = True
        tenant = Tenant(
            tenant_id=tid,
            name="Embedded Test Tenant",
            subdomain=tid,
            ad_server="mock",
            is_active=True,
            billing_plan="standard",
            authorized_emails=[],
            authorized_domains=[],
            auto_approve_format_ids=[],
            policy_settings={},
            is_embedded=is_embedded,
            external_source=external_source if is_embedded else None,
            external_org_id=f"org_{uuid.uuid4().hex[:8]}" if is_embedded else None,
        )
        session.add(tenant)
        session.add(CurrencyLimit(tenant_id=tid, currency_code="USD"))
        session.commit()
    return tid


def cleanup_embedded_test_tenant(tid: str) -> None:
    """Delete a tenant inserted by ``insert_embedded_test_tenant``."""
    from src.core.database.models import AdapterConfig, Principal, PropertyTag

    with get_db_session() as session:
        session.info["management_api_caller"] = True
        for model in (AdapterConfig, CurrencyLimit, PropertyTag, Principal):
            session.execute(model.__table__.delete().where(model.tenant_id == tid))
        session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()
