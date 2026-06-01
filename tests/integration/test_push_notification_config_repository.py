"""Integration tests for ``PushNotificationConfigRepository`` against real PostgreSQL.

Covers the contracts the order-approval and webhook delivery flows depend on:
the most-recent-active lookup (used by ``lookup_webhook_url``) and the
URL+principal lookup (used by ``_send_approval_webhook``). Both methods MUST
filter by ``tenant_id`` — these tests prove it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.repositories import PushNotificationConfigRepository
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _SetupEnv(IntegrationEnv):
    """Bare integration env — used only for factory-based test data setup."""

    EXTERNAL_PATCHES: dict[str, str] = {}


def _setup(setup_fn) -> None:
    """Execute ``setup_fn(env)`` inside a ``_SetupEnv`` and commit factory data."""
    with _SetupEnv() as env:
        setup_fn(env)
        env._commit_factory_data()


class TestFindMostRecentActiveForPrincipal:
    def test_returns_most_recent_active(self, integration_db):
        """The most recently created active row wins when several exist."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        older_time = datetime.now(UTC) - timedelta(hours=2)
        newer_time = datetime.now(UTC) - timedelta(minutes=5)

        def setup(env):
            tenant = TenantFactory(tenant_id="pn_recent_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="webhook_older",
                url="https://buyer.example/older",
                created_at=older_time,
                is_active=True,
            )
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="webhook_newer",
                url="https://buyer.example/newer",
                created_at=newer_time,
                is_active=True,
            )

        _setup(setup)

        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, "pn_recent_t1")
            config = repo.find_most_recent_active_for_principal("p1")

        assert config is not None
        assert config.url == "https://buyer.example/newer", (
            f"Expected most recent active webhook URL, got {config.url!r}"
        )

    def test_skips_inactive_rows(self, integration_db):
        """Inactive configs are excluded even if they are the most recent."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        older_time = datetime.now(UTC) - timedelta(hours=2)
        newer_time = datetime.now(UTC) - timedelta(minutes=5)

        def setup(env):
            tenant = TenantFactory(tenant_id="pn_inactive_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="webhook_active_old",
                url="https://buyer.example/active",
                created_at=older_time,
                is_active=True,
            )
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="webhook_inactive_new",
                url="https://buyer.example/inactive",
                created_at=newer_time,
                is_active=False,
            )

        _setup(setup)

        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, "pn_inactive_t1")
            config = repo.find_most_recent_active_for_principal("p1")

        assert config is not None
        assert config.url == "https://buyer.example/active", f"Inactive newer config leaked through; got {config.url!r}"

    def test_returns_none_when_no_config_exists(self, integration_db):
        """No config for the principal → ``None``."""
        from tests.factories import PrincipalFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="pn_empty_t1")
            PrincipalFactory(tenant=tenant, principal_id="p1")

        _setup(setup)

        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, "pn_empty_t1")
            config = repo.find_most_recent_active_for_principal("p1")

        assert config is None

    def test_does_not_leak_across_tenants(self, integration_db):
        """A different tenant's active config must not be returned."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        def setup(env):
            t1 = TenantFactory(tenant_id="pn_iso_t1")
            t2 = TenantFactory(tenant_id="pn_iso_t2")
            PrincipalFactory(tenant=t1, principal_id="p1")
            principal_t2 = PrincipalFactory(tenant=t2, principal_id="p1")
            # Tenant 2 has an active webhook for the same principal_id
            PushNotificationConfigFactory(
                tenant=t2,
                principal=principal_t2,
                id="webhook_t2",
                url="https://buyer.example/tenant2",
                is_active=True,
            )

        _setup(setup)

        with get_db_session() as db:
            repo_t1 = PushNotificationConfigRepository(db, "pn_iso_t1")
            config = repo_t1.find_most_recent_active_for_principal("p1")

        assert config is None, "Cross-tenant webhook config leaked into tenant 1 lookup"


class TestFindActiveByUrl:
    def test_returns_matching_active_config(self, integration_db):
        """Active config matching ``(principal_id, url)`` is returned."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="pn_url_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="webhook_url_match",
                url="https://buyer.example/webhook",
                authentication_type="bearer",
                authentication_token="secret-token",
                is_active=True,
            )

        _setup(setup)

        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, "pn_url_t1")
            config = repo.find_active_by_url("p1", "https://buyer.example/webhook")

        assert config is not None
        assert config.url == "https://buyer.example/webhook"
        assert config.authentication_type == "bearer"
        assert config.authentication_token == "secret-token"

    def test_returns_none_for_inactive_config(self, integration_db):
        """An inactive config matching the URL is not returned."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="pn_url_inactive_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="webhook_url_inactive",
                url="https://buyer.example/webhook",
                is_active=False,
            )

        _setup(setup)

        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, "pn_url_inactive_t1")
            config = repo.find_active_by_url("p1", "https://buyer.example/webhook")

        assert config is None, "Inactive config was returned by find_active_by_url"

    def test_returns_none_for_different_url(self, integration_db):
        """A config exists, but its URL does not match the lookup → ``None``."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="pn_url_other_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="webhook_url_other",
                url="https://buyer.example/different",
                is_active=True,
            )

        _setup(setup)

        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, "pn_url_other_t1")
            config = repo.find_active_by_url("p1", "https://buyer.example/webhook")

        assert config is None

    def test_does_not_leak_across_tenants(self, integration_db):
        """A matching URL in another tenant must not be returned."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        def setup(env):
            TenantFactory(tenant_id="pn_url_iso_t1")
            t2 = TenantFactory(tenant_id="pn_url_iso_t2")
            principal_t2 = PrincipalFactory(tenant=t2, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=t2,
                principal=principal_t2,
                id="webhook_url_t2",
                url="https://buyer.example/webhook",
                is_active=True,
            )

        _setup(setup)

        with get_db_session() as db:
            repo_t1 = PushNotificationConfigRepository(db, "pn_url_iso_t1")
            config = repo_t1.find_active_by_url("p1", "https://buyer.example/webhook")

        assert config is None, "Cross-tenant webhook config leaked into tenant 1 URL lookup"
