"""Unit tests for incremental sync behavior.

This test verifies that incremental sync does NOT mark unchanged placements as STALE.
See GitHub issue #812: Incremental Sync incorrectly marks unchanged Placements as STALE
"""

import sys
from unittest.mock import ANY, MagicMock, patch


def _setup_mock_dependencies():
    """Set up mocks for the heavy external dependencies of _run_sync_thread.

    Returns a dict of mocks keyed by name.
    """
    mock_inventory_service = MagicMock()
    mock_discovery = MagicMock()
    mock_discovery.ad_units = []
    mock_discovery.placements = []
    mock_discovery.labels = []
    mock_discovery.custom_targeting_keys = {}
    mock_discovery.custom_targeting_values = {}
    mock_discovery.audience_segments = []
    mock_discovery.discover_ad_units.return_value = []
    mock_discovery.discover_placements.return_value = []
    mock_discovery.discover_labels.return_value = []
    mock_discovery.discover_custom_targeting.return_value = {"total_keys": 0}
    mock_discovery.discover_audience_segments.return_value = []

    return {
        "inventory_service": mock_inventory_service,
        "discovery": mock_discovery,
    }


def _make_mock_db_session(scalars_side_effect, scalar_return=None):
    """Create a mock db session context manager.

    Args:
        scalars_side_effect: Side effect function for db.scalars()
        scalar_return: Return value for db.scalar() (used in count queries)
    """
    mock_db = MagicMock()
    mock_db.scalars.side_effect = scalars_side_effect
    if scalar_return is not None:
        if isinstance(scalar_return, list):
            mock_db.scalar.side_effect = scalar_return
        else:
            mock_db.scalar.return_value = scalar_return

    mock_db_session = MagicMock()
    mock_db_session.__enter__ = MagicMock(return_value=mock_db)
    mock_db_session.__exit__ = MagicMock(return_value=False)
    return mock_db_session


def _ensure_googleads_mocked():
    """Ensure googleads and google.oauth2 modules are mocked in sys.modules.

    These are heavy external dependencies that aren't installed in the test
    environment. We add mock entries only if not already present so we don't
    clobber existing module entries.
    """
    modules_to_mock = [
        "googleads",
        "googleads.ad_manager",
        "googleads.oauth2",
        "google.oauth2.service_account",
    ]
    added = []
    for mod_name in modules_to_mock:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()
            added.append(mod_name)
    return added


def _cleanup_mocked_modules(added):
    """Remove mock modules that we added."""
    for mod_name in added:
        sys.modules.pop(mod_name, None)


def _run_sync_with_mode(sync_mode, mocks, *, adapter_config_overrides=None, current_ad_unit_count=1):
    """Run _run_sync_thread with the given sync_mode and mocked dependencies.

    Args:
        sync_mode: "incremental" or "full"
        mocks: Dict from _setup_mock_dependencies()
        adapter_config_overrides: Optional dict of attrs to override on the
            mock adapter_config (e.g. simulate a stale gam_auth_method).
        current_ad_unit_count: Cached ad-unit count returned for incremental
            fallback detection.
    """
    mock_tenant = MagicMock()
    mock_adapter_config = MagicMock()
    mock_adapter_config.adapter_type = "google_ad_manager"
    mock_adapter_config.gam_network_code = "12345"
    mock_adapter_config.gam_auth_method = "oauth"
    mock_adapter_config.gam_refresh_token = "fake-token"
    mock_adapter_config.gam_service_account_json = None
    if adapter_config_overrides:
        for k, v in adapter_config_overrides.items():
            setattr(mock_adapter_config, k, v)

    # For incremental mode, we need a previous successful sync
    mock_last_sync = MagicMock()
    mock_last_sync.started_at = MagicMock()
    mock_last_sync.started_at.tzinfo = None

    call_count = [0]

    def scalars_side_effect(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            # First call: Tenant lookup
            result.first.return_value = mock_tenant
        else:
            # All subsequent calls: return adapter_config (covers repository's
            # get_by_tenant which may be called multiple times, plus any
            # incremental sync last-sync lookup returns adapter_config harmlessly)
            result.first.return_value = mock_adapter_config
        return result

    scalar_return = [current_ad_unit_count, 0, 0, 0, 0, 0] if sync_mode == "incremental" else None
    mock_db_session = _make_mock_db_session(scalars_side_effect, scalar_return=scalar_return)

    added_modules = _ensure_googleads_mocked()
    try:
        # GAMClientManager is bypassed at the seam — the GAM client itself is
        # not under test here; we want to exercise build_gam_config_from_adapter
        # and the orchestration in _run_sync_thread without dragging in real
        # google-auth credential machinery. Patch at the canonical definition
        # (src.adapters.gam.client) so the test isn't sensitive to whether
        # background_sync_service hoists its import or keeps it local.
        mock_client_manager_cls = MagicMock()
        mock_client_manager_cls.return_value.get_client.return_value = MagicMock()
        with (
            patch(
                "src.services.background_sync_service.get_db_session",
                return_value=mock_db_session,
            ),
            patch(
                "src.adapters.gam_inventory_discovery.GAMInventoryDiscovery",
                return_value=mocks["discovery"],
            ),
            patch(
                "src.services.gam_inventory_service.GAMInventoryService",
                return_value=mocks["inventory_service"],
            ),
            patch(
                "src.adapters.gam.client.GAMClientManager",
                mock_client_manager_cls,
            ),
            patch(
                "src.adapters.gam.GAMClientManager",
                mock_client_manager_cls,
            ),
        ):
            from src.services.background_sync_service import _run_sync_thread

            _run_sync_thread(
                tenant_id="test-tenant",
                sync_id=f"sync-{sync_mode}",
                sync_mode=sync_mode,
                sync_types=None,
                custom_targeting_limit=None,
                audience_segment_limit=None,
            )
            return mock_client_manager_cls
    finally:
        _cleanup_mocked_modules(added_modules)


def test_incremental_sync_should_skip_stale_marking():
    """Verify that incremental sync does NOT call _mark_stale_inventory.

    Bug: When incremental sync runs, it only fetches placements modified since
    the last sync. The _mark_stale_inventory function then marks ALL placements
    not touched in this sync as STALE - including unchanged ones that simply
    weren't fetched because they didn't change.

    Expected: _mark_stale_inventory is NOT called during incremental syncs.
    """
    mocks = _setup_mock_dependencies()
    _run_sync_with_mode("incremental", mocks)
    mocks["inventory_service"]._mark_stale_inventory.assert_not_called()


def test_incremental_sync_with_empty_ad_unit_cache_falls_back_to_full():
    """An emptied catalog needs a full pass, not a delta from last success."""
    mocks = _setup_mock_dependencies()
    _run_sync_with_mode("incremental", mocks, current_ad_unit_count=0)
    mocks["inventory_service"]._mark_stale_inventory.assert_called_once_with("test-tenant", ANY)


def test_full_sync_should_call_mark_stale():
    """Verify that full sync DOES call _mark_stale_inventory.

    Full sync fetches ALL items from GAM, so any item not in the response
    should be marked STALE (it was deleted from GAM).
    """
    mocks = _setup_mock_dependencies()
    _run_sync_with_mode("full", mocks)
    mocks["inventory_service"]._mark_stale_inventory.assert_called_once()


def test_full_sync_should_not_delete_existing_inventory_before_discovery():
    """Full sync must not empty the catalog before the first GAM API call.

    If credentials fail or the worker dies after an up-front delete, selectors
    disappear and subsequent refreshes can remain blocked behind a stale
    running row. Full sync should upsert fresh data, then mark stale rows only
    after the discovery pass succeeds.
    """
    mocks = _setup_mock_dependencies()
    _run_sync_with_mode("full", mocks)
    mocks["inventory_service"].db.execute.assert_not_called()


def test_sync_uses_service_account_when_auth_method_is_stale_oauth():
    """Regression: when gam_auth_method='oauth' but service_account_json is
    present and refresh_token is None, the sync must still pick the
    service-account credential — not fall through to GoogleRefreshTokenClient
    with refresh_token=None (which produces 'credentials do not contain
    refresh_token, token_uri, client_id, client_secret').

    This is the embedded-mode tenant provisioning bug: the AdapterConfig
    column server_default is 'oauth', and the pre-fix
    src/admin/tenant_management_api.py:_persist_adapter_config never
    overrode it, so service-account-only tenants ended up with stale auth
    method.
    """
    mocks = _setup_mock_dependencies()
    sa_json = '{"type": "service_account", "client_email": "sa@x.iam.gserviceaccount.com"}'

    client_manager_cls = _run_sync_with_mode(
        "full",
        mocks,
        adapter_config_overrides={
            "gam_auth_method": "oauth",  # stale!
            "gam_refresh_token": None,
            "gam_service_account_json": sa_json,
        },
    )

    # The config passed to GAMClientManager must carry service_account_json,
    # not refresh_token. If the pre-fix logic were still in place, the config
    # would carry no auth at all (legacy build_gam_config gated SA on
    # gam_auth_method=='service_account') and the sync would fail before
    # constructing the client.
    assert client_manager_cls.called, "GAMClientManager was never constructed — sync failed before client build"
    config_arg = client_manager_cls.call_args[0][0]
    assert config_arg.get("service_account_json") == sa_json
    assert "refresh_token" not in config_arg
    # And the sync proceeded past the client build into actual inventory work.
    mocks["inventory_service"]._write_inventory_batch.assert_called()
