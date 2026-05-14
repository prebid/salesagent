"""Tests for `compute_valid_actions` — the AdCP ValidAction matrix wrapper.

The wrapper delegates to ``adcp.server.helpers.valid_actions_for_status`` for
the canonical state -> actions matrix (single source of truth per DRY
invariant). These tests verify (a) the wrapper preserves the SDK matrix
intact for spec-defined statuses, (b) the legacy ``pending_activation``
local-enum value is mapped to ``pending_creatives``, (c) the
``has_pending_creatives`` hint surfaces ``sync_creatives`` for active/paused
buys when the SDK matrix did not already include it.
"""

from __future__ import annotations

import pytest
from adcp.types import MediaBuyStatus
from adcp.types.generated_poc.enums.media_buy_valid_action import MediaBuyValidAction as ValidAction

from src.core.helpers.valid_actions import compute_valid_actions


@pytest.mark.parametrize(
    "terminal_status",
    [MediaBuyStatus.canceled, MediaBuyStatus.completed, MediaBuyStatus.rejected],
)
def test_terminal_states_yield_empty_actions(terminal_status):
    """Terminal states allow no further transitions per spec."""
    assert compute_valid_actions(terminal_status, has_pending_creatives=False) == []
    assert compute_valid_actions(terminal_status, has_pending_creatives=True) == []


def test_pending_creatives_matches_sdk_matrix():
    """``pending_creatives`` per the AdCP 3.0.6+ split. SDK matrix is the
    source of truth: cancel + update_* + add_packages + sync_creatives.
    """
    actions = compute_valid_actions(MediaBuyStatus.pending_creatives, has_pending_creatives=False)
    assert {a.value for a in actions} == {
        "cancel",
        "update_budget",
        "update_dates",
        "update_packages",
        "add_packages",
        "sync_creatives",
    }


def test_pending_start_matches_sdk_matrix():
    """``pending_start`` per the AdCP 3.0.6+ split. SDK matrix is the source
    of truth: same as pending_creatives minus sync_creatives.
    """
    actions = compute_valid_actions(MediaBuyStatus.pending_start, has_pending_creatives=False)
    assert {a.value for a in actions} == {
        "cancel",
        "update_budget",
        "update_dates",
        "update_packages",
        "add_packages",
    }


def test_active_matches_sdk_matrix():
    """Active buys offer the SDK's full mid-flight surface (no sync_creatives
    by default; the SDK reserves that for pending_creatives).
    """
    no_pending = compute_valid_actions(MediaBuyStatus.active, has_pending_creatives=False)
    assert no_pending[0] == ValidAction.pause, "pause leads for active state"
    assert {a.value for a in no_pending} == {
        "pause",
        "cancel",
        "update_budget",
        "update_dates",
        "update_packages",
        "add_packages",
    }


def test_active_with_pending_appends_sync_creatives():
    """When has_pending_creatives=True, the wrapper appends sync_creatives
    even on active so buyers can still resolve outstanding creative reviews.
    """
    with_pending = compute_valid_actions(MediaBuyStatus.active, has_pending_creatives=True)
    assert ValidAction.sync_creatives in with_pending


def test_paused_matches_sdk_matrix():
    """Paused buys per the SDK matrix: resume + cancel + update_budget +
    update_dates only. (Narrower than active — no update_packages or
    add_packages since paused is meant to be a temporary halt, not a
    structural edit window.)
    """
    actions = compute_valid_actions(MediaBuyStatus.paused, has_pending_creatives=False)
    assert actions[0] == ValidAction.resume
    assert ValidAction.pause not in actions
    assert {a.value for a in actions} == {
        "resume",
        "cancel",
        "update_budget",
        "update_dates",
    }


def test_paused_with_pending_appends_sync_creatives():
    """has_pending_creatives hint also surfaces sync_creatives on paused."""
    with_pending = compute_valid_actions(MediaBuyStatus.paused, has_pending_creatives=True)
    assert ValidAction.sync_creatives in with_pending


def test_returns_list_of_enum_members_not_strings():
    """Buyers may iterate the list looking for enum identity; emitting
    strings would silently break that pattern.
    """
    actions = compute_valid_actions(MediaBuyStatus.active, has_pending_creatives=False)
    assert all(isinstance(a, ValidAction) for a in actions)
