"""Compute the AdCP `valid_actions` list for a media buy in a given state.

Single source of truth for the state -> valid_actions matrix. Used by every
response that exposes `valid_actions` (update_media_buy success responses and
get_media_buys per-buy entries) so the matrix lives in exactly one place.
"""

from __future__ import annotations

from adcp.types import MediaBuyStatus
from adcp.types.generated_poc.media_buy.update_media_buy_response import ValidAction


def compute_valid_actions(
    status: MediaBuyStatus,
    *,
    has_pending_creatives: bool,
) -> list[ValidAction]:
    """Return the actions a buyer may perform on a media buy in `status`.

    Spec basis: the per-state action matrix in
    `dist/schemas/3.0.6/media-buy/specification` plus `media-buy-valid-action.json`.
    Terminal states (canceled, completed, rejected) return an empty list per
    "Terminal states allow no further transitions". Pending states only allow
    cancel + sync_creatives. Active and paused expose the full mid-flight
    surface; sync_creatives is offered when at least one creative is still
    awaiting review.
    """
    if status in (MediaBuyStatus.canceled, MediaBuyStatus.completed, MediaBuyStatus.rejected):
        return []

    # Local enum lacks the 3.0.6 split into pending_creatives/pending_start;
    # treat the legacy pending_activation as "needs creatives or start" with
    # the same action set the spec defines for both pending states.
    if status == MediaBuyStatus.pending_activation:
        return [ValidAction.cancel, ValidAction.sync_creatives]

    actions: list[ValidAction] = []
    if status == MediaBuyStatus.active:
        actions.append(ValidAction.pause)
    elif status == MediaBuyStatus.paused:
        actions.append(ValidAction.resume)

    actions.extend(
        [
            ValidAction.cancel,
            ValidAction.update_budget,
            ValidAction.update_dates,
            ValidAction.update_packages,
            ValidAction.add_packages,
        ]
    )

    if has_pending_creatives:
        actions.append(ValidAction.sync_creatives)

    return actions
