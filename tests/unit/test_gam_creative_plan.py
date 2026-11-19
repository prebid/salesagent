"""Vectors for ``plan_gam_creative`` that a create_media_buy scenario cannot reach.

local-gam-creative-kinds.feature grades every creative kind, the size rule and both buyer-facing
refusals through the GAM stand-in. Two inputs never arrive from the public surface: a line item
with no creative placeholder (the seller's own configuration always seeds one), and a creative
stating both fixed sizes and bounds (``formats/canonical/display_tag.json`` makes the size modes
mutually exclusive, so the reader never produces both).
"""

from src.adapters.gam.creative_plan import GamCreativePlan, GamCreativeRefusal, GamRefusalReason, plan_gam_creative
from src.core.creative_to_traffic import CreativeToTraffic, TagUrlRepresentation, TrafficSize, TrafficSizeRange
from src.core.exceptions import AdCPConfigurationError


def _tag(**sizing: object) -> CreativeToTraffic:
    return CreativeToTraffic(
        creative_id="cr-tag",
        format_kind="display_tag",
        representation=TagUrlRepresentation(url="https://ads.example.com/tag.js"),
        **sizing,
    )


def test_a_line_item_with_no_placeholder_is_the_sellers_configuration_error():
    refusal = plan_gam_creative(_tag(), ())

    assert isinstance(refusal, GamCreativeRefusal)
    assert refusal.reason is GamRefusalReason.NO_PLACEHOLDER
    assert isinstance(refusal.error(), AdCPConfigurationError)


def test_declared_sizes_and_bounds_together_take_their_union_declared_sizes_first():
    ctt = _tag(
        sizes=(TrafficSize(width=300, height=250),),
        size_ranges=(TrafficSizeRange(min_width=700, max_height=90),),
    )
    placeholders = (TrafficSize(width=728, height=90), TrafficSize(width=300, height=250))

    plan = plan_gam_creative(ctt, placeholders)

    assert isinstance(plan, GamCreativePlan)
    assert (plan.creative["size"], plan.lica_sizes) == ({"width": 300, "height": 250}, ({"width": 728, "height": 90},))
