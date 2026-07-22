"""Shared test helpers for TMP provider unit tests.

Extracted from test_tmp_providers_discovery_route.py to avoid duplicating the
UoW mock factories across the four TMP test files (CLAUDE.md DRY invariant).

Usage::

    from tests.unit._tmp_helpers import _make_tmp_uow, _make_provider, make_super_admin_client

    mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)
    client = make_super_admin_client()
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.database.models import TMPProvider

# Sentinel distinguishing "caller didn't pass tenant" (auto-build one) from
# "caller explicitly passed tenant=None" (simulate unknown tenant / 404 path).
_UNSET = object()


def make_super_admin_client():
    """Create a Flask test client authenticated as super admin.

    Shared by test_ssrf_url_validator.py and test_tmp_providers_blueprint.py
    to avoid duplicating the identical app-creation + session-setup block
    (CLAUDE.md DRY invariant).
    """
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["test_user"] = "test_super_admin@example.com"
        sess["test_user_role"] = "super_admin"
        sess["authenticated"] = True
    return client


def _make_provider(
    provider_id: str = "uuid-1",
    name: str = "Provider A",
    endpoint: str = "http://si-agent.localhost:3003",
    context_match: bool = True,
    identity_match: bool = True,
    countries: list[str] | None = None,
    uid_types: list[str] | None = None,
    properties: list[str] | None = None,
    timeout_ms: int = 200,
    priority: int = 0,
    status: str = "active",
) -> TMPProvider:
    """Create a real TMPProvider ORM instance (no DB session required).

    Uses the real model so that to_dict() is exercised against the production
    implementation rather than a MagicMock reimplementation that can silently
    diverge (e.g. the missing-properties regression that was caught in review).
    """
    p = TMPProvider()
    p.provider_id = provider_id
    p.name = name
    p.endpoint = endpoint
    p.context_match = context_match
    p.identity_match = identity_match
    p.countries = countries
    p.uid_types = uid_types
    p.properties = properties
    p.timeout_ms = timeout_ms
    p.priority = priority
    p.status = status
    return p


def _make_tmp_uow(providers: list[TMPProvider], tenant: MagicMock | None = _UNSET) -> MagicMock:  # type: ignore[assignment]
    """Return a mock TMPProviderUoW context manager.

    The yielded UoW has ``.tmp_providers.list_syncable()`` returning *providers*.

    ``tenant``: the discovery route (``src/routes/tmp_providers.py``) reads
    ``uow.tenant_config.get_tenant()`` from the SAME TMPProviderUoW instance —
    it collapses the tenant-existence check and the provider read into one
    transaction (Round 11 review: avoids two separate transactions and the
    DetachedInstanceError from reading provider fields after a separate
    TenantConfigUoW block already closed the session).

    Pass a tenant MagicMock to populate that path explicitly. The default
    (unset) auto-builds a generic tenant MagicMock so callers that only care
    about providers don't need to construct one. Pass ``tenant=None``
    explicitly to simulate an unknown tenant (404 path).
    """
    mock_uow = MagicMock()
    mock_uow.tmp_providers = MagicMock()
    mock_uow.tmp_providers.list_syncable.return_value = providers
    mock_uow.tenant_config = MagicMock()
    mock_uow.tenant_config.get_tenant.return_value = MagicMock() if tenant is _UNSET else tenant
    mock_uow_cls = MagicMock()
    mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_uow_cls
