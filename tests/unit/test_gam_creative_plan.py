"""Vectors for ``plan_gam_creative`` that a create_media_buy scenario cannot reach.

local-gam-creative-kinds.feature grades every creative kind, the size rule and both buyer-facing
refusals through the GAM stand-in. Two inputs never arrive from the public surface: a line item
with no creative placeholder (the seller's own configuration always seeds one), and a creative
stating both fixed sizes and bounds (``formats/canonical/display_tag.json`` makes the size modes
mutually exclusive, so the reader never produces both).
"""

from src.adapters.gam.creative_plan import GamCreativePlan, GamCreativeRefusal, GamRefusalReason, plan_gam_creative
from src.core.errors.codes import ErrorCode
from src.core.traffic_carrier import CreativeToTraffic, TagUrlRepresentation, TrafficSize, TrafficSizeRange


def _tag(sizes: tuple[TrafficSize, ...] = (), size_ranges: tuple[TrafficSizeRange, ...] = ()) -> CreativeToTraffic:
    """A tag creative accepting what its option accepts, as the reader builds one."""
    return CreativeToTraffic(
        creative_id="cr-tag",
        format_kind="display_tag",
        representation=TagUrlRepresentation(url="https://ads.example.com/tag.js"),
        sizes=sizes,
        size_ranges=size_ranges,
        option_sizes=sizes,
        option_size_ranges=size_ranges,
    )


def test_a_line_item_with_no_placeholder_is_the_sellers_configuration_error():
    refusal = plan_gam_creative(_tag(), ())

    assert isinstance(refusal, GamCreativeRefusal)
    assert refusal.reason is GamRefusalReason.NO_PLACEHOLDER
    problem = refusal.problem("pkg-1")
    assert (problem.code, problem.subject_type, problem.subject_id, problem.package_id) == (
        ErrorCode.CONFIGURATION_ERROR,
        "creative",
        "cr-tag",
        "pkg-1",
    )


def test_declared_sizes_and_bounds_together_take_their_union_declared_sizes_first():
    ctt = _tag(
        sizes=(TrafficSize(width=300, height=250),),
        size_ranges=(TrafficSizeRange(min_width=700, max_height=90),),
    )
    placeholders = (TrafficSize(width=728, height=90), TrafficSize(width=300, height=250))

    plan = plan_gam_creative(ctt, placeholders)

    assert isinstance(plan, GamCreativePlan)
    assert (plan.creative["size"], plan.lica_sizes) == (
        {"width": 300, "height": 250},
        ({"width": 300, "height": 250}, {"width": 728, "height": 90}),
    )
