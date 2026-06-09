"""Regression: uc006 asset-level provenance attaches to the (flat) individual-slot asset.

AdCP schema (core/provenance.json): a Provenance object "attaches to creative manifests,
individual assets, or content-standards artifacts; the most-specific provenance object
replaces the inherited one entirely." The image slot here is an individual asset (a single
object per AdCP 3.1), so asset-level provenance attaches directly to that object.

The INV-5 scenario (BR-RULE-094) is xfailed (production stores no asset-level provenance),
which masks shape bugs in the mutation step. This test pins the mutation against the shape
the Given step actually builds (via the factory) and confirms the payload stays parseable.

Part of #1391 SDK 5.7 creative-asset-shape migration.
"""

from adcp.types import CreativeAsset

from tests.bdd.steps.domain.uc006_sync_creatives import given_asset_with_provenance_source_type
from tests.factories.creative_asset import make_image_asset

_FORMAT = {"id": "display_300x250", "agent_url": "http://agent.test"}


def test_asset_provenance_attaches_to_individual_asset():
    """The asset-provenance Given step sets provenance on the individual asset object.

    The image slot is a single (flat) asset object, so provenance attaches directly to it
    (no list indexing), and the payload remains parseable as a CreativeAsset.
    """
    ctx = {"creatives": [{"creative_id": "c", "name": "n", "format_id": _FORMAT, "assets": make_image_asset("image")}]}

    given_asset_with_provenance_source_type(ctx, "trained_algorithmic_media")

    asset = ctx["creatives"][-1]["assets"]["image"]
    assert asset["provenance"]["digital_source_type"] == "trained_algorithmic_media"

    # Valid minimal Provenance per schema -> payload still parses as a CreativeAsset.
    CreativeAsset(**ctx["creatives"][-1])
