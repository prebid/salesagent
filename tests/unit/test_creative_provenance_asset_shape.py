"""Regression: uc006 asset-level provenance mutation must target the SDK 5.7 list shape.

AdCP schema (v3.1-04f59d2d5, core/provenance.json): a Provenance object "attaches to
creative manifests, individual assets, or content-standards artifacts; the most-specific
provenance object replaces the inherited one entirely (no field-level merging)." In the
SDK 5.7 list shape an asset slot is a list of asset objects, so asset-level provenance
belongs at ``assets[role][0]["provenance"]``.

The INV-5 scenario (BR-RULE-094) is xfailed (production stores no asset-level
provenance), which masks a list-vs-dict TypeError in the mutation step. This test pins
the mutation against the migrated (list) shape directly.

Part of #1391 SDK 5.7 creative-asset-shape migration.
"""

from adcp.types import CreativeAsset

from tests.bdd.steps.domain.uc006_sync_creatives import given_asset_with_provenance_source_type
from tests.factories.creative_asset import make_image_assets

_FORMAT = {"id": "display_300x250", "agent_url": "http://agent.test"}


def test_asset_provenance_mutation_targets_list_element():
    """The asset-provenance Given step must set provenance on the list asset object.

    Before the fix the step does ``assets["image"]["provenance"] = ...`` which raises
    TypeError on the list shape. After the fix it indexes ``[0]`` so provenance lands
    on the individual asset object, matching the AdCP schema, and the payload stays
    parseable as a CreativeAsset.
    """
    ctx = {
        "creatives": [
            {
                "creative_id": "c",
                "name": "n",
                "format_id": _FORMAT,
                "assets": make_image_assets("image"),
            }
        ]
    }

    given_asset_with_provenance_source_type(ctx, "trained_algorithmic_media")

    asset = ctx["creatives"][-1]["assets"]["image"][0]
    assert asset["provenance"]["digital_source_type"] == "trained_algorithmic_media"

    # Valid minimal Provenance per schema -> payload still parses as SDK 5.7.
    CreativeAsset(**ctx["creatives"][-1])
