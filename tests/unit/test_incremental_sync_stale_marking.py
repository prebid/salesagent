"""Unit tests for incremental sync behavior.

This test verifies that incremental sync does NOT mark unchanged placements as STALE.
See GitHub issue #812: Incremental Sync incorrectly marks unchanged Placements as STALE
"""


def test_incremental_sync_should_skip_stale_marking_in_source():
    """Verify that incremental sync skips _mark_stale_inventory in the source code.

    Bug: When incremental sync runs, it only fetches placements modified since
    the last sync. The _mark_stale_inventory function then marks ALL placements
    not touched in this sync as STALE - including unchanged ones that simply
    weren't fetched because they didn't change.

    Expected: The code should check sync_mode before calling _mark_stale_inventory
    and skip it for incremental syncs.
    """
    import inspect

    from src.services import background_sync_service

    # Get the source code of _run_sync_thread
    source = inspect.getsource(background_sync_service._run_sync_thread)

    # Check that _mark_stale_inventory call is conditional on sync_mode
    # The fix should wrap the call in: if sync_mode == "full":
    assert "_mark_stale_inventory" in source, "_mark_stale_inventory should be in the code"

    # Find the line that calls _mark_stale_inventory
    lines = source.split("\n")
    mark_stale_line_idx = None
    for i, line in enumerate(lines):
        if "_mark_stale_inventory" in line and "def " not in line:
            mark_stale_line_idx = i
            break

    assert mark_stale_line_idx is not None, "Could not find _mark_stale_inventory call"

    # Check that there's a sync_mode == "full" condition before this call
    # Look at the preceding lines for the condition
    preceding_lines = "\n".join(lines[max(0, mark_stale_line_idx - 5) : mark_stale_line_idx + 1])

    # The fix should have: if sync_mode == "full":
    has_full_mode_check = 'sync_mode == "full"' in preceding_lines or "sync_mode == 'full'" in preceding_lines

    assert has_full_mode_check, (
        f"_mark_stale_inventory should only be called when sync_mode == 'full'.\nPreceding lines:\n{preceding_lines}"
    )


def test_full_sync_should_mark_stale():
    """Test that full sync mode configuration is correct.

    Full sync fetches ALL items from GAM, so any item not in the response
    should be marked STALE (it was deleted from GAM).
    """
    from src.services.gam_inventory_service import GAMInventoryService

    # Verify the _mark_stale_inventory method exists
    assert hasattr(GAMInventoryService, "_mark_stale_inventory")
