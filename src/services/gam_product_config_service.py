"""Service for managing GAM product implementation configurations."""

import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from adcp.canonical_formats.compat_helpers import FormatIdInput
from pydantic import ValidationError

from src.core.format_resolver import upgraded_reference
from src.core.format_satisfaction import product_declarations

logger = logging.getLogger(__name__)

#: The slot a product gets when no declaration it carries states one. ``validate_config``
#: below refuses a config with no placeholder, and this function SEEDS a config an operator
#: edits afterwards, so it answers with a default rather than refusing.
_FALLBACK_SIZE = (300, 250)

#: Each canonical ``format_kind`` GAM has a creative family for, in the vocabulary
#: ``implementation_config["supported_format_types"]`` is written in. ONE table rather than a
#: native set beside a type set, because the native decision IS a family: a native line item
#: takes a 1x1 slot and lets its creative template decide the rendered size, which
#: ``managers/orders.py`` renders as ``creativeSizeType: "NATIVE"``, and that is where the
#: native row is grounded. ``formats/canonical/native_in_feed.json``'s ``main_image_sizes``
#: is the native image ASSET's size, not the slot's, so it is deliberately not read.
#:
#: HAND-WRITTEN ON PURPOSE, and it cannot be otherwise: a canonical kind's GAM creative type
#: is a judgement about a vendor, stated nowhere in the pin. What IS derived is that every
#: key names a kind the pin declares and that the pin has grown no kind this table ignores
#: silently -- ``tests/unit/test_gam_placeholders_from_snapshots.py`` asserts both against
#: ``core/product-format-declaration.json``'s discriminator, so a pin bump that adds a kind
#: fails there rather than being classified by accident.
#:
#: FOUR OF THE PIN'S SIXTEEN KINDS ARE DELIBERATELY ABSENT -- ``agent_placement``,
#: ``coordinated_placements``, ``sponsored_placement`` and ``custom``. Each is a composition
#: contract or a seller-defined shape rather than a creative of a GAM type, and none of them
#: states a slot size. They contribute no placeholder and are NOT refused: GAM makes no claim
#: that it cannot place them, so a refusal would have nothing to cite, and ``custom`` is by
#: definition whatever this seller defines it to be. The audio rows ARE here, because GAM
#: states the opposite -- it has no standalone audio line item, which is the refusal
#: ``managers/orders.py`` already raises for the deprecated arm.
_GAM_FAMILY_BY_KIND = {
    "display_tag": "display",
    "html5": "display",
    "image": "display",
    "image_carousel": "display",
    "responsive_creative": "display",
    "seller_rendered_stateful_display": "display",
    "native_in_feed": "native",
    "video_hosted": "video",
    "video_vast": "video",
    "audio_daast": "audio",
    "audio_hosted": "audio",
    "audio_vast": "audio",
}


def gam_format_family(kind: str) -> str | None:
    """The GAM creative family *kind* traffics as, or ``None`` when this seller has none.

    The one reader of :data:`_GAM_FAMILY_BY_KIND`, so that the adapter's
    ``supported_format_types`` gate and this module's slot derivation classify a kind the
    same way. ``None`` is a real answer and not an error: see the table's own note on the
    four kinds it does not classify.
    """
    return _GAM_FAMILY_BY_KIND.get(kind)


def _placeholder(width: int, height: int, *, is_native: bool = False) -> dict[str, Any]:
    """One GAM creative placeholder: a slot size, and how many creatives GAM waits for.

    ``int()`` because the pinned params generate scalar wrappers over ``int``
    (``adcp.types._scalar.ScalarInt``); the value compares and serializes as an ``int``
    either way, and naming the type here keeps the seeded document plain.
    """
    return {"width": int(width), "height": int(height), "expected_creative_count": 1, "is_native": is_native}


def declared_gam_slots(declarations: Iterable[Any]) -> list[dict[str, Any]]:
    """The distinct GAM slots *declarations* state, each read by ATTRIBUTE.

    The CANONICAL arm, read as a first-class input. ``format_kind`` is the identity every
    layer of this seller shares, and ``core/product-format-declaration.json`` (AdCP)
    makes ``params`` the per-kind canonical schema -- so a slot size is stated there, in the
    arm the pin calls "the authoring path", and not only on the deprecated one.

    TWO CALLERS, TWO DIFFERENT PINNED CLASSES, and that is why this takes ``Iterable[Any]``
    and reads ``.format_kind`` / ``.params`` rather than validating. The product seeder
    hands it ``ProductFormatDeclaration`` arms (via :func:`product_declarations`, which
    normalizes an ORM row's documents); the GAM order manager hands it
    ``PackageFormatSnapshot`` arms off a package's recorded ``formats_to_provide``
    (``salesagent-ol033.8``). Those are SIBLING generated unions over the same discriminator
    -- a snapshot is ``allOf`` over the declaration schema plus package bindings -- so
    re-validating one into the other is the class confusion that answered INTERNAL_ERROR for
    every product of a tenant once (``salesagent-1d5z4``) and silently killed an approval
    with no adapter call at all. Attribute reading is the adoption CLAUDE.md names for
    exactly this, and both classes answer both attributes.

    THE DIMENSION SOURCES, in the order the canonical schemas state them: ``sizes[]`` (a
    declaration accepting several fixed sizes is several GAM slots), then fixed
    ``width``/``height``. A declaration stating only a RANGE -- ``min_width``/``max_width``
    on ``image``, ``video_hosted`` and ``video_vast`` -- yields NO slot. A range is not a
    slot size, and picking an endpoint would be the same invention as the 640x480 constant
    this replaced; each caller decides what no slot means for it (the seeder defaults, the
    adapter falls through to the deprecated arm).

    DEDUPLICATED, because two declarations reaching one size are one slot: see
    :func:`_one_per_slot`.
    """
    slots: list[dict[str, Any]] = []
    for declaration in declarations:
        kind = str(declaration.format_kind)
        if gam_format_family(kind) == "native":
            slots.append(_placeholder(1, 1, is_native=True))
            continue

        params = declaration.params
        sizes = getattr(params, "sizes", None)
        if sizes:
            slots.extend(_placeholder(size.width, size.height) for size in sizes)
            continue

        width, height = getattr(params, "width", None), getattr(params, "height", None)
        if width and height:
            slots.append(_placeholder(width, height))
            continue

        logger.warning(
            "Format declaration of kind %r states no fixed slot size, so it contributes no GAM creative placeholder.",
            kind,
        )
    return _one_per_slot(slots)


def _legacy_placeholders(formats: Any) -> list[dict[str, Any]]:
    """The GAM slots a product's deprecated ``format_ids[]`` imply.

    ONE call answers both halves of the question. ``upgrade_legacy_format_id`` (through
    ``format_resolver.upgraded_reference``, which canonically addresses the entry) keeps the
    width and height a stored entry CARRIES (the format picker emits
    ``{agent_url, id: "display_image", width, height}`` for a parameterized selection, and
    ``_parse_format_entries`` stores it) and derives them from the id when the entry states
    none (``display_728x90_image`` -> 728x90). It accepts the entry as a document, a model
    or a bare string, so the two storage patterns need no branch here.

    THE EIGHT-MEMBER DIMENSION MAP THAT STOOD HERE IS GONE, and not only because nothing
    reached it: measured against ``tests/fixtures/creative_formats/reference_formats.json``,
    its keys (``display_300x250``, ``display_320x100``, ``display_300x50``) match no id in
    the catalogue, which carries ``display_300x250_image`` / ``_html`` / ``_generative`` and
    a ``display_336x280`` the map lacked. The dimensions are in the data.

    An entry the SDK cannot read is DROPPED with a warning rather than raising, the same
    choice ``format_resolver.format_identity_or_none`` makes about the same arm and for the
    same reason: this runs inside ``_create_media_buy_impl``'s auto-generation, where one
    unusable row must not refuse the buy.
    """
    placeholders: list[dict[str, Any]] = []
    for entry in formats or []:
        try:
            # Addressed first (``format_resolver.upgraded_reference``): the SDK upgrades a
            # size-encoded id only for the reference agent and recognizes it by a partial
            # URL canonicalization, so a stored respelling (a trailing root-label dot, a dot
            # segment, the agent at another path) produced no placeholder.
            upgraded = upgraded_reference(entry)
        except (ValidationError, ValueError, TypeError, AttributeError):
            logger.warning("Unusable stored format reference, ignored: %r", entry)
            continue

        if upgraded.width and upgraded.height:
            placeholders.append(_placeholder(upgraded.width, upgraded.height))
        else:
            logger.warning(
                "Format reference %r states no dimensions and none derive from its id, so it "
                "contributes no GAM creative placeholder.",
                upgraded.id,
            )
    return placeholders


def _one_per_slot(placeholders: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """*placeholders* with each distinct slot kept once, in declared order.

    Two declarations reaching the same size is ordinary -- ``display_728x90_image`` and a
    picker-parameterized ``display_image`` at 728x90 are one slot -- and emitting it twice
    tells GAM to wait for two creatives there, since ``expected_creative_count`` is 1 per
    placeholder. That is the "X out of Y expected" state ``managers/orders.py`` already
    filters for on the media-buy side.
    """
    by_slot: dict[tuple[int, int, bool], dict[str, Any]] = {}
    for placeholder in placeholders:
        by_slot.setdefault(
            (placeholder["width"], placeholder["height"], placeholder["is_native"]),
            placeholder,
        )
    return list(by_slot.values())


def product_gam_slots(formats: Any, format_options: Any) -> list[dict[str, Any]]:
    """The distinct GAM slots a product's two format arms STATE, and nothing invented.

    THE CANONICAL ARM ANSWERS WHEN IT STATES A SIZE. ``core/product-format-declaration.json``
    makes ``format_options[]`` "the closed set of accepted formats for this product" and says
    legacy named formats "MUST be normalized to canonical declarations before comparison", so
    the two arms are not unioned -- a product declaring one shape on both arms would
    otherwise get that slot counted twice.

    The deprecated arm is consulted when the canonical one yields nothing, which covers a
    legacy-only product AND a canonical declaration stating only a range. Falling through
    rather than stopping is the deliberate choice: a derived size an operator can correct is
    more use than none, and the one product this reads oddly for -- a canonical video range
    beside a legacy display size -- is mis-authored either way.

    EMPTY IS A REAL ANSWER and the caller decides what it means, which is why the seeding
    default lives in :func:`_creative_placeholders` and not here. A product whose
    declarations state no slot genuinely states none; the config seeder answers with a
    correctable default because ``validate_config`` refuses a config with no placeholder,
    while ``gam_inventory_service``'s ad-unit scorer must score on NO size signal rather than
    on an invented 300x250 that would match real inventory.
    """
    return declared_gam_slots(product_declarations(format_options)) or _one_per_slot(_legacy_placeholders(formats))


def _creative_placeholders(formats: Any, format_options: Any) -> list[dict[str, Any]]:
    """The GAM creative placeholders to SEED a product's implementation_config with."""
    placeholders = product_gam_slots(formats, format_options)
    if placeholders:
        return placeholders

    logger.warning(
        "No format declaration this product carries states a GAM slot size; seeding the "
        "%dx%d default, which an operator must correct if the product's creatives are a "
        "different size.",
        *_FALLBACK_SIZE,
    )
    return [_placeholder(*_FALLBACK_SIZE)]


class GAMProductConfigService:
    """Handles GAM-specific product configuration management."""

    @staticmethod
    def generate_default_config(
        delivery_type: str,
        formats: Sequence[FormatIdInput] | None = None,
        *,
        format_options: Any = None,
    ) -> dict[str, Any]:
        """Generate default GAM implementation config based on product delivery type.

        Args:
            delivery_type: "guaranteed" or "non_guaranteed"
            formats: the product's deprecated ``format_ids`` arm, as the stored entries
                (``{agent_url, id, width?, height?, duration_ms?}``), as the validated
                references a converted product carries, or as bare id strings. Pass the
                references whenever there are any: a bare id drops the width and height an
                entry stores, and an id like ``display_image`` encodes none to derive.
            format_options: the product's canonical ``format_options`` arm, as the stored
                documents or as the pinned union's validated arms. ``Any`` for the reason
                ``format_satisfaction.product_declarations`` states: this is reached from an
                ORM row and from a converted product, and that function is what reconciles
                the two shapes.

        Returns:
            Dictionary of GAM implementation config with sensible defaults
        """
        # Base configuration shared across all types
        base_config = {
            "cost_type": "CPM",
            "creative_rotation_type": "EVEN",
            "primary_goal_unit_type": "IMPRESSIONS",
            "include_descendants": True,
        }

        # Delivery-type-specific defaults
        if delivery_type == "guaranteed":
            base_config.update(
                {
                    "line_item_type": "STANDARD",
                    "priority": 6,
                    "primary_goal_type": "DAILY",
                    "delivery_rate_type": "EVENLY",
                    "non_guaranteed_automation": "manual",  # Guaranteed always manual
                }
            )
        else:  # non_guaranteed
            base_config.update(
                {
                    "line_item_type": "PRICE_PRIORITY",
                    "priority": 10,
                    "primary_goal_type": "NONE",
                    "delivery_rate_type": "AS_FAST_AS_POSSIBLE",
                    "creative_rotation_type": "OPTIMIZED",
                    "non_guaranteed_automation": "confirmation_required",  # Safe default
                }
            )

        base_config["creative_placeholders"] = _creative_placeholders(formats, format_options)

        return base_config

    @staticmethod
    def validate_config(config: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate GAM implementation config for required fields.

        Args:
            config: GAM implementation configuration dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not config:
            return False, "implementation_config is required for GAM products"

        # Required fields
        required_fields = ["priority", "creative_placeholders"]

        for field in required_fields:
            if field not in config:
                return False, f"Missing required GAM field: {field}"

        # Validate priority range
        priority = config.get("priority")
        if not isinstance(priority, int) or priority < 1 or priority > 16:
            return False, "priority must be an integer between 1 and 16"

        # Validate line item type (optional - if not provided, will be auto-selected based on pricing)
        if "line_item_type" in config:
            valid_line_item_types = ["STANDARD", "SPONSORSHIP", "NETWORK", "BULK", "PRICE_PRIORITY", "HOUSE"]
            if config.get("line_item_type") not in valid_line_item_types:
                return False, f"line_item_type must be one of: {', '.join(valid_line_item_types)}"

        # Validate creative placeholders
        placeholders = config.get("creative_placeholders", [])
        if not placeholders:
            return False, "At least one creative placeholder is required"

        for placeholder in placeholders:
            if "width" not in placeholder or "height" not in placeholder:
                return False, "Creative placeholders must have width and height"

        # Warn if no inventory targeting specified (will use network root as fallback)
        has_inventory_targeting = config.get("targeted_ad_unit_ids") or config.get("targeted_placement_ids")
        if not has_inventory_targeting:
            logger.warning(
                "No inventory targeting specified (targeted_ad_unit_ids or targeted_placement_ids). "
                "Will use network root ad unit as fallback."
            )

        return True, None

    @staticmethod
    def parse_form_config(form_data) -> dict[str, Any]:
        """Parse GAM configuration form data into implementation_config format.

        Args:
            form_data: Flask request.form object (ImmutableMultiDict)

        Returns:
            Parsed implementation_config dictionary
        """
        config = {}

        # Basic line item settings
        config["line_item_type"] = form_data.get("line_item_type", "STANDARD")
        priority = form_data.get("priority", "8")
        config["priority"] = int(priority) if priority else 8
        config["cost_type"] = form_data.get("cost_type", "CPM")

        # Delivery settings
        config["creative_rotation_type"] = form_data.get("creative_rotation_type", "EVEN")
        config["delivery_rate_type"] = form_data.get("delivery_rate_type", "EVENLY")
        config["primary_goal_type"] = form_data.get("primary_goal_type", "DAILY")
        config["primary_goal_unit_type"] = form_data.get("primary_goal_unit_type", "IMPRESSIONS")

        # Automation settings
        config["non_guaranteed_automation"] = form_data.get("non_guaranteed_automation", "confirmation_required")

        # Inventory targeting
        ad_unit_ids_text = form_data.get("targeted_ad_unit_ids", "").strip()
        if ad_unit_ids_text:
            config["targeted_ad_unit_ids"] = [id.strip() for id in ad_unit_ids_text.split("\n") if id.strip()]

        placement_ids_text = form_data.get("targeted_placement_ids", "").strip()
        if placement_ids_text:
            config["targeted_placement_ids"] = [id.strip() for id in placement_ids_text.split("\n") if id.strip()]

        config["include_descendants"] = "include_descendants" in form_data

        # Creative placeholders (arrays)
        widths = form_data.getlist("placeholder_width[]")
        heights = form_data.getlist("placeholder_height[]")
        counts = form_data.getlist("placeholder_count[]")
        is_natives = form_data.getlist("placeholder_is_native[]")

        placeholders = []
        for i in range(len(widths)):
            placeholders.append(
                {
                    "width": int(widths[i]),
                    "height": int(heights[i]),
                    "expected_creative_count": int(counts[i]) if i < len(counts) else 1,
                    "is_native": str(i) in is_natives,  # Checkboxes send index as value
                }
            )
        config["creative_placeholders"] = placeholders

        # Frequency caps (arrays)
        cap_max_imps = form_data.getlist("cap_max_impressions[]")
        cap_time_units = form_data.getlist("cap_time_unit[]")
        cap_time_ranges = form_data.getlist("cap_time_range[]")

        if cap_max_imps:
            frequency_caps = []
            for i in range(len(cap_max_imps)):
                frequency_caps.append(
                    {
                        "max_impressions": int(cap_max_imps[i]),
                        "time_unit": cap_time_units[i] if i < len(cap_time_units) else "DAY",
                        "time_range": int(cap_time_ranges[i]) if i < len(cap_time_ranges) else 1,
                    }
                )
            config["frequency_caps"] = frequency_caps

        # Competition & exclusions
        comp_labels = form_data.get("competitive_exclusion_labels", "").strip()
        if comp_labels:
            config["competitive_exclusion_labels"] = [
                label.strip() for label in comp_labels.split(",") if label.strip()
            ]

        # Custom targeting keys (JSON)
        custom_targeting = form_data.get("custom_targeting_keys", "{}").strip()
        if custom_targeting and custom_targeting != "{}":
            try:
                config["custom_targeting_keys"] = json.loads(custom_targeting)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in custom_targeting_keys, skipping")

        # Advanced settings
        config["environment_type"] = form_data.get("environment_type", "BROWSER")

        discount_type = form_data.get("discount_type")
        if discount_type:
            config["discount_type"] = discount_type
            discount_value = form_data.get("discount_value")
            if discount_value:
                config["discount_value"] = float(discount_value)

        config["allow_overbook"] = "allow_overbook" in form_data
        config["skip_inventory_check"] = "skip_inventory_check" in form_data
        config["disable_viewability_avg_revenue_optimization"] = (
            "disable_viewability_avg_revenue_optimization" in form_data
        )

        # Video settings
        if config["environment_type"] == "VIDEO_PLAYER":
            companion = form_data.get("companion_delivery_option")
            if companion:
                config["companion_delivery_option"] = companion

            video_duration = form_data.get("video_max_duration")
            if video_duration:
                config["video_max_duration"] = int(video_duration) * 1000  # Convert seconds to milliseconds

            skip_offset = form_data.get("skip_offset")
            if skip_offset:
                config["skip_offset"] = int(skip_offset) * 1000  # Convert seconds to milliseconds

        # Native settings
        native_style = form_data.get("native_style_id")
        if native_style:
            config["native_style_id"] = native_style

        # Team IDs
        team_ids = form_data.get("applied_team_ids", "").strip()
        if team_ids:
            config["applied_team_ids"] = [id.strip() for id in team_ids.split(",") if id.strip()]

        return config
