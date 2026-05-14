"""Compute the AdCP `valid_actions` list for a media buy in a given state.

Thin wrapper over ``adcp.server.helpers.valid_actions_for_status`` so the
state -> valid_actions matrix lives in exactly one place (the SDK) and not
duplicated across our codebase. The SDK helper is the canonical
``MEDIA_BUY_STATE_MACHINE`` source of truth (DRY invariant per CLAUDE.md).

Prefer ``adcp.server.helpers.valid_actions_for_status(status_str)`` directly
when you already have a status string. This wrapper exists for callers that
hold a ``MediaBuyStatus`` enum instance and want a ``list[MediaBuyValidAction]``
back, with an optional hint for surfacing ``sync_creatives`` on active/paused
buys that still have pending creative reviews.
"""

from __future__ import annotations

from adcp.server.helpers import valid_actions_for_status
from adcp.types import MediaBuyStatus
from adcp.types.generated_poc.enums.media_buy_valid_action import MediaBuyValidAction as ValidAction


def compute_valid_actions(
    status: MediaBuyStatus,
    *,
    has_pending_creatives: bool = False,
) -> list[ValidAction]:
    """Return the actions a buyer may perform on a media buy in `status`.

    Delegates to ``adcp.server.helpers.valid_actions_for_status`` for the
    canonical matrix. ``has_pending_creatives`` is an optional hint: when
    True and the status is active/paused, ``sync_creatives`` is appended if
    the SDK matrix did not already include it. The SDK matrix scopes
    ``sync_creatives`` to ``pending_creatives`` only, but a buyer with
    creatives still awaiting review benefits from the affordance regardless
    of the buy's lifecycle status.
    """
    status_str = status.value if hasattr(status, "value") else str(status)
    raw_actions = valid_actions_for_status(status_str)
    actions = [ValidAction(name) for name in raw_actions]

    if has_pending_creatives and ValidAction.sync_creatives not in actions and status_str in {"active", "paused"}:
        actions.append(ValidAction.sync_creatives)

    return actions
