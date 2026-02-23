"""Unit tests for incremental sync behavior.

This test verifies that incremental sync does NOT mark unchanged placements as STALE.
See GitHub issue #812: Incremental Sync incorrectly marks unchanged Placements as STALE
"""

import sys
from unittest.mock import MagicMock, patch


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


def _run_sync_with_mode(sync_mode, mocks):
    """Run _run_sync_thread with the given sync_mode and mocked dependencies.

    Args:
        sync_mode: "incremental" or "full"
        mocks: Dict from _setup_mock_dependencies()
    """
    mock_tenant = MagicMock()
    mock_adapter_config = MagicMock()
    mock_adapter_config.gam_network_code = "12345"
    mock_adapter_config.gam_auth_method = "oauth"
    mock_adapter_config.gam_refresh_token = "fake-token"

    # For incremental mode, we need a previous successful sync
    mock_last_sync = MagicMock()
    mock_last_sync.started_at = MagicMock()
    mock_last_sync.started_at.tzinfo = None

    call_count = [0]

    def scalars_side_effect(stmt):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.first.return_value = mock_tenant
        elif call_count[0] == 2:
            result.first.return_value = mock_adapter_config
        elif call_count[0] == 3 and sync_mode == "incremental":
            # Last successful sync for incremental mode
            result.first.return_value = mock_last_sync
        else:
            result.first.return_value = None
        return result

    scalar_return = 0 if sync_mode == "incremental" else None
    mock_db_session = _make_mock_db_session(scalars_side_effect, scalar_return=scalar_return)

    added_modules = _ensure_googleads_mocked()
    try:
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


def test_full_sync_should_call_mark_stale():
    """Verify that full sync DOES call _mark_stale_inventory.

    Full sync fetches ALL items from GAM, so any item not in the response
    should be marked STALE (it was deleted from GAM).
    """
    mocks = _setup_mock_dependencies()
    _run_sync_with_mode("full", mocks)
    mocks["inventory_service"]._mark_stale_inventory.assert_called_once()
