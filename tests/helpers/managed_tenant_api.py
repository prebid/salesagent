"""Test helpers for the sprint-1 Tenant Management API.

The Tenant Management API is gated by a key stored in the
``tenant_management_config`` table. Tests need to seed that key, and several
tests need to bind factory-boy factories to a session to create fixture data
without inline ``session.add()`` (CLAUDE.md pattern #8). Both helpers live
here to keep the call sites DRY.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.database_session import get_db_session
from src.core.database.models import TenantManagementConfig


def install_management_api_key(api_key: str) -> str:
    """Upsert ``api_key`` into ``tenant_management_config`` and return it.

    Idempotent — safe to call from multiple fixtures in the same test session.
    """
    with get_db_session() as session:
        existing = session.scalars(
            select(TenantManagementConfig).filter_by(config_key="tenant_management_api_key")
        ).first()
        if existing is None:
            session.add(
                TenantManagementConfig(
                    config_key="tenant_management_api_key",
                    config_value=api_key,
                    description="Test key",
                    updated_at=datetime.now(UTC),
                    updated_by="pytest",
                )
            )
        else:
            existing.config_value = api_key
        session.commit()
    return api_key


def make_management_api_test_client(api_key: str):
    """Return a Flask test client wired to the Tenant Management blueprint."""
    from flask import Flask

    from src.admin.tenant_management_api import tenant_management_api

    installed_key = install_management_api_key(api_key)
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(tenant_management_api)
    return application.test_client(), {"X-Tenant-Management-API-Key": installed_key}


def configure_google_ad_manager_adapter(tenant):
    """Create the standard embedded-test GAM adapter config for a tenant."""
    from tests.factories import AdapterConfigFactory

    return AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="12345",
        gam_service_account_email="sa@example.com",
        gam_auth_method="service_account",
        gam_service_account_json_plaintext='{"type":"service_account"}',
    )


@contextmanager
def bind_factories_to_session() -> Iterator[Session]:
    """Bind every factory in ``tests.factories.ALL_FACTORIES`` to a fresh session.

    Yielded session is the one factories will write to. Original session bindings
    are restored on exit so concurrent tests don't observe each other's state.

    Use cases: integration tests that need to create ORM rows from a test body
    without violating the no-inline-session-add architecture guard.
    """
    from tests.factories import ALL_FACTORIES

    saved: dict = {}
    with get_db_session() as session:
        for f in ALL_FACTORIES:
            saved[f] = (f._meta.sqlalchemy_session, f._meta.sqlalchemy_session_persistence)
            f._meta.sqlalchemy_session = session
            f._meta.sqlalchemy_session_persistence = "commit"
        try:
            yield session
        finally:
            for f, (orig_session, orig_persistence) in saved.items():
                f._meta.sqlalchemy_session = orig_session
                f._meta.sqlalchemy_session_persistence = orig_persistence
