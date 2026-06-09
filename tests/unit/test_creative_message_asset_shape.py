"""Regression: uc006 generative steps must build message assets in SDK 5.7 list shape.

A bare role->dict text asset (``{"message": {"content": ...}}``) FAILS CreativeAsset
parsing — the SDK 5.7 discriminated union requires an ``asset_type`` tag and a list
value. The uc006 generative-prompt scenarios that consume these assets are currently
xfailed ("UC-006 harness not yet wired for non-account scenarios"), so the BDD run
does NOT catch the invalid shape. This unit test pins the contract directly by
invoking the real step function and parsing the payload it produces, plus covers the
role-priority extractor wrapper that the steps feed.

Part of the #1391 SDK 5.7 creative-asset-shape migration.
"""

from adcp.types import CreativeAsset

from src.core.tools.creatives._assets import _extract_message_from_assets
from tests.bdd.steps.domain.uc006_sync_creatives import given_message_asset_with_prompt
from tests.factories.creative_asset import make_text_assets

_FORMAT = {"id": "display_gen", "agent_url": "http://agent.test"}


def test_message_asset_step_builds_parseable_sdk57_shape():
    """given_message_asset_with_prompt must produce a CreativeAsset-parseable payload.

    Before the fix the step builds ``{"message": {"content": ...}}`` (bare dict),
    which raises ValidationError on CreativeAsset(**payload). After the fix it uses
    the SDK 5.7 list shape and parses, and production extracts the prompt.
    """
    ctx = {"creatives": [{"creative_id": "c1", "name": "Gen", "format_id": _FORMAT, "assets": {}}]}
    given_message_asset_with_prompt(ctx)

    creative = CreativeAsset(**ctx["creatives"][-1])
    assert _extract_message_from_assets(creative) == "Generate a banner ad for summer sale"


def test_extract_message_from_assets_reads_list_shape_roles():
    """Role-priority extractor reads SDK 5.7 list-shape message/brief/prompt roles."""
    for role in ("message", "brief", "prompt"):
        creative = CreativeAsset(
            creative_id="c",
            name="n",
            format_id=_FORMAT,
            assets=make_text_assets(role, f"prompt-{role}"),
        )
        assert _extract_message_from_assets(creative) == f"prompt-{role}"
