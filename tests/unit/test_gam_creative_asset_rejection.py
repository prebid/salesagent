"""Repro: GAM creative-asset validation must reject as CREATIVE_REJECTED, not ValueError.

The GAM creatives manager raises bare ``ValueError`` for buyer-correctable
creative-input problems (non-URL image asset, missing video duration). These
are creative-input rejections: the pinned spec (AdCP v3.1.1
dist/schemas/3.1.1/enums/error-code.json) classifies ``CREATIVE_REJECTED`` as
``correctable`` ("revise the creative per the seller's advertising_policies"),
graded on the creative surface (compliance/3.1.1 creative scenarios,
``task: sync_creatives``) — NOT the generic-adapter SERVICE_UNAVAILABLE /
transient family a bare ValueError decays into (salesagent-z06g).

On the reaching surfaces (the three create-media-buy push paths),
``add_creative_assets`` reports per-asset partial success; its blanket
``except`` swallowed the reason entirely, so the buyer saw a bare
``status="failed"`` with ``message=None`` and nothing actionable. The typed
error must carry its message into the ``AssetStatus``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.adapters.gam.managers.creatives import GAMCreativesManager
from src.core.exceptions import AdCPCreativeRejectedError


@pytest.fixture
def manager() -> GAMCreativesManager:
    return GAMCreativesManager(client_manager=None, advertiser_id="adv-1", dry_run=True)


class TestCreativeAssetRejectionIsTyped:
    def test_image_with_non_url_asset_raises_creative_rejected(self, manager):
        """A data-URI/binary image asset is a buyer-correctable creative rejection."""
        asset = {
            "creative_id": "cr_img_1",
            "url": "data:image/png;base64,AAAA",
            "width": 300,
            "height": 250,
        }
        with pytest.raises(AdCPCreativeRejectedError, match="requires an HTTP\\(S\\) URL"):
            manager._create_hosted_asset_creative(asset)

    def test_video_missing_duration_raises_creative_rejected(self, manager):
        """A video asset without the spec-required duration is a creative rejection."""
        asset = {
            "creative_id": "cr_vid_1",
            "url": "https://cdn.example.com/spot.mp4",
            "width": 640,
            "height": 480,
        }
        with pytest.raises(AdCPCreativeRejectedError, match="missing required duration"):
            manager._create_hosted_asset_creative(asset)


class TestRejectionReasonReachesAssetStatus:
    def test_add_creative_assets_carries_rejection_message(self, manager):
        """The per-asset handler must surface the rejection reason, not drop it.

        All three reaching surfaces show ``status.message`` to the operator /
        buyer on failure; a swallowed reason leaves them with an unactionable
        bare "failed".
        """
        asset = {
            "creative_id": "cr_vid_2",
            "url": "https://cdn.example.com/spot.mp4",
            "width": 640,
            "height": 480,
            "package_assignments": [],
        }
        statuses = manager.add_creative_assets("order-1", [asset], datetime.now(UTC))

        assert len(statuses) == 1
        status = statuses[0]
        assert status.status == "failed"
        assert status.message is not None and "missing required duration" in status.message, (
            f"rejection reason dropped: message={status.message!r}"
        )
