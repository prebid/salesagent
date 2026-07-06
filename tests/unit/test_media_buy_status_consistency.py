"""Cross-tool media-buy status mapping consistency.

get_media_buy_delivery and get_media_buys each map the persisted
``MediaBuy.status`` column onto their wire status vocabulary. The spec
requires the two required tools to describe the same buy with the same
lifecycle status (enums/media-buy-status.json). This module pins the two
mirrored maps together so they cannot drift.
"""

from __future__ import annotations

from adcp.types import MediaBuyStatus


class TestCrossToolStatusMappingConsistency:
    """get_media_buy_delivery and get_media_buys report the same status for the same buy.

    Regression: the two tools' mirrored persisted-status maps disagreed on
    "draft" (delivery said pending_creatives, list said pending_start), so
    one buy reported two different statuses depending on which required tool
    the buyer called. The maps are mirrored rather than shared because the
    output vocabularies genuinely differ ("failed" is reportable on delivery
    responses but has no lifecycle equivalent) — this test pins the shared
    subset together so they cannot drift again.

    Spec: enums/media-buy-status.json (lifecycle vocabulary);
    get-media-buy-delivery-response.json (delivery status enum).
    """

    def test_shared_persisted_statuses_map_identically(self):
        from src.core.tools.media_buy_delivery import _PERSISTED_STATUS_TO_INTERNAL
        from src.core.tools.media_buy_list import _PERSISTED_STATUS_TO_ADCP

        assert set(_PERSISTED_STATUS_TO_INTERNAL) == set(_PERSISTED_STATUS_TO_ADCP)

        for persisted, internal in _PERSISTED_STATUS_TO_INTERNAL.items():
            if persisted == "failed":
                # Delivery may report "failed"; the lifecycle enum has no
                # equivalent so get_media_buys reports the closest terminal
                # state, "rejected".
                assert _PERSISTED_STATUS_TO_ADCP[persisted] is MediaBuyStatus.rejected
                continue
            assert _PERSISTED_STATUS_TO_ADCP[persisted].value == internal, (
                f"persisted status {persisted!r}: delivery maps to {internal!r} "
                f"but get_media_buys maps to {_PERSISTED_STATUS_TO_ADCP[persisted].value!r}"
            )

    def test_draft_is_pending_creatives(self):
        """A lingering draft buy has no creatives assigned (the lifecycle
        transitions stamp pending_creatives/pending_start as creatives land),
        which is exactly the spec description of pending_creatives."""
        from src.core.tools.media_buy_delivery import _PERSISTED_STATUS_TO_INTERNAL
        from src.core.tools.media_buy_list import _PERSISTED_STATUS_TO_ADCP

        assert _PERSISTED_STATUS_TO_INTERNAL["draft"] == "pending_creatives"
        assert _PERSISTED_STATUS_TO_ADCP["draft"] is MediaBuyStatus.pending_creatives
