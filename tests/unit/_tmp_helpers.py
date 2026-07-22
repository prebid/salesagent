"""Shared test helpers for TMP provider unit tests.

Extracted from test_tmp_providers_discovery_route.py to avoid duplicating the
UoW mock factories across the four TMP test files (CLAUDE.md DRY invariant).

Usage::

    from tests.unit._tmp_helpers import (
        _make_blueprint_uow,
        _make_sync_uow,
        _make_tenant_config_uow,
        _make_tmp_uow,
        _make_provider,
        make_super_admin_client,
    )

    # FastAPI discovery route tests:
    mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)

    # Flask admin blueprint tests:
    mock_uow_cls, mock_uow = _make_blueprint_uow()
    with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
        ...

    # Sync service tests (MediaBuyUoW + TMPProviderUoW pair):
    mock_mb_cls, mock_mb_uow, mock_tp_cls, mock_tp_uow = _make_sync_uow(
        packages=[pkg], providers=[provider]
    )

    # TenantConfigUoW tests (_resolve_seller_agent_url):
    mock_uow_cls = _make_tenant_config_uow(tenant)

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


def _make_blueprint_uow(
    tenant_id: str = "default",
    tenant_name: str = "Default Tenant",
    providers: list | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Return ``(mock_uow_cls, mock_uow)`` for Flask admin blueprint tests.

    The returned ``mock_uow_cls`` is ready to be used as the patched
    ``TMPProviderUoW`` class.  ``mock_uow`` exposes:

    - ``mock_uow.tenant_config.get_tenant()`` → a ``MagicMock`` tenant with
      ``tenant_id`` and ``name`` set.
    - ``mock_uow.tmp_providers`` → a ``MagicMock`` repository.

    Collapses the 23 copy-pasted 4-line UoW scaffold blocks in
    ``test_tmp_providers_blueprint.py`` (CLAUDE.md DRY invariant).

    Usage::

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            response = client.get(...)

        # Inspect calls:
        mock_uow.tmp_providers.create_from_fields.assert_called_once_with(...)
    """
    mock_tenant = MagicMock()
    mock_tenant.tenant_id = tenant_id
    mock_tenant.name = tenant_name

    mock_uow = MagicMock()
    mock_uow.tenant_config = MagicMock()
    mock_uow.tenant_config.get_tenant.return_value = mock_tenant
    mock_uow.tmp_providers = MagicMock()
    if providers is not None:
        mock_uow.tmp_providers.list_all.return_value = providers

    mock_uow_cls = MagicMock()
    mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_uow_cls, mock_uow


def _make_tenant_config_uow(tenant: MagicMock | None = None) -> MagicMock:
    """Return a mock ``TenantConfigUoW`` class for ``_resolve_seller_agent_url`` tests.

    The yielded UoW has ``.tenant_config.get_tenant()`` returning *tenant*.
    Collapses the 5 copy-pasted 4-line ``TenantConfigUoW`` scaffold blocks in
    ``TestResolveSellAgentUrl`` (CLAUDE.md DRY invariant).

    Usage::

        mock_uow_cls = _make_tenant_config_uow(tenant)
        with patch("src.services.tmp_provider_sync.TenantConfigUoW", mock_uow_cls):
            result = _resolve_seller_agent_url("test-tenant")
    """
    if tenant is None:
        tenant = MagicMock()
    mock_uow = MagicMock()
    mock_uow.tenant_config = MagicMock()
    mock_uow.tenant_config.get_tenant.return_value = tenant
    mock_uow_cls = MagicMock()
    mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_uow_cls


def _make_sync_uow(
    packages: list | None = None,
    providers: list | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Return ``(mock_mb_cls, mock_mb_uow, mock_tp_cls, mock_tp_uow)`` for sync service tests.

    Collapses the repeated ``MediaBuyUoW`` + ``TMPProviderUoW`` scaffold pairs
    in ``TestSyncPackagesFanOut``, ``TestSyncSessionClosedBeforeHTTP``, and
    ``TestProviderMaterializedBeforeSessionCloses`` (CLAUDE.md DRY invariant).

    - ``mock_mb_uow.media_buys.get_packages.return_value`` → *packages* (default ``[]``)
    - ``mock_tp_uow.tmp_providers.list_syncable.return_value`` → *providers* (default ``[]``)

    Usage::

        mock_mb_cls, mock_mb_uow, mock_tp_cls, mock_tp_uow = _make_sync_uow(
            packages=[pkg], providers=[provider]
        )
        with (
            patch("src.services.tmp_provider_sync.MediaBuyUoW", mock_mb_cls),
            patch("src.services.tmp_provider_sync.TMPProviderUoW", mock_tp_cls),
        ):
            sync_packages_for_media_buy("tenant-1", "mb-1")
    """
    mock_mb_uow = MagicMock()
    mock_mb_uow.media_buys = MagicMock()
    mock_mb_uow.media_buys.get_packages.return_value = packages if packages is not None else []
    mock_mb_cls = MagicMock()
    mock_mb_cls.return_value.__enter__ = MagicMock(return_value=mock_mb_uow)
    mock_mb_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_tp_uow = MagicMock()
    mock_tp_uow.tmp_providers = MagicMock()
    mock_tp_uow.tmp_providers.list_syncable.return_value = providers if providers is not None else []
    mock_tp_cls = MagicMock()
    mock_tp_cls.return_value.__enter__ = MagicMock(return_value=mock_tp_uow)
    mock_tp_cls.return_value.__exit__ = MagicMock(return_value=False)

    return mock_mb_cls, mock_mb_uow, mock_tp_cls, mock_tp_uow


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
